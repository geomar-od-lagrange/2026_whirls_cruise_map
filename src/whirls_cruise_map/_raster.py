"""Shared raster helper: warp an equirectangular field to Web Mercator and
colour-map it to an RGBA PNG that ``L.imageOverlay`` places correctly.

The speed shading is a lat/lon field drawn on a Web-Mercator (EPSG:3857) map; a
plain image overlay of an equirectangular raster is mis-registered in latitude.
Resampling the rows from even latitude to even Mercator-y so the overlay's linear
stretch lands them right is the fix.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def _mercator_y(lat_deg: np.ndarray) -> np.ndarray:
    """Web-Mercator (EPSG:3857) y for a latitude in degrees (unscaled)."""
    lat = np.radians(lat_deg)
    return np.log(np.tan(np.pi / 4 + lat / 2))


def _edges(centers: np.ndarray) -> tuple[float, float]:
    """Outer edges of an evenly spaced cell-centre coordinate: the first/last
    centre extended outward by half a cell."""
    return (
        float(centers[0] - 0.5 * (centers[1] - centers[0])),
        float(centers[-1] + 0.5 * (centers[-1] - centers[-2])),
    )


def _warp_to_mercator(values: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Resample rows from even latitude to even Mercator-y. ``lats`` ascending
    cell centres; the returned image spans the outer cell *edges* in Mercator-y
    (so it co-registers with edge-based ``bounds``), with one output row per
    input row sampled at the row's Mercator-y centre. ``L.imageOverlay``
    stretches the bitmap's outer edges onto ``bounds``, so the warp must cover
    edge-to-edge, not centre-to-centre. Rows run south->north."""
    lat_s_edge, lat_n_edge = _edges(lats)
    y_s, y_n = _mercator_y(np.array([lat_s_edge, lat_n_edge]))
    n = lats.size
    dy = (y_n - y_s) / n
    y_centers = y_s + (np.arange(n) + 0.5) * dy
    lat_targets = np.degrees(2.0 * np.arctan(np.exp(y_centers)) - np.pi / 2)
    warped = np.empty((n, values.shape[1]), dtype=float)
    for j in range(values.shape[1]):
        # np.interp clamps to the end values outside the centre range, which
        # extends the edge half-cells as a flat fill — the right behaviour for
        # a cell-centred field shown edge-to-edge.
        warped[:, j] = np.interp(lat_targets, lats, values[:, j])
    return warped


def _warp_north_up(values, lats, lons):
    """Warp ``values`` to Mercator and return ``(north_up_2d, bounds)``.

    ``north_up_2d`` has PNG row order (north → south, top → bottom); ``bounds``
    is ``[[lat_min, lon_min], [lat_max, lon_max]]`` (SW, NE) at the outer cell
    *edges*, since ``L.imageOverlay`` places the bitmap's outer edges (not its
    pixel centres) on the rectangle. Shared by the PNG and WebP writers.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    warped = _warp_to_mercator(np.asarray(values, dtype=float), lats)
    lon_w, lon_e = _edges(lons)
    lat_s, lat_n = _edges(lats)
    bounds = [[lat_s, lon_w], [lat_n, lon_e]]
    return warped[::-1, :], bounds


def mercator_rgba_png(values, lats, lons, to_rgba):
    """Warp ``values`` (shape ``(nlat, nlon)``, ``lats``/``lons`` ascending cell
    centres) to Web Mercator, colour-map it with ``to_rgba`` and return
    ``(png_bytes, bounds)``.

    ``to_rgba`` receives the north-up warped 2-D array and returns an
    ``(ny, nx, 4)`` float RGBA array (it owns the colour map and the alpha /
    NaN handling). ``bounds`` is the outer cell edges (see :func:`_warp_north_up`).
    """
    north_up, bounds = _warp_north_up(values, lats, lons)
    buf = io.BytesIO()
    mpimg.imsave(buf, to_rgba(north_up), format="png")
    return buf.getvalue(), bounds


def mercator_rgba_webp(values, lats, lons, to_rgba):
    """Warp ``values`` to Web Mercator, colour-map it with ``to_rgba`` and write a
    **lossless WebP** — same pixels as :func:`mercator_rgba_png` at roughly *half*
    the bytes (measured ~85 kB vs ~150 kB and ~310 kB for indexed-PNG / RGBA-PNG on
    the cruise-bbox speed field), which is what makes an 8-frame time slider
    affordable on the at-sea link.

    ``to_rgba`` owns the colour map and the alpha / NaN (land → transparent)
    handling, exactly as for the PNG writer, and returns an ``(ny, nx, 4)`` float
    RGBA array; WebP lossless keeps the alpha plane, so land stays transparent.
    ``lossless`` + ``method=6`` picks the smallest encoding (slower, but this is a
    build step). The client renders the file directly (``L.imageOverlay``); WebP is
    universally supported and honours ``image-rendering: pixelated`` like any image.
    Returns ``(webp_bytes, bounds)``.
    """
    north_up, bounds = _warp_north_up(values, lats, lons)
    rgba = (np.clip(to_rgba(north_up), 0.0, 1.0) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(
        buf, format="WEBP", lossless=True, quality=100, method=6
    )
    return buf.getvalue(), bounds
