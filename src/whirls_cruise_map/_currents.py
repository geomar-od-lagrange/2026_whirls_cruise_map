"""Fetch today's CMEMS surface currents and render them for the map.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N, widened by
10 deg on every side and expressed in -180..180 for the web map.

Shading frames are named by their **absolute valid time** (``speed_2026-07-01T00Z.webp``,
``vorticity_...``, ``flowvis_...webp``) and span every ``FRAME_STEP_H`` (12 h) step
from :data:`FIELD_TMIN` (floored to 00Z) through the 6-hourly product's forecast edge
— a growing set (~50 frames today, ~2/day). Each slow run fetches only the span of
frames that still need (re)rendering (see :func:`plan_render`): old frames already on
disk and safely behind now are immutable and skipped forever, so the fetch and render
stay incremental as the history grows. From the fetched window we derive:

- ``to_flowvis_frames`` — one **static streamline** raster per frame for the flow
  overlay, so the flow scrubs with the shadings as a plain ``L.imageOverlay`` (fluent,
  no client-side particle animation). Rendered on the same Mercator warp / edge bounds
  as the speed raster (see :func:`_raster.mercator_streamlines_webp`), as lossless WebP.
- ``to_speed_frames`` — one near-native speed raster per frame (cmocean ``speed``,
  Web-Mercator warped, land transparent), as compact **lossless WebP** frames on a
  frozen colour scale (:data:`SPEED_VMAX`).

Land is kept as NaN throughout; the near-inertial *advection* field is a separate,
finer hourly window (:func:`whirls_cruise_map._field_store.load_window`),
unrelated to these overlays.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cmocean
import copernicusmarine
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

from . import _raster, _time  # noqa: E402
from ._frames import (  # noqa: E402
    N_BINS,
    _quantize_unit,
    _slice_at,
    frame_filename,
    frame_valid_time,
    parse_frame_filename,
)
from ._retry import with_retry  # noqa: E402

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}

# Cruise start (2026-06-28T00Z): the earliest day the incremental field store
# (_field_store) backfills. Env override lets the app point at another cruise
# or region later — alongside BBOX, this is that seam.
FIELD_TMIN = os.environ.get("WHIRLS_FIELD_TMIN", "2026-06-28T00:00:00Z")

# CMEMS fetches run only on the slow (6-hourly) tier, so a transient blip that
# isn't retried leaves currents/vorticity/forecast/hindcast/inertial stale for
# up to 6h. copernicusmarine exposes no timeout/retry knob, so wrap the subset
# in a few backed-off attempts. Kept small to stay inside the slow job's 1800s
# deadline even across both (field + window) fetches.
_ATTEMPTS = 3
_BACKOFF = 5  # base seconds: 5s, 10s between attempts

# Chunk the shading-window load over time so the float32 cast in fetch_shading_window
# runs block-by-block (dask reads each K-step block as float64, casts to float32, and
# releases the float64 block) — the full float64 window is never resident, which bounds
# the cold-start derive peak that OOM-killed the slow tier (#37).
_SHADING_TIME_CHUNK = 8

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"

# Time-slider shading. Frames are named by absolute valid time and step every
# FRAME_STEP_H from FIELD_TMIN (floored to 00Z) through the 6-hourly product's
# forecast edge. 12 h is a multiple of the product's 6 h grid, so every frame time
# is a real step. Frames render as lossless WebP on the frozen SPEED_VMAX / ζ·f
# VORT_CLIP scale (see to_speed_frames / _vorticity.to_vorticity_frames,
# docs/currents.md).
FRAME_STEP_H = 12

# A frame already on disk is *final* — immutable, never re-rendered — once its valid
# time is this far behind wall-clock now (the CMEMS-revises-nothing-behind-the-edge
# working assumption of plans/034 with a safety margin). Recent + forecast frames
# (valid_time + margin > now) are re-rendered every slow run; a growing frame history
# can never force the whole set back into a build. Matches _field_store.FINAL_MARGIN_H.
FRAME_FINAL_MARGIN_H = 12

# Generous upper bound for the shading fetch's end time (now + this). CMEMS clamps the
# subset to the product's actual forecast edge, so the returned window's max time *is*
# that edge; ~10 d matches the anfc forecast reach (and _field_store's fallback).
FORECAST_REACH_H = 240

# Hourly surface product for the *time-dependent* advection field (the deploy API's
# drift integration and the near-inertial decomposition).
# 6-hourly (``DATASET_ID``) resolves the inertial band here (T_f ~15-24 h
# > 12 h Nyquist), but only ~3 samples per inertial cycle, so linear-in-time
# interpolation chords the loop; hourly (~20/cycle) traces it smoothly for a
# negligible fetch cost (measured +0.8 s over 6-hourly for a +/-12 h window). The
# shading/flow overlays (the flow streamline frames, the speed/ζ/f slider frames) use the 6-hourly
# ``DATASET_ID`` window; only the advection field is this hourly one. See
# docs/currents.md.
WINDOW_DATASET_ID = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
WINDOW_BACK_H = 12  # hours of hourly field to fetch behind now (hindcast + bracket)
WINDOW_FWD_H = 12   # ... and ahead of now (forecast + bracket); +/-6 h advection

# The deployment forecast API (whirls_cruise_map._api) no longer reads a
# build-persisted window at all — it reads the incremental per-day field store
# directly (whirls_cruise_map._field_store), scoping each request's own field to
# just the span that request needs, reloaded from the store's live manifest
# rather than sized ahead of time for cron-cadence staleness. So the build's own
# hourly-window fetch below only has to cover its own consumers: the +/-6 h
# forecast/hindcast advection and the inertial decomposition's narrow slice
# (WINDOW_BACK_H + WINDOW_FWD_H = 24 h, well under an inertial period).

# Speed shading, on a **frozen** colour scale. vmax is a constant, not a per-build
# pooled percentile: re-pooling over a growing frame history would drift the scale and
# force every immutable old frame back into build memory to stay colour-consistent.
# Frozen 2026-07-13 from the pooled 99th-percentile scale of the then-current 8-frame
# window (which rendered vmax 1.18 m/s); rounded to a stable 1.2 so the legend never
# breathes across builds.
SPEED_CMAP = cmocean.cm.speed
SPEED_VMAX = 1.2  # m/s

# --- fetch -----------------------------------------------------------------

def fetch_shading_window(
    *, t_lo: datetime, t_hi: datetime, bbox: dict = BBOX
) -> xr.Dataset:
    """Download surface ``uo``/``vo`` over ``bbox`` for the 6-hourly window
    ``[t_lo, t_hi]`` and return the 3-D ``(time, latitude, longitude)`` field with the
    **time dimension preserved**, land kept as NaN.

    This covers exactly the span of shading frames that need (re)rendering this run
    (``t_lo`` = the earliest such frame from :func:`plan_render`, ``t_hi`` = a generous
    forecast reach that CMEMS clamps to the product's actual edge), which the build
    then slices per frame. ``coordinates_selection_method="outside"`` brackets the
    range so both endpoints land inside the returned steps. Relies on the local
    copernicusmarine login.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "currents.nc"
        with_retry(
            lambda: copernicusmarine.subset(
                dataset_id=DATASET_ID,
                variables=["uo", "vo"],
                minimum_longitude=bbox["lon_min"],
                maximum_longitude=bbox["lon_max"],
                minimum_latitude=bbox["lat_min"],
                maximum_latitude=bbox["lat_max"],
                minimum_depth=0.49,
                maximum_depth=0.5,
                start_datetime=t_lo,
                end_datetime=t_hi,
                coordinates_selection_method="outside",
                output_filename=str(out),
                overwrite=True,
            ),
            attempts=_ATTEMPTS,
            backoff=_BACKOFF,
            label=f"CMEMS subset of {DATASET_ID}",
        )
        with xr.open_dataset(out, chunks={"time": _SHADING_TIME_CHUNK}) as ds:
            ds = ds.astype({"uo": np.float32, "vo": np.float32}).load()

    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds


def valid_time(field: xr.Dataset) -> str:
    """ISO-8601 valid time of the field (UTC, ``Z`` suffix). For a single-time
    field; the windowed field carries a ``time`` dimension instead."""
    return np.datetime_as_string(field["time"].values, unit="s") + "Z"


# --- absolute-time frames: naming, span, planner ----------------------------

# `currents` stays here (never in _FRAME_FILE_RE) so retired `currents_*.json` flow grids
# — replaced by the pre-rendered `flowvis_*.webp` streamlines — are pruned on next build.
_STALE_FRAME_RE = re.compile(
    r"^(speed|vorticity|flowvis|currents)_"
    r"(\d{4}-\d{2}-\d{2}T\d{2}Z|[+-]\d{2,3}h)\.(webp|json)$"
)


def frame_tmin() -> datetime:
    """The first frame's valid time: :data:`FIELD_TMIN` floored to 00Z. The whole
    frame grid is anchored here, so every frame time is this plus a multiple of
    ``FRAME_STEP_H``."""
    t = _time.parse_iso(FIELD_TMIN)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def frame_span(t_lo: datetime, t_hi: datetime, step_h: int = FRAME_STEP_H) -> list[datetime]:
    """Every ``step_h`` step from ``t_lo`` through ``t_hi`` inclusive (``t_lo`` is
    assumed on the grid; empty if ``t_hi < t_lo``)."""
    out, t, step = [], t_lo, timedelta(hours=step_h)
    while t <= t_hi:
        out.append(t)
        t += step
    return out


def plan_render(
    frames: list[datetime],
    existing: set[datetime],
    now: datetime,
    margin_h: int = FRAME_FINAL_MARGIN_H,
) -> list[datetime]:
    """Which of ``frames`` still need (re)rendering: every frame *except* those already
    on disk (``existing``) **and** safely behind now (``valid_time + margin <= now``,
    hence final and immutable). Missing frames, recent frames, and forecast frames all
    render; a deleted old frame re-plans (it's no longer in ``existing``). Pure and
    network-free — the incremental-rendering decision the build's shading block runs."""
    margin = timedelta(hours=margin_h)
    return [t for t in frames if not (t in existing and t + margin <= now)]


def nearest_valid_time(frames: list[datetime], now: datetime) -> str:
    """The ``frame_valid_time`` of the frame nearest ``now`` — the manifest's top-level
    ``valid_time`` for now-only readers / the client's initial view."""
    return frame_valid_time(min(frames, key=lambda t: abs(t - now)))


def frame_manifest(kind: str, frames: list[datetime], ext: str = "webp") -> list[dict]:
    """The client manifest ``[{valid_time, file}]`` for ``frames`` — one entry per frame
    in span order, no offset."""
    return [
        {"valid_time": frame_valid_time(t), "file": frame_filename(kind, t, ext)}
        for t in frames
    ]


def existing_frame_times(map_dir: Path) -> set[datetime]:
    """Frame valid times already fully rendered under ``map_dir`` — a time counts only
    if **all three** WebP artifacts (speed + vorticity + flowvis streamlines) are
    present, so a frame half-written by a prior partial run re-plans rather than being
    treated as final."""
    times: set[datetime] | None = None
    for kind, ext in (("speed", "webp"), ("vorticity", "webp"), ("flowvis", "webp")):
        found = {
            parsed[1]
            for p in map_dir.glob(f"{kind}_*.{ext}")
            if (parsed := parse_frame_filename(p.name)) is not None
        }
        times = found if times is None else (times & found)
    return times or set()


def first_pending_frame(
    t_lo: datetime,
    existing: set[datetime],
    now: datetime,
    step_h: int = FRAME_STEP_H,
    margin_h: int = FRAME_FINAL_MARGIN_H,
) -> datetime:
    """The earliest frame that still needs rendering — the shading fetch's lower bound,
    computable *before* the fetch (it doesn't depend on the forecast edge): the min of
    :func:`plan_render` is either an old hole or the first recent frame, both at or
    before now. Probing the grid up to ``now`` is enough to find it (forecast frames all
    render, so they never lower the min)."""
    probe = frame_span(t_lo, now + timedelta(hours=step_h), step_h)
    pending = plan_render(probe, existing, now, margin_h)
    return pending[0] if pending else t_lo


def window_frame_edge(window: xr.Dataset, t_lo: datetime, step_h: int = FRAME_STEP_H) -> datetime:
    """The last frame time on the grid at or before the fetched window's max time — the
    frame span's upper bound (the 6-hourly product's forecast edge, snapped to the
    ``step_h`` frame grid)."""
    wmax = window["time"].values.max()
    wmax_dt = datetime.fromtimestamp(
        wmax.astype("datetime64[s]").astype("int64"), tz=timezone.utc
    )
    steps = int((wmax_dt - t_lo).total_seconds() // (step_h * 3600))
    return t_lo + timedelta(hours=step_h * steps)


def prune_stale_frames(map_dir: Path, frames: list[datetime]) -> list[str]:
    """Delete every frame-named file under ``map_dir`` not referenced by the current
    span ``frames`` — retired offset-named artifacts (``speed_+12h.webp`` &c.) and
    absolute frames no longer in the span — so stale files never linger. Meta files and
    non-frame files don't match :data:`_STALE_FRAME_RE`, so they're untouched. Returns
    the removed names."""
    keep = set()
    for t in frames:
        keep.add(frame_filename("speed", t, "webp"))
        keep.add(frame_filename("vorticity", t, "webp"))
        keep.add(frame_filename("flowvis", t, "webp"))
    removed = []
    for p in sorted(map_dir.iterdir()):
        if _STALE_FRAME_RE.match(p.name) and p.name not in keep:
            p.unlink()
            removed.append(p.name)
    return removed


# --- flow streamlines (pre-rendered static WebP) ---------------------------

def to_flowvis_frames(window: xr.Dataset, frame_times: list[datetime]) -> list[dict]:
    """One **static streamline** WebP per requested frame time, so the flow overlay
    scrubs with the speed / ζ·f shadings as a plain ``L.imageOverlay`` — fluent, with no
    client-side particle animation (this replaces the leaflet-velocity flow trails).

    Slices the same fetched window as :func:`to_speed_frames` for each of
    ``frame_times`` (no extra fetch; a frame time lands on the 6-hourly grid exactly),
    rendering each with :func:`_raster.mercator_streamlines_webp` on the same Mercator
    warp and edge bounds as the speed raster, so the client places it with the shared
    ``meta.bounds``. Returns ``[{valid_time, file, image}]`` with ``image`` the WebP bytes
    and ``file`` e.g. ``flowvis_2026-07-01T00Z.webp``; the build writes each ``image`` and
    assembles the ``flow_frames`` manifest for ``currents_meta.json`` from the full span
    via :func:`frame_manifest`."""
    frames = []
    for t in frame_times:
        f = _slice_at(window, t).sortby("latitude").sortby("longitude")
        image, _ = _raster.mercator_streamlines_webp(
            f["uo"].values, f["vo"].values, f["latitude"].values, f["longitude"].values
        )
        frames.append(
            {
                "valid_time": frame_valid_time(t),
                "file": frame_filename("flowvis", t),
                "image": image,
            }
        )
    return frames


# --- speed shading (Mercator-warped PNG) -----------------------------------

def _colorbar_stops(n: int = N_BINS) -> list[str]:
    """The ``n`` discrete bin colours the speed raster actually uses — hex, low ->
    high (the ``(i+0.5)/n`` midpoints :func:`_quantize_unit` snaps to). The client
    legend renders them as hard-edged classes, so it shows exactly the raster's
    classes rather than a smooth ramp over sampled stops."""
    return [mcolors.to_hex(SPEED_CMAP((i + 0.5) / n)) for i in range(n)]


def to_speed_frames(window: xr.Dataset, frame_times: list[datetime]) -> tuple[list[dict], dict]:
    """Render |velocity| for each requested frame time as a compact **lossless WebP**
    (cmocean ``speed``, land transparent) and return ``(frames, meta)``.

    All frames share the **frozen** :data:`SPEED_VMAX` colour scale, so a colour means
    the same speed at every time and every immutable old frame stays colour-consistent
    without re-pooling a growing history. Each frame is ``{valid_time, file, image}``;
    ``meta`` carries the shared ``bounds/vmax/units/colorbar`` for the build to write
    into ``currents_meta.json`` alongside the full-span ``frames``/``flow_frames``
    manifests and top-level ``valid_time`` it assembles itself."""
    def to_rgba(warped):
        # Quantize to N_BINS flat classes before the lookup (see N_BINS): far fewer
        # unique colours, so lossless WebP squeezes the constant-value regions.
        rgba = SPEED_CMAP(_quantize_unit(np.clip(warped / SPEED_VMAX, 0.0, 1.0)))
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    frames, bounds = [], None
    for t in frame_times:
        f = _slice_at(window, t).sortby("latitude").sortby("longitude")
        speed = np.hypot(f["uo"].values, f["vo"].values)
        image, bounds = _raster.mercator_rgba_webp(
            speed, f["latitude"].values, f["longitude"].values, to_rgba
        )
        frames.append(
            {
                "valid_time": frame_valid_time(t),
                "file": frame_filename("speed", t),
                "image": image,
            }
        )
    meta = {
        "bounds": bounds,
        "vmax": SPEED_VMAX,
        "units": "m/s",
        "colorbar": _colorbar_stops(),
    }
    return frames, meta


# Static land/sea basemap colours (#29): opaque gray land, flat blue sea. The sea tone
# matches the CSS #map fallback (#dfe7ee) so there is no flash before the WebP loads;
# the land gray reads under both shadings and under "None".
LANDMASK_LAND_RGB = (0.788, 0.788, 0.769)  # ~#c9c9c4
LANDMASK_SEA_RGB = (0.875, 0.906, 0.933)   # ~#dfe7ee


def to_landmask_webp(window: xr.Dataset) -> tuple[bytes, dict]:
    """Bake a static gray-land / blue-sea mask (#29) from the field's own land pattern.

    Land is ``NaN`` in the CMEMS field. The land geometry is time-invariant — every
    slice comes off the same fixed grid, which has no tidal-flat / intertidal cells — so
    the mask is baked from a **single** representative time slice rather than reducing
    over the whole window. The mask goes through the same Mercator warp as the shadings,
    so it co-registers with their ``bounds`` exactly. Returns ``(webp_bytes, bounds)``
    (the frontend reuses the shading ``meta.bounds``)."""
    f = window.isel(time=0, drop=True) if "time" in window.dims else window
    f = f.sortby("latitude").sortby("longitude")
    land = np.isnan(f["uo"].values)
    # NaN on land, finite on sea — so the shared warp/mask path treats land as NaN
    # exactly like the shadings do.
    field = np.where(land, np.nan, 0.0)

    def to_rgba(warped):
        m = np.isnan(warped)
        rgba = np.empty(warped.shape + (4,), dtype=float)
        for c in range(3):
            rgba[..., c] = np.where(m, LANDMASK_LAND_RGB[c], LANDMASK_SEA_RGB[c])
        rgba[..., 3] = 1.0
        return rgba

    return _raster.mercator_rgba_webp(field, f["latitude"].values, f["longitude"].values, to_rgba)
