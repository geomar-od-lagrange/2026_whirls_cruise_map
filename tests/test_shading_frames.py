"""Time-slider speed and flow frames (_currents.to_speed_frames /
to_velocity_frames).

The slider ships one lossless WebP per 12 h offset (-12 … +72 h), all sharing one
colour scale so a colour means the same speed at every time, plus one
leaflet-velocity flow grid per offset so the trails scrub in lockstep. These pin the
frame set, the shared vmax, the compact WebP encoding, the land mask, and the flow
frame manifest.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import xarray as xr
from PIL import Image

from whirls_cruise_map import _currents
from whirls_cruise_map._currents import (
    SHADING_OFFSETS_H,
    to_speed_frames,
    to_velocity_frames,
)


def _window(with_land: bool = False):
    """A 6-hourly window around now whose speed *grows with time*, so a per-frame
    scale would drift — the shared scale must be pinned to the fastest frame."""
    lats = np.linspace(-55.0, -15.0, 60)
    lons = np.linspace(-10.0, 35.0, 66)
    now = np.datetime64(datetime.now(timezone.utc).replace(tzinfo=None), "ns")
    offsets = np.arange(-12, 78, 6)  # 6-hourly, brackets the -12…+72 range
    times = now + offsets.astype("timedelta64[h]")
    base_u = np.tile(np.linspace(0.1, 0.6, lons.size), (lats.size, 1))
    base_v = np.tile(np.linspace(-0.3, 0.3, lats.size)[:, None], (1, lons.size))
    # scale each frame up with the offset -> later frames are strictly faster
    scale = 1.0 + (offsets - offsets.min()) / 100.0
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


def test_frame_set_and_names():
    frames, meta = to_speed_frames(_window())
    assert [f["offset_h"] for f in frames] == SHADING_OFFSETS_H == [-12, 0, 12, 24, 36, 48, 60, 72]
    assert [f["file"] for f in meta["frames"]][:3] == [
        "speed_-12h.webp", "speed_+00h.webp", "speed_+12h.webp",
    ]
    assert meta["units"] == "m/s"
    assert meta["valid_time"] == next(f["valid_time"] for f in frames if f["offset_h"] == 0)


def test_one_shared_scale():
    """A single vmax — the SPEED_CLIP_PERCENTILE pooled over *every* frame — backs
    the whole slider, so a colour is the same speed at every time. Pin that
    definition, and that it genuinely spans the frames (between the slowest and
    fastest frame's own percentile), unlike a per-frame scale that would drift."""
    ds = _window()
    _, meta = to_speed_frames(ds)
    slices = _currents.select_frames(ds)
    pooled = np.stack([_currents._speed_of(s) for _, s in slices])
    expected = float(np.nanpercentile(pooled, _currents.SPEED_CLIP_PERCENTILE))
    assert meta["vmax"] == expected
    per_frame = [
        np.nanpercentile(_currents._speed_of(s), _currents.SPEED_CLIP_PERCENTILE)
        for _, s in slices
    ]
    assert min(per_frame) < meta["vmax"] < max(per_frame)  # a genuine pool, not one frame


def test_frames_are_webp_and_masked():
    frames, _ = to_speed_frames(_window(with_land=True))
    im = Image.open(io.BytesIO(frames[1]["image"]))
    assert im.format == "WEBP"  # the compact encoding
    alpha = np.array(im.convert("RGBA"))[..., 3]
    assert (alpha == 0).any() and (alpha == 255).any()  # land masked, ocean opaque


# --- flow frames --------------------------------------------------------------

def test_flow_frame_set_and_manifest():
    """One flow grid per slider offset, JSON-named, valid-times matching the speed
    frames so the two scrub in lockstep. The manifest carries no bulky `data`."""
    frames, manifest = to_velocity_frames(_window())
    assert [f["offset_h"] for f in frames] == SHADING_OFFSETS_H
    assert [f["file"] for f in manifest][:3] == [
        "currents_-12h.json", "currents_+00h.json", "currents_+12h.json",
    ]
    speed_frames, _ = to_speed_frames(_window())
    assert [f["valid_time"] for f in frames] == [f["valid_time"] for f in speed_frames]
    # Manifest is the compact client seam: offsets/times/files only, no velocity data.
    assert all(set(m) == {"offset_h", "valid_time", "file"} for m in manifest)


def test_flow_frame_data_is_leaflet_velocity_and_rounded():
    """Each frame's `data` is the two-component leaflet-velocity list, with values
    rounded to 4 dp to keep the 8-frame slider affordable on the VSAT link."""
    frames, _ = to_velocity_frames(_window())
    data = frames[0]["data"]
    assert [c["header"]["parameterNumberName"] for c in data] == [
        "Eastward current", "Northward current",
    ]
    for c in data:
        assert all(v == round(v, 4) for v in c["data"])
