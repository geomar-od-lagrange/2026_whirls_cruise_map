"""Fetch today's CMEMS surface currents and render them for the map.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N, widened by
10 deg on every side and expressed in -180..180 for the web map.

The CMEMS field is fetched over a **forecast window** (6-hourly ``PT6H-i``, the
times nearest ``[now-12h, now+72h]``) so the map can scrub it on a time slider at
12 h steps (``-12 … now … +72 h``). From that window we derive:

- ``to_velocity_frames`` — one coarsened leaflet-velocity ``[u, v]`` grid per slider
  frame for the animated flow trails, so the trails scrub with the shadings instead
  of staying pinned to the now slice. Each grid's magnitude is compressed sub-linearly
  (see ``VELOCITY_GAMMA``) so the slow eddies animate visibly while the Agulhas jet
  does not run away; direction is preserved. (``to_velocity_json`` renders one such
  grid from a single 2-D slice.)
- ``to_speed_frames`` — one near-native speed raster per slider frame (cmocean
  ``speed``, Web-Mercator warped, land transparent), as compact **lossless WebP**
  frames sharing one colour scale, plus a small ``frames`` manifest for the client.

Land is kept as NaN throughout; the near-inertial *advection* field is a separate,
finer hourly window (:func:`fetch_field_window`), unrelated to these overlays.
"""
from __future__ import annotations

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

from . import _raster  # noqa: E402
from ._retry import with_retry  # noqa: E402

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}

# CMEMS fetches run only on the slow (6-hourly) tier, so a transient blip that
# isn't retried leaves currents/vorticity/forecast/hindcast/inertial stale for
# up to 6h. copernicusmarine exposes no timeout/retry knob, so wrap the subset
# in a few backed-off attempts. Kept small to stay inside the slow job's 1800s
# deadline even across both (field + window) fetches.
_ATTEMPTS = 3
_BACKOFF = 5  # base seconds: 5s, 10s between attempts

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"

# Time-slider shading. The speed/vorticity rasters ship one frame every
# SHADING_STEP_H over [-SHADING_BACK_H, +SHADING_FWD_H]: -12, now, +12 … +72 h.
# All frames come from one 6-hourly ``DATASET_ID`` fetch over that range (12 h is a
# multiple of the 6 h grid, so every target time is a real step), so the whole
# shading set shares one clock and the *now* frame is identical to the previous
# single-time speed raster. The frames render as lossless WebP on one shared colour
# scale (see to_speed_frames / _vorticity.to_vorticity_frames, docs/currents.md).
SHADING_STEP_H = 12
SHADING_BACK_H = 12
SHADING_FWD_H = 72
SHADING_OFFSETS_H = list(range(-SHADING_BACK_H, SHADING_FWD_H + 1, SHADING_STEP_H))

# Hourly surface product for the *time-dependent* forecast/hindcast advection
# field. 6-hourly (``DATASET_ID``) resolves the inertial band here (T_f ~15-24 h
# > 12 h Nyquist), but only ~3 samples per inertial cycle, so linear-in-time
# interpolation chords the loop; hourly (~20/cycle) traces it smoothly for a
# negligible fetch cost (measured +0.8 s over 6-hourly for a +/-12 h window). The
# shading/flow overlays (the flow-trail grids, the speed/ζ/f slider frames) use the 6-hourly
# ``DATASET_ID`` window; only the advection field is this hourly one. See
# docs/forecast.md, docs/currents.md.
WINDOW_DATASET_ID = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
WINDOW_BACK_H = 12  # hours of hourly field to fetch behind now (hindcast + bracket)
WINDOW_FWD_H = 12   # ... and ahead of now (forecast + bracket); +/-6 h advection

# Forecast-API window reach. The slow cron persists one window to the PVC that the
# forecast API (whirls_cruise_map._api) serves from; a served window may be up to
# one slow-cron cadence stale, so its forward reach must cover a full run started
# at "now" even at that age: fwd >= FORECAST_HORIZON_H + SLOW_CADENCE_H. Deriving
# the reach from those two drivers (not a bare 60) keeps a horizon bump from
# silently outrunning the window. Back-reach covers the displayed-field lag, same
# as the inertial/advection window above. FORECAST_HORIZON_H is the single source
# for the API's default run length (_api._DEFAULT_HORIZON_H reads it).
FORECAST_HORIZON_H = 48  # forecast-API default run length (hours)
SLOW_CADENCE_H = 12      # slow-cron period; a served window may be this stale
FORECAST_WINDOW_BACK_H = WINDOW_BACK_H                       # 12 h back
FORECAST_WINDOW_FWD_H = FORECAST_HORIZON_H + SLOW_CADENCE_H  # 60 h forward

