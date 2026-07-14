"""Normalized surface relative vorticity (the Rossby number ζ/f) from the CMEMS
current field, rendered as a diverging Mercator-warped raster.

``ζ = ∂v/∂x − ∂u/∂y`` is the vertical relative vorticity of the surface flow;
``f = 2Ω sin φ`` is the planetary vorticity, **negative in the Southern
Hemisphere** (the cruise bbox is −55…−15° lat). The overlay is the dimensionless
ratio ``ζ/f``. With both ζ and f negative for a Southern-Hemisphere cyclone,
``ζ/f > 0`` is **cyclonic** and ``ζ/f < 0`` is **anticyclonic** — the standard
Rossby-number sign, identical in both hemispheres, so cyclones and anticyclones
read directly as opposite-signed lobes that the speed magnitude alone hides.

Derived from the **same fetched window** the speed/flow overlays use
(:func:`whirls_cruise_map._currents.fetch_shading_window`): vorticity is a spatial
derivative of the ``uo``/``vo`` already in hand, so it needs no extra fetch and
renders at the same near-native 1/12° grid. One frame per requested absolute time (the
same incremental render plan the speed frames follow — see
:func:`._currents.plan_render`); each is a snapshot diagnostic of that instant, not an
advected field.

Structured exactly like the surface-speed shading
(:func:`._currents.to_speed_frames`) — one diagnostic 2-D field per frame through
the shared :func:`._raster.mercator_rgba_webp` helper on one frozen colour scale —
with two differences that follow from ζ/f being **signed**: a *diverging* colour
map and a *symmetric* ``±VORT_CLIP`` clip, reported to the client as a ``vmin`` key in
the meta so the legend spans −vmax…0…+vmax rather than 0…vmax.
"""
from __future__ import annotations

from datetime import datetime

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
# signed field, clipped symmetrically at a **frozen** ±VORT_CLIP (|ζ/f|) so a few
# grid-scale spikes don't wash the scale out. A constant, not a per-build pooled
# percentile: re-pooling over a growing frame history would drift the scale and force
# every immutable old frame back into build memory. Frozen 2026-07-13 from the pooled
# 98th-percentile |ζ/f| of the then-current 8-frame window (which rendered 0.30), so
# the legend never breathes across builds. ``curl`` is cmocean's field-curl map —
# built for exactly this quantity.
VORT_CMAP = cmocean.cm.curl
VORT_CLIP = 0.3  # |ζ/f|


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


def _colorbar_stops(n: int = _currents.N_BINS) -> list[str]:
    """The ``n`` discrete bin colours the ζ/f raster uses across the full diverging
    map, low (anticyclonic) → high (cyclonic) — the ``(i+0.5)/n`` midpoints
    :func:`._currents._quantize_unit` snaps to. A local twin of
    :func:`._currents._colorbar_stops`, which is bound to the sequential speed map
    (cf. :func:`._inertial._colorbar_stops`)."""
    return [mcolors.to_hex(VORT_CMAP((i + 0.5) / n)) for i in range(n)]


def to_vorticity_frames(
    window: xr.Dataset, frame_times: list[datetime]
) -> tuple[list[dict], dict]:
    """Render ζ/f for each requested frame time as a compact **lossless WebP** (cmocean
    ``curl``, symmetric ``±VORT_CLIP`` clip, land transparent) and return
    ``(frames, meta)`` — the signed, diverging twin of
    :func:`._currents.to_speed_frames`.

    The clip is the **frozen** :data:`VORT_CLIP`, so the symmetric ``[−clip, +clip]``
    scale (and its single legend) holds across every frame and every immutable old
    frame stays colour-consistent; each field maps onto the diverging map with zero at
    its neutral midpoint. Frames are ``{valid_time, file, image}``; ``meta`` carries
    ``bounds/vmin/vmax/units/colorbar`` (``vmin = −vmax`` marking the symmetric range)
    for the build to write into ``vorticity_meta.json`` alongside the full-span
    ``frames`` manifest and top-level ``valid_time`` it assembles itself.
    """
    def to_rgba(warped):
        # [-clip, clip] -> [0, 1] so zero maps to the diverging map's midpoint, then
        # quantize to N_BINS flat classes before the lookup (see _currents.N_BINS).
        # N_BINS is even, so zero (-> 0.5) stays a bin *edge*: 6 classes per sign.
        t = _currents._quantize_unit(np.clip(warped / VORT_CLIP, -1.0, 1.0) * 0.5 + 0.5)
        rgba = VORT_CMAP(t)
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    frames, bounds = [], None
    for ft in frame_times:
        zof, lats, lons = zeta_over_f(_currents._slice_at(window, ft))
        image, bounds = _raster.mercator_rgba_webp(zof, lats, lons, to_rgba)
        frames.append(
            {
                "valid_time": _currents.frame_valid_time(ft),
                "file": _currents.frame_filename("vorticity", ft),
                "image": image,
            }
        )
    meta = {
        "bounds": bounds,
        "vmin": -VORT_CLIP,
        "vmax": VORT_CLIP,
        "units": "ζ/f",
        "colorbar": _colorbar_stops(),
    }
    return frames, meta
