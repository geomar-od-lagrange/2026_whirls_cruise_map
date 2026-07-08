"""ζ/f normalized relative vorticity (_vorticity).

Two checks: the derivative/metric maths matches an analytic case exactly, and the
render emits a symmetric-range PNG with land carried through as transparency.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import xarray as xr
from PIL import Image

from whirls_cruise_map._vorticity import (
    OMEGA,
    _EARTH_RADIUS_M,
    to_vorticity_frames,
    zeta_over_f,
)


def _field(lats, lons, u, v, with_time=False):
    ds = xr.Dataset(
        {"uo": (("latitude", "longitude"), u), "vo": (("latitude", "longitude"), v)},
        coords={"latitude": lats, "longitude": lons},
    )
    if with_time:
        ds = ds.assign_coords(time=np.datetime64("2026-07-04T00:00:00"))
    return ds


def test_dvdx_matches_analytic():
    """v linear in longitude, u = 0 => ζ = ∂v/∂x = (1/(R cos φ)) ∂v/∂λ, exact at
    interior points (np.gradient is exact on linear data). Pins the ∂v/∂x half of
    the operator: sign and magnitude, including the cos-latitude metric factor and
    the SH-negative f. Dropping cosφ or using degrees for the lon spacing breaks
    the rtol."""
    lats = np.linspace(-40.0, -39.0, 5)
    lons = np.linspace(10.0, 12.0, 9)
    _, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    a = 3.0  # m/s per radian of longitude
    v = a * np.radians(lon_grid)
    u = np.zeros_like(v)

    zof, olats, _ = zeta_over_f(_field(lats, lons, u, v))

    iy, ix = 2, 4  # interior, away from the one-sided edge stencil
    phi = np.radians(olats[iy])
    zeta_expected = a / (_EARTH_RADIUS_M * np.cos(phi))
    f = 2.0 * OMEGA * np.sin(phi)  # < 0 in the SH
    assert np.isclose(zof[iy, ix], zeta_expected / f, rtol=1e-6)


def test_dudy_matches_analytic():
    """u linear in latitude, v = 0 => ζ = −∂u/∂y = −(1/R) ∂u/∂φ, exact at interior
    points. Pins the *other* half of the operator (the ∂u/∂y term the dv/dx case
    leaves at zero): a missing 1/R metric factor or a degrees-vs-radians slip on
    the latitude spacing changes the magnitude and breaks the rtol."""
    lats = np.linspace(-40.0, -39.0, 9)
    lons = np.linspace(10.0, 12.0, 5)
    lat_grid, _ = np.meshgrid(lats, lons, indexing="ij")
    b = 2.0  # m/s per radian of latitude
    u = b * np.radians(lat_grid)
    v = np.zeros_like(u)

    zof, olats, _ = zeta_over_f(_field(lats, lons, u, v))

    iy, ix = 4, 2  # interior
    phi = np.radians(olats[iy])
    zeta_expected = -b / _EARTH_RADIUS_M  # ζ = −(1/R) ∂u/∂φ, ∂u/∂φ = b
    f = 2.0 * OMEGA * np.sin(phi)
    assert np.isclose(zof[iy, ix], zeta_expected / f, rtol=1e-6)


def test_sh_cyclone_is_positive():
    """A Southern-Hemisphere cyclone (clockwise: u = ω·y_m, v = −ω·x_m about the
    patch centre) has ζ < 0 and f < 0, so ζ/f > 0 — the Rossby-number convention."""
    lat0, lon0 = -40.0, 11.0
    lats = np.linspace(lat0 - 0.5, lat0 + 0.5, 11)
    lons = np.linspace(lon0 - 0.5, lon0 + 0.5, 11)
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    x_m = _EARTH_RADIUS_M * np.cos(np.radians(lat0)) * np.radians(lon_grid - lon0)
    y_m = _EARTH_RADIUS_M * np.radians(lat_grid - lat0)
    omega = 1e-5
    u = omega * y_m
    v = -omega * x_m

    zof, _, _ = zeta_over_f(_field(lats, lons, u, v))
    assert zof[5, 5] > 0.0  # centre cell, cyclonic


def _window(with_land: bool):
    """A time-dimensioned window spanning the slider offsets around now, so
    to_vorticity_frames can select its -12…+72 h frames."""
    lats = np.linspace(-42.0, -38.0, 12)
    lons = np.linspace(9.0, 13.0, 12)
    rng = np.arange(lons.size, dtype=float)
    u2 = np.tile(rng, (lats.size, 1)) * 0.01
    v2 = np.tile(rng[::-1], (lats.size, 1)) * 0.01
    if with_land:
        u2 = u2.copy()
        u2[0, 0] = np.nan  # a "land" cell (persists across every frame)
    now = np.datetime64(datetime.now(timezone.utc).replace(tzinfo=None), "ns")
    times = now + np.arange(-12, 78, 6).astype("timedelta64[h]")  # 6-hourly bracket
    u = np.repeat(u2[None], times.size, axis=0)
    v = np.repeat(v2[None], times.size, axis=0)
    ds = xr.Dataset(
        {"uo": (("time", "latitude", "longitude"), u),
         "vo": (("time", "latitude", "longitude"), v)},
        coords={"time": times, "latitude": lats, "longitude": lons},
    )
    return to_vorticity_frames(ds)


def _alpha(image: bytes) -> np.ndarray:
    """Alpha plane of a frame image, via its RGBA expansion."""
    return np.array(Image.open(io.BytesIO(image)).convert("RGBA"))[..., 3]


def test_render_meta_is_symmetric():
    frames, meta = _window(with_land=False)
    assert meta["vmin"] == -meta["vmax"]  # symmetric range, shared across frames
    assert meta["vmax"] > 0
    assert meta["units"] == "ζ/f"
    assert len(meta["colorbar"]) == 16
    assert [f["offset_h"] for f in meta["frames"]] == [-12, 0, 12, 24, 36, 48, 60, 72]
    assert meta["now_offset_h"] == 0
    # top-level valid_time is the now (offset 0) frame's, ISO-8601 Z.
    now_frame = next(f for f in meta["frames"] if f["offset_h"] == 0)
    assert meta["valid_time"] == now_frame["valid_time"]
    assert meta["valid_time"].endswith("Z")
    assert now_frame["file"] == "vorticity_+00h.webp"


def test_land_becomes_transparent():
    """A NaN input cell must reach every frame image as fully transparent pixels,
    and an all-ocean field must have none — pinning the land alpha mask, not just
    the container format."""
    land_frames, _ = _window(with_land=True)
    ocean_frames, _ = _window(with_land=False)

    assert land_frames[0]["image"][:4] == b"RIFF"  # WebP RIFF container
    assert Image.open(io.BytesIO(land_frames[0]["image"])).format == "WEBP"
    assert (_alpha(land_frames[0]["image"]) == 0).any()  # the land cell shows through
    assert (_alpha(ocean_frames[0]["image"]) > 0).all()  # no spurious transparency