# Coarsen the native 1/12-deg grid for the animated trails only. The speed raster
# stays near-native (it is a small image either way and looks markedly sharper).
COARSEN_STRIDE = 3

# Sub-linear compression of the trail-animation velocity magnitude (gamma < 1).
# gamma=0.5 (sqrt) lifts the slow eddies relative to the fast jet (~10x -> ~3x).
VELOCITY_GAMMA = 0.5

# Speed shading.
SPEED_CMAP = cmocean.cm.speed
SPEED_CLIP_PERCENTILE = 99
COLORBAR_STOPS = 16


# --- fetch -----------------------------------------------------------------

def fetch_shading_window(
    bbox: dict = BBOX, back_h: int = SHADING_BACK_H, fwd_h: int = SHADING_FWD_H
) -> xr.Dataset:
    """Download surface ``uo``/``vo`` over ``bbox`` for the 6-hourly window
    ``[now-back_h, now+fwd_h]`` and return the 3-D ``(time, latitude, longitude)``
    field with the **time dimension preserved**, land kept as NaN.

    This is the source for both the animated flow trails (from the *now* slice) and
    the time-slider speed/vorticity rasters (one frame every ``SHADING_STEP_H``).
    ``coordinates_selection_method="outside"`` brackets the range so the ``-back_h``
    and ``+fwd_h`` targets are inside the returned steps. Relies on the local
    copernicusmarine login.
    """
    now = datetime.now(timezone.utc)
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
                start_datetime=now - timedelta(hours=back_h),
                end_datetime=now + timedelta(hours=fwd_h),
                coordinates_selection_method="outside",
                output_filename=str(out),
                overwrite=True,
            ),
            attempts=_ATTEMPTS,
            backoff=_BACKOFF,
            label=f"CMEMS subset of {DATASET_ID}",
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()

    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds


def _nearest_now_time(window: xr.Dataset) -> np.datetime64:
    """The window step nearest wall-clock now — the slider's ``0 h`` anchor."""
    now = np.datetime64(datetime.now(timezone.utc).replace(tzinfo=None), "ns")
    return window["time"].sel(time=now, method="nearest").values


def _now_valid_time(frames: list[dict]) -> str:
    """The now (offset 0) frame's ``valid_time`` for the meta's top-level key,
    falling back to the first frame if a custom offset list omits 0."""
    return next((f["valid_time"] for f in frames if f["offset_h"] == 0), frames[0]["valid_time"])


def frame_filename(kind: str, offset_h: int, ext: str = "webp") -> str:
    """Slider-frame artifact name, e.g. ``speed_+12h.webp`` / ``vorticity_-12h.webp``
    (lossless WebP shadings — half the bytes of PNG at the same pixels) or
    ``currents_+00h.json`` (the flow-trail grid, ``ext="json"``). Single source of
    truth shared by the renderers' metas and the build."""
    return f"{kind}_{offset_h:+03d}h.{ext}"


def select_frames(
    window: xr.Dataset, offsets: list[int] = SHADING_OFFSETS_H
) -> list[tuple[int, xr.Dataset]]:
    """``(offset_h, 2-D slice)`` for each slider offset, the slice nearest
    ``now + offset_h`` (12 h steps land on the 6-hourly grid exactly). A target past
    the fetched window's edge clamps to the last step rather than erroring."""
    t0 = _nearest_now_time(window)
    frames = []
    for off in offsets:
        target = t0 + np.timedelta64(off, "h")
        frames.append((off, window.sel(time=target, method="nearest")))
    return frames


