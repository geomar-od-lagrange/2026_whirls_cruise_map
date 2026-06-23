"""Shared raster helper: warp an equirectangular field to Web Mercator and
colour-map it to an RGBA PNG that ``L.imageOverlay`` places correctly.

Both the speed shading and the FTLE overlay are lat/lon fields drawn on a
Web-Mercator (EPSG:3857) map; a plain image overlay of an equirectangular raster
is mis-registered in latitude. Resampling the rows from even latitude to even
Mercator-y so the overlay's linear stretch lands them right is the fix, and doing
it in one place keeps the two layers co-registered.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import numpy as np  # noqa: E402


def _mercator_y(lat_deg: np.ndarray) -> np.ndarray:
    """Web-Mercator (EPSG:3857) y for a latitude in degrees (unscaled)."""
    lat = np.radians(lat_deg)
    return np.log(np.tan(np.pi / 4 + lat / 2))


def _warp_to_mercator(values: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Resample rows from even latitude to even Mercator-y. ``lats`` ascending;
    returned rows are evenly spaced in Mercator y, south->north."""
    y = _mercator_y(lats)
    y_even = np.linspace(y[0], y[-1], lats.size)
    lat_targets = np.degrees(2.0 * np.arctan(np.exp(y_even)) - np.pi / 2)
    warped = np.empty((lat_targets.size, values.shape[1]), dtype=float)
    for j in range(values.shape[1]):
        # np.interp spreads NaN into the adjacent target rows, so any masked
        # coast widens by a half-cell — fine for a shading/overlay layer.
        warped[:, j] = np.interp(lat_targets, lats, values[:, j])
    return warped


def mercator_rgba_png(values, lats, lons, to_rgba):
    """Warp ``values`` (shape ``(nlat, nlon)``, ``lats``/``lons`` ascending) to
    Web Mercator, colour-map it with ``to_rgba`` and return ``(png_bytes,
    bounds)``.

    ``to_rgba`` receives the north-up warped 2-D array and returns an
    ``(ny, nx, 4)`` float RGBA array (it owns the colour map and the alpha /
    NaN handling). ``bounds`` is ``[[lat_min, lon_min], [lat_max, lon_max]]``
    (SW, NE) for ``L.imageOverlay``.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    warped = _warp_to_mercator(np.asarray(values, dtype=float), lats)
    rgba = to_rgba(warped[::-1, :])  # PNG rows north -> south (top -> bottom)

    buf = io.BytesIO()
    mpimg.imsave(buf, rgba, format="png")
    bounds = [
        [float(lats.min()), float(lons.min())],
        [float(lats.max()), float(lons.max())],
    ]
    return buf.getvalue(), bounds
