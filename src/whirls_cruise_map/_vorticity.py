"""Normalized surface relative vorticity (the Rossby number ζ/f) from the CMEMS
current field, rendered as a diverging Mercator-warped raster.

``ζ = ∂v/∂x − ∂u/∂y`` is the vertical relative vorticity of the surface flow;
``f = 2Ω sin φ`` is the planetary vorticity, **negative in the Southern
Hemisphere** (the cruise bbox is −55…−15° lat). The overlay is the dimensionless
ratio ``ζ/f``. With both ζ and f negative for a Southern-Hemisphere cyclone,
``ζ/f > 0`` is **cyclonic** and ``ζ/f < 0`` is **anticyclonic** — the standard
Rossby-number sign, identical in both hemispheres, so cyclones and anticyclones
read directly as opposite-signed lobes that the speed magnitude alone hides.

Derived from the **same single-time field** the speed/flow overlays use
(:func:`whirls_cruise_map._currents.fetch_field`): vorticity is a spatial
derivative of the ``uo``/``vo`` already in hand, so it needs no extra fetch and
renders at the same near-native 1/12° grid. It is a snapshot diagnostic, not an
advected field.

Structured exactly like the surface-speed shading (:func:`._currents.to_speed_png`)
and the inertial-amplitude raster (:func:`._inertial.to_inertial_png`) — one
diagnostic 2-D field through the shared :func:`._raster.mercator_rgba_png` helper —
with two differences that follow from ζ/f being **signed**: a *diverging* colour
map and a *symmetric* ``±vmax`` clip, reported to the client as a ``vmin`` key in
the meta so the legend spans −vmax…0…+vmax rather than 0…vmax.
"""
from __future__ import annotations

import cmocean
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

from . import _currents, _raster  # noqa: E402

OMEGA = 7.2921159e-5  # Earth's rotation rate (rad/s); f = 2*OMEGA*sin(lat)
_EARTH_RADIUS_M = 6_371_000.0

# Diverging map (blue-green ↔ white ↔ dark-red across the sampled stops) for the
# signed field, clipped symmetrically at this percentile of |ζ/f| so a few
# grid-scale spikes don't wash the scale out. ``curl`` is cmocean's field-curl
# map — built for exactly this quantity.
VORT_CMAP = cmocean.cm.curl
CLIP_PERCENTILE = 98
COLORBAR_STOPS = 16


def zeta_over_f(field: xr.Dataset) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(ζ/f, lats, lons)`` on the ascending lat/lon grid, land kept as NaN.

    ``field`` carries ``uo``/``vo`` on a lat/lon grid (CMEMS land already NaN).
    Derivatives take the sphere's metric factors — ``∂/∂x = 1/(R cos φ) · ∂/∂λ``
    and ``∂/∂y = 1/R · ∂/∂φ`` with λ, φ in radians — via :func:`numpy.gradient`
    along the longitude/latitude axes. ``np.gradient`` propagates NaN into the
    neighbouring cell, so the coastal ocean ring masks out (a one-cell erosion of
    the edge, as the speed warp already does). ``f`` is far from zero over this
    bbox, so the ratio is well-conditioned everywhere.
    """
    f = field.sortby("latitude").sortby("longitude")  # both ascending
    f = f.transpose("latitude", "longitude")  # pin axis order for np.gradient
    lats = f["latitude"].values.astype(float)
    lons = f["longitude"].values.astype(float)
    u = f["uo"].values  # (lat, lon), land NaN
    v = f["vo"].values

    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    cos_phi = np.cos(lat_r)[:, np.newaxis]

    dv_dx = np.gradient(v, lon_r, axis=1) / (_EARTH_RADIUS_M * cos_phi)
    du_dy = np.gradient(u, lat_r, axis=0) / _EARTH_RADIUS_M
    zeta = dv_dx - du_dy

    fcor = (2.0 * OMEGA * np.sin(lat_r))[:, np.newaxis]  # < 0 in the SH
    return zeta / fcor, lats, lons


def _colorbar_stops(n: int = COLORBAR_STOPS) -> list[str]:
    """Hex stops sampled across the full diverging map, low (anticyclonic) → high
    (cyclonic). A local twin of :func:`._currents._colorbar_stops`, which is bound
    to the sequential speed map (cf. :func:`._inertial._colorbar_stops`)."""
    return [mcolors.to_hex(VORT_CMAP(i / (n - 1))) for i in range(n)]


def to_vorticity_png(field: xr.Dataset) -> tuple[bytes, dict]:
    """Render ζ/f as a Mercator-warped RGBA PNG (cmocean ``curl``, clipped to a
    symmetric ``±vmax``, land transparent) and return ``(png_bytes, meta)``.

    ``vmax`` is the ``CLIP_PERCENTILE``-th percentile of ``|ζ/f|``; the field is
    mapped from ``[−vmax, +vmax]`` onto the diverging map so zero lands on its
    neutral midpoint. ``meta`` matches ``currents_meta.json``'s shape plus a
    ``vmin`` (= ``−vmax``) that marks the symmetric range for the client legend;
    ``valid_time`` reuses the field's own so ζ/f shares the speed raster's clock.
    """
    zof, lats, lons = zeta_over_f(field)
    vmax = float(np.nanpercentile(np.abs(zof), CLIP_PERCENTILE))

    def to_rgba(warped):
        # [-vmax, vmax] -> [0, 1] so zero maps to the diverging map's midpoint.
        rgba = VORT_CMAP(np.clip(warped / vmax, -1.0, 1.0) * 0.5 + 0.5)
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    png, bounds = _raster.mercator_rgba_png(zof, lats, lons, to_rgba)
    meta = {
        "valid_time": _currents.valid_time(field),
        "bounds": bounds,
        "vmin": -vmax,
        "vmax": vmax,
        "units": "ζ/f",
        "colorbar": _colorbar_stops(),
    }
    return png, meta