def fetch_field_window(
    bbox: dict = BBOX, back_h: int = WINDOW_BACK_H, fwd_h: int = WINDOW_FWD_H
) -> xr.Dataset:
    """Download hourly surface ``uo``/``vo`` over ``bbox`` for the window
    ``[now-back_h, now+fwd_h]`` and return the 3-D ``(time, latitude, longitude)``
    field with the **time dimension preserved**, land kept as NaN.

    This feeds the *time-dependent* forecast/hindcast advection: the particle is
    pushed by the current at its own clock time, so it traces the inertial loop the
    model already carries instead of the straight streamline of a single frozen
    snapshot. ``coordinates_selection_method="outside"`` makes the returned steps
    *bracket* the window so the stepper always interpolates between two real times
    at the edges. Relies on the local copernicusmarine login.
    """
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "currents_window.nc"
        with_retry(
            lambda: copernicusmarine.subset(
                dataset_id=WINDOW_DATASET_ID,
                variables=["uo", "vo"],
                minimum_longitude=bbox["lon_min"],
                maximum_longitude=bbox["lon_max"],
                minimum_latitude=bbox["lat_min"],
                maximum_latitude=bbox["lat_max"],
                minimum_depth=0.49,
                maximum_depth=0.5,
                start_datetime=now - timedelta(hours=back_h),
                end_datetime=now + timedelta(hours=fwd_h),
                coordinates_selection_method="outside",
                output_filename=str(out),
                overwrite=True,
            ),
            attempts=_ATTEMPTS,
            backoff=_BACKOFF,
            label=f"CMEMS subset of {WINDOW_DATASET_ID}",
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()

    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds


def valid_time(field: xr.Dataset) -> str:
    """ISO-8601 valid time of the field (UTC, ``Z`` suffix). For a single-time
    field; the windowed field carries a ``time`` dimension instead."""
    return np.datetime_as_string(field["time"].values, unit="s") + "Z"


# --- flow trails (leaflet-velocity grid) -----------------------------------

def _component_header(field: xr.DataArray, number: int, name: str) -> dict:
    """leaflet-velocity header for one component; ``field`` must be ordered
    latitude-descending, longitude-ascending."""
    lats = field["latitude"].values
    lons = field["longitude"].values
    return {
        "parameterUnit": "m.s-1",
        "parameterCategory": 2,
        "parameterNumber": number,
        "parameterNumberName": name,
        "nx": int(lons.size),
        "ny": int(lats.size),
        "lo1": float(lons.min()),
        "lo2": float(lons.max()),
        "la1": float(lats.max()),
        "la2": float(lats.min()),
        "dx": float(abs(lons[1] - lons[0])),
        "dy": float(abs(lats[1] - lats[0])),
        "refTime": valid_time(field),
        "forecastTime": 0,
    }


def _component(field: xr.DataArray, number: int, name: str) -> dict:
    """Turn a 2-D field into a leaflet-velocity object: data is row-major from the
    north-west corner (latitude descending, longitude ascending); land NaN -> 0.

    leaflet-velocity needs a hole-free regular grid and has no land mask, so land
    is filled with zero velocity rather than left absent. The client then
    bilinearly interpolates across the ocean->0 boundary, so coastal ocean
    velocities bleed onto the adjacent land and animated particles drift ashore.
    The coarsening in :func:`to_velocity_json` widens the bleed (the decimated
    coastline is offset by up to a coarse cell). Unlike the speed shading, which
    masks land with a transparent palette index (:func:`to_speed_frames`), the
    trails have no such mask. Accepted as a known cosmetic limitation; see
    plans/BACKLOG.md.
    """
    field = field.sortby("latitude", ascending=False).sortby(
        "longitude", ascending=True
    )
    # Round to 4 dp: the raw solve yields 17-significant-digit floats, ~3x the bytes
    # for sub-mm/s precision the decorative, gamma-scaled trails never resolve. At 4 dp
    # a frame is ~0.45 MB (vs ~1 MB), so the 8-frame slider stays affordable.
    data = np.round(np.nan_to_num(field.values, nan=0.0).astype(float), 4)
    return {
        "header": _component_header(field, number, name),
        "data": data.ravel(order="C").tolist(),
    }


def _scale_for_animation(coarse: xr.Dataset) -> xr.Dataset:
    """Compress the velocity magnitude sub-linearly, keeping direction, so the
    slow eddies animate. ``m' = vref * (m/vref)**gamma`` => factor ``(m/vref)**
    (gamma-1)``; ``vref`` is the field's 99th-percentile speed (its fixed point)."""
    spd = np.hypot(coarse["uo"], coarse["vo"])
    ocean = np.where(spd.values > 0, spd.values, np.nan)
    vref = float(np.nanpercentile(ocean, 99))
    factor = xr.where(spd > 0, (spd / vref) ** (VELOCITY_GAMMA - 1.0), 0.0)
    return coarse.assign(uo=coarse["uo"] * factor, vo=coarse["vo"] * factor)


def to_velocity_json(field: xr.Dataset, stride: int = COARSEN_STRIDE) -> list[dict]:
    """Coarsened, magnitude-compressed leaflet-velocity ``[u, v]`` for the trails,
    from a single 2-D ``(latitude, longitude)`` slice."""
    coarse = field.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )
    coarse = _scale_for_animation(coarse)
    return [
        _component(coarse["uo"], number=2, name="Eastward current"),
        _component(coarse["vo"], number=3, name="Northward current"),
    ]


