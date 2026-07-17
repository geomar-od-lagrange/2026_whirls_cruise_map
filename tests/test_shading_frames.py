"""Absolute-time speed & flow shading frames plus the incremental-render planner
(_currents).

Frames are named by absolute valid time (speed_2026-06-28T00Z.webp), span every 12 h
step from FIELD_TMIN through the forecast edge, and render on a *frozen* colour scale
(SPEED_VMAX). Only frames that aren't yet final are (re)rendered each run. These pin
the absolute naming + round-trip, the frozen scale, the compact WebP encoding / land
mask, the flow manifest, and the pure planner / manifest / pruning helpers the build
drives — all network-free.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import matplotlib.colors as mcolors
import numpy as np
import xarray as xr
from PIL import Image

from whirls_cruise_map import _currents
from whirls_cruise_map._currents import (
    FRAME_FINAL_MARGIN_H,
    FRAME_STEP_H,
    SPEED_VMAX,
    existing_frame_times,
    first_pending_frame,
    frame_manifest,
    frame_span,
    nearest_valid_time,
    plan_render,
    prune_stale_frames,
    to_flowvis_frames,
    to_speed_frames,
)
from whirls_cruise_map._frames import (
    N_BINS,
    frame_filename,
    frame_valid_time,
    parse_frame_filename,
)

T0 = datetime(2026, 6, 28, 0, 0, tzinfo=timezone.utc)  # a 00Z frame anchor


def _window(t_lo: datetime = T0, n_steps: int = 9, with_land: bool = False):
    """A 6-hourly window from ``t_lo`` whose speed *grows with time*, so a per-frame
    scale would drift — the frozen SPEED_VMAX must not."""
    lats = np.linspace(-55.0, -15.0, 60)
    lons = np.linspace(-10.0, 35.0, 66)
    t0 = np.datetime64(t_lo.replace(tzinfo=None), "ns")
    times = t0 + (np.arange(n_steps) * 6).astype("timedelta64[h]")
    base_u = np.tile(np.linspace(0.1, 0.6, lons.size), (lats.size, 1))
    base_v = np.tile(np.linspace(-0.3, 0.3, lats.size)[:, None], (1, lons.size))
    scale = 1.0 + np.arange(n_steps) / 10.0  # later frames strictly faster
    u = base_u[None] * scale[:, None, None]
    v = base_v[None] * scale[:, None, None]
    if with_land:
        u[:, :5, :5] = np.nan
        v[:, :5, :5] = np.nan
    return xr.Dataset(
        {"uo": (("time", "latitude", "longitude"), u),
         "vo": (("time", "latitude", "longitude"), v)},
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


def _frame_times():
    return frame_span(T0, T0 + timedelta(hours=48))  # 0, 12, 24, 36, 48 h


# --- absolute naming + round-trip ---------------------------------------------

def test_absolute_names_and_round_trip():
    fts = _frame_times()
    frames, meta = to_speed_frames(_window(), fts)
    assert [f["file"] for f in frames][:3] == [
        "speed_2026-06-28T00Z.webp",
        "speed_2026-06-28T12Z.webp",
        "speed_2026-06-29T00Z.webp",
    ]
    assert ":" not in frames[0]["file"]  # colon-free token -> a safe filename
    for f, t in zip(frames, fts):
        kind, parsed = parse_frame_filename(f["file"])  # file -> valid_time round-trip
        assert kind == "speed"
        assert parsed == t
        assert f["valid_time"] == frame_valid_time(t)
    assert meta["units"] == "m/s"
    # The renderer returns only the shared scale; the build assembles the manifest.
    assert "valid_time" not in meta and "frames" not in meta


def test_parse_ignores_retired_and_meta_names():
    assert parse_frame_filename("speed_+12h.webp") is None  # retired offset form
    assert parse_frame_filename("currents_meta.json") is None
    assert parse_frame_filename("build.json") is None


# --- frozen colour scale ------------------------------------------------------

def test_frozen_speed_scale_drives_colorbar():
    """vmax is the constant SPEED_VMAX regardless of the field's magnitude, and the
    colorbar is the N_BINS discrete class colours derived from it."""
    _, meta = to_speed_frames(_window(), _frame_times())
    assert meta["vmax"] == SPEED_VMAX == 1.2
    assert len(meta["colorbar"]) == N_BINS
    assert meta["colorbar"] == [
        mcolors.to_hex(_currents.SPEED_CMAP((i + 0.5) / N_BINS)) for i in range(N_BINS)
    ]
    # A much faster field yields the *same* frozen vmax (no re-pooling / drift).
    _, meta2 = to_speed_frames(_window() * 3.0, _frame_times())
    assert meta2["vmax"] == SPEED_VMAX


# --- encoding / land mask / binning -------------------------------------------

def test_frames_are_webp_and_masked():
    frames, _ = to_speed_frames(_window(with_land=True), _frame_times())
    im = Image.open(io.BytesIO(frames[1]["image"]))
    assert im.format == "WEBP"
    alpha = np.array(im.convert("RGBA"))[..., 3]
    assert (alpha == 0).any() and (alpha == 255).any()  # land masked, ocean opaque


def test_frames_binned_to_n_bins():
    frames, meta = to_speed_frames(_window(), _frame_times())
    im = np.array(Image.open(io.BytesIO(frames[1]["image"])).convert("RGBA"))
    opaque = im[im[..., 3] == 255][:, :3]
    n_colours = len({tuple(px) for px in opaque})
    assert 0 < n_colours <= N_BINS
    assert len(meta["colorbar"]) == N_BINS


# --- flow (static streamline) frames -----------------------------------------

def test_flowvis_frames_absolute_and_webp():
    fts = _frame_times()
    frames = to_flowvis_frames(_window(), fts)
    assert [f["file"] for f in frames][:2] == [
        "flowvis_2026-06-28T00Z.webp",
        "flowvis_2026-06-28T12Z.webp",
    ]
    speed_frames, meta = to_speed_frames(_window(), fts)
    # Flow frames are parallel to the speed frames (same grid, same order), so the client
    # scrubs them by the same frame index.
    assert [f["valid_time"] for f in frames] == [f["valid_time"] for f in speed_frames]
    # Each frame is real lossless-WebP bytes and co-registers with the speed raster: the
    # renderer shares the shading's Mercator warp / edge bounds, so the client places the
    # flow overlay with the same `meta.bounds`.
    for f in frames:
        assert f["image"][:4] == b"RIFF" and f["image"][8:12] == b"WEBP"
    sl = _window().isel(time=0).sortby("latitude").sortby("longitude")
    _, bounds = _currents._raster.mercator_streamlines_webp(
        sl["uo"].values, sl["vo"].values, sl["latitude"].values, sl["longitude"].values
    )
    assert bounds == meta["bounds"]


# --- the incremental-render planner (pure) ------------------------------------

def test_plan_render_backfill_then_incremental():
    now = T0 + timedelta(days=3)
    grid = frame_span(T0, now + timedelta(hours=24))

    # First run: nothing on disk -> render the whole span (backfill).
    assert plan_render(grid, set(), now) == grid

    # Second run: every frame on disk -> only recent + forecast (valid_time + margin
    # still ahead of now); the older frames are final and skipped.
    on_disk = set(grid)
    planned = plan_render(grid, on_disk, now)
    margin = timedelta(hours=FRAME_FINAL_MARGIN_H)
    assert planned == [t for t in grid if t + margin > now]
    assert planned and planned != grid  # a genuine incremental subset

    # A deleted old frame re-plans (it's no longer in `existing`).
    old = grid[0]
    assert old not in planned
    assert old in plan_render(grid, on_disk - {old}, now)


def test_plan_render_margin_boundary():
    now = T0 + timedelta(days=2)  # a 00Z instant
    final = now - timedelta(hours=FRAME_FINAL_MARGIN_H)  # valid_time + margin == now
    recent = final + timedelta(hours=FRAME_STEP_H)       # valid_time + margin  > now
    grid = [final, recent]
    planned = plan_render(grid, set(grid), now)
    assert final not in planned  # boundary counts as final (<= now)
    assert recent in planned


# --- manifest / nearest-now / disk scan / fetch bound -------------------------

def test_frame_manifest_schema():
    fts = frame_span(T0, T0 + timedelta(hours=24))
    man = frame_manifest("speed", fts)
    assert all(set(m) == {"valid_time", "file"} for m in man)  # no offset_h
    assert man[0] == {
        "valid_time": "2026-06-28T00:00:00Z",
        "file": "speed_2026-06-28T00Z.webp",
    }
    flow = frame_manifest("flowvis", fts)
    assert flow[0]["file"] == "flowvis_2026-06-28T00Z.webp"


def test_nearest_valid_time_picks_now_frame():
    grid = frame_span(T0, T0 + timedelta(hours=48))
    now = T0 + timedelta(hours=25)  # nearest 12 h frame is +24 h
    assert nearest_valid_time(grid, now) == frame_valid_time(T0 + timedelta(hours=24))


def test_existing_requires_all_three_and_first_pending(tmp_path):
    t = T0
    (tmp_path / frame_filename("speed", t)).write_bytes(b"x")
    assert existing_frame_times(tmp_path) == set()  # speed alone isn't a full frame
    (tmp_path / frame_filename("vorticity", t)).write_bytes(b"x")
    (tmp_path / frame_filename("flowvis", t)).write_bytes(b"x")
    (tmp_path / "currents_meta.json").write_bytes(b"{}")  # must not be mistaken for a frame
    assert existing_frame_times(tmp_path) == {t}

    now = T0 + timedelta(days=5)
    assert first_pending_frame(T0, set(), now) == T0          # cold start: backfill from tmin
    assert first_pending_frame(T0, {T0}, now) > T0            # T0 final: fetch the recent tail


# --- pruning ------------------------------------------------------------------

def test_prune_removes_retired_and_out_of_span_only(tmp_path):
    grid = frame_span(T0, T0 + timedelta(hours=12))
    keep = []
    for t in grid:
        for kind, ext in (("speed", "webp"), ("vorticity", "webp"), ("flowvis", "webp")):
            p = tmp_path / frame_filename(kind, t, ext)
            p.write_bytes(b"x")
            keep.append(p.name)
    (tmp_path / "speed_+12h.webp").write_bytes(b"x")       # retired offset names
    (tmp_path / "vorticity_-12h.webp").write_bytes(b"x")
    (tmp_path / "currents_+00h.json").write_bytes(b"x")    # retired leaflet-velocity flow grid
    (tmp_path / "speed_2020-01-01T00Z.webp").write_bytes(b"x")  # absolute, out of span
    (tmp_path / "currents_meta.json").write_bytes(b"{}")   # meta + non-frame -> untouched
    (tmp_path / "build.json").write_bytes(b"{}")

    removed = prune_stale_frames(tmp_path, grid)
    assert set(removed) == {
        "speed_+12h.webp",
        "vorticity_-12h.webp",
        "currents_+00h.json",
        "speed_2020-01-01T00Z.webp",
    }
    for name in keep:
        assert (tmp_path / name).exists()
    assert (tmp_path / "currents_meta.json").exists()
    assert (tmp_path / "build.json").exists()


# --- #37: float32 window cast + single-slice land mask ------------------------

def test_fetch_shading_window_casts_to_float32(monkeypatch):
    """fetch_shading_window returns a float32 window even though the CMEMS product is
    float64 on the wire — the chunked-lazy astype (#37) narrows uo/vo without holding
    the full float64 window. copernicusmarine.subset is mocked to emit a float64 .nc,
    mirroring the real product's dtype."""
    lats = np.linspace(-55.0, -15.0, 20)
    lons = np.linspace(-10.0, 35.0, 22)
    times = np.datetime64("2026-06-28T00", "ns") + (np.arange(6) * 6).astype("timedelta64[h]")
    u = np.ones((times.size, lats.size, lons.size), dtype=np.float64)
    v = -np.ones((times.size, lats.size, lons.size), dtype=np.float64)
    u[:, :4, :4] = np.nan  # land block, NaN at every step
    v[:, :4, :4] = np.nan

    def fake_subset(**kwargs):
        xr.Dataset(
            {"uo": (("time", "latitude", "longitude"), u),
             "vo": (("time", "latitude", "longitude"), v)},
            coords={"time": times, "latitude": lats, "longitude": lons},
        ).to_netcdf(kwargs["output_filename"])

    monkeypatch.setattr(_currents.copernicusmarine, "subset", fake_subset)

    win = _currents.fetch_shading_window(
        t_lo=datetime(2026, 6, 28, tzinfo=timezone.utc),
        t_hi=datetime(2026, 6, 29, 6, tzinfo=timezone.utc),
    )
    assert win["uo"].dtype == np.float32 and win["vo"].dtype == np.float32
    assert np.array_equal(np.isnan(win["uo"].values), np.isnan(u))  # land pattern kept
    assert np.isfinite(win["vo"].values[:, 10, 10]).all()           # sea stays finite


def test_landmask_is_single_slice_equivalent():
    """The land mask is time-invariant, so to_landmask_webp (#37) reads a single time
    slice: its output for the full N-step window is byte-identical to the 1-step slice."""
    win = _window(n_steps=7, with_land=True)
    full = _currents.to_landmask_webp(win)
    one = _currents.to_landmask_webp(win.isel(time=[0]))
    assert full[0] == one[0]  # identical WebP bytes
    assert full[1] == one[1]  # identical bounds
    assert full[0][:4] == b"RIFF" and full[0][8:12] == b"WEBP"
