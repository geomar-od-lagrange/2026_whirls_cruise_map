"""ζ/f normalized relative vorticity (_vorticity).

Two checks: the derivative/metric maths matches an analytic case exactly, and the
render emits a symmetric-range PNG with land carried through as transparency.
"""
from __future__ import annotations

import io

import matplotlib.image as mpimg
import numpy as np
import xarray as xr

from whirls_cruise_map._vorticity import (
    OMEGA,
    _EARTH_RADIUS_M,
    to_vorticity_png,
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


def _field_and_meta(with_land: bool):
    lats = np.linspace(-42.0, -38.0, 12)
    lons = np.linspace(9.0, 13.0, 12)
    rng = np.arange(lons.size, dtype=float)
    u = np.tile(rng, (lats.size, 1)) * 0.01
    v = np.tile(rng[::-1], (lats.size, 1)) * 0.01
    if with_land:
        u[0, 0] = np.nan  # a "land" cell
    return to_vorticity_png(_field(lats, lons, u, v, with_time=True))


def test_render_meta_is_symmetric():
    _, meta = _field_and_meta(with_land=False)
    assert meta["vmin"] == -meta["vmax"]  # symmetric range
    assert meta["vmax"] > 0
    assert meta["units"] == "ζ/f"
    assert len(meta["colorbar"]) == 16
    assert meta["valid_time"] == "2026-07-04T00:00:00Z"


def test_land_becomes_transparent():
    """A NaN input cell must reach the PNG as fully transparent pixels, and an
    all-ocean field must have none — pinning the alpha mask (rgba[isnan,3]=0), not
    just the PNG signature."""
    def alpha(png: bytes) -> np.ndarray:
        return mpimg.imread(io.BytesIO(png))[..., 3]

    land_png, _ = _field_and_meta(with_land=True)
    ocean_png, _ = _field_and_meta(with_land=False)

    assert land_png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature
    assert (alpha(land_png) == 0.0).any()  # the land cell shows through
    assert (alpha(ocean_png) > 0.0).all()  # no spurious transparency