def to_velocity_frames(
    window: xr.Dataset, offsets: list[int] = SHADING_OFFSETS_H
) -> tuple[list[dict], list[dict]]:
    """One leaflet-velocity flow grid per slider offset, so the animated trails scrub
    with the speed/ζ·f shadings instead of staying pinned to the now slice.

    Slices the same window and offsets as :func:`to_speed_frames` (no extra fetch;
    12 h steps land on the 6-hourly grid exactly), rendering each with
    :func:`to_velocity_json`. Returns ``(frames, manifest)``: each frame is
    ``{offset_h, valid_time, file, data}`` with ``data`` the velocity ``[u, v]`` list
    and ``file`` e.g. ``currents_+00h.json``; ``manifest`` is the compact
    ``[{offset_h, valid_time, file}]`` the build merges into ``currents_meta.json`` as
    ``flow_frames`` for the client's slider. Same valid-times as the speed frames; the
    *now* frame (offset 0) is the slice the map opens on.
    """
    frames = [
        {
            "offset_h": offset,
            "valid_time": valid_time(ds),
            "file": frame_filename("currents", offset, ext="json"),
            "data": to_velocity_json(ds),
        }
        for offset, ds in select_frames(window, offsets)
    ]
    manifest = [{k: f[k] for k in ("offset_h", "valid_time", "file")} for f in frames]
    return frames, manifest


# --- speed shading (Mercator-warped PNG) -----------------------------------

def _colorbar_stops(n: int = COLORBAR_STOPS) -> list[str]:
    """Hex stops sampled along the speed colour map, low -> high."""
    return [mcolors.to_hex(SPEED_CMAP(i / (n - 1))) for i in range(n)]


def _speed_of(frame: xr.Dataset) -> np.ndarray:
    """|velocity| on the ascending lat/lon grid, land NaN preserved."""
    f = frame.sortby("latitude").sortby("longitude")
    return np.hypot(f["uo"].values, f["vo"].values)


def to_speed_frames(
    window: xr.Dataset, offsets: list[int] = SHADING_OFFSETS_H
) -> tuple[list[dict], dict]:
    """Render |velocity| for each slider offset as a compact **lossless WebP**
    (cmocean ``speed``, land transparent) and return ``(frames, meta)``.

    All frames share **one** colour scale — ``vmax`` is the ``SPEED_CLIP_PERCENTILE``
    of speed pooled over *every* frame — so a colour means the same speed at every
    time and the single legend stays honest across the slider. Each frame is a dict
    ``{offset_h, valid_time, file, image}``; ``meta`` (``currents_meta.json``)
    carries the shared ``bounds/vmax/units/colorbar`` plus a ``frames`` manifest
    ``[{offset_h, valid_time, file}]``, ``now_offset_h``, and a top-level
    ``valid_time`` (= the now frame) for now-only readers (the deploy tool's start).
    """
    slices = select_frames(window, offsets)
    speeds = [_speed_of(ds) for _, ds in slices]
    vmax = float(np.nanpercentile(np.stack(speeds), SPEED_CLIP_PERCENTILE))

    def to_rgba(warped):
        rgba = SPEED_CMAP(np.clip(warped / vmax, 0.0, 1.0))
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    frames, bounds = [], None
    for (offset, ds), speed in zip(slices, speeds):
        f = ds.sortby("latitude").sortby("longitude")
        image, bounds = _raster.mercator_rgba_webp(
            speed, f["latitude"].values, f["longitude"].values, to_rgba
        )
        frames.append(
            {
                "offset_h": offset,
                "valid_time": valid_time(ds),
                "file": frame_filename("speed", offset),
                "image": image,
            }
        )
    meta = {
        "valid_time": _now_valid_time(frames),
        "bounds": bounds,
        "vmax": vmax,
        "units": "m/s",
        "colorbar": _colorbar_stops(),
        "now_offset_h": 0,
        "frames": [{k: f[k] for k in ("offset_h", "valid_time", "file")} for f in frames],
    }
    return frames, meta
