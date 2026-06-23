"""Fetch the SPASSO FTLE field and render it as simplified ridge contours.

Source: the WHIRLS cruise's own SPASSO v2.1 product on IPSL THREDDS (public, no
auth), a daily 00Z backward-FTLE field over the Cape Basin. High FTLE = attracting
Lagrangian coherent structures (eddy/filament rims, transport barriers).

The field is vectorised to a single iso-FTLE line contour (GeoJSON) rather than a
raster: Leaflet projects the lon/lat geometry itself (no manual Mercator warp),
the artifact is ~1-2 orders of magnitude smaller than the equivalent PNG, and it
stays crisp at every zoom. The level is placed at a fraction of the p2..p98
clipped range (mirroring the raster's old contrast clip) so the strong Agulhas
retroflection tail can't capture it and starve the Cape Basin eddies the cruise
targets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import contourpy
import numpy as np
import xarray as xr

URL_TEMPLATE = (
    "https://thredds-x.ipsl.fr/thredds/dodsC/WHIRLS/SATELLITE/FTLE/"
    "{date:%Y%m%d}_FTLE_Copernicus_PHY.nc"
)
MAX_GAP_HOURS = 24.0          # give up if the nearest available file is farther
SMOOTH_CELLS = 3              # light 3x3 box smooth before contouring
CLIP_PERCENTILES = (2.0, 98.0)  # the contrast ramp the level is placed within
LEVEL_FRAC = 0.40            # level = p2 + frac * (p98 - p2)
SIMPLIFY_TOL_DEG = 0.015     # Douglas-Peucker tolerance (~1.5 source cells)
MIN_LEN_DEG = 0.08           # drop contour rings shorter than ~8 km (noise specks)
ROUND_DP = 3                 # coordinate precision (~110 m << 1.1 km cell)
LEVEL_COLOR = "#cb181d"      # red, matched in the client style + legend


def fetch_ftle(target: datetime) -> tuple[xr.DataArray, datetime] | None:
    """Open the FTLE file whose 00Z time is closest to ``target`` and within
    ``MAX_GAP_HOURS``; return ``(ftle_2d, valid_time)`` or ``None``.

    Tries the nearest candidate dates in order of time gap. ``ftle_2d`` carries
    ascending ``lat``/``lon`` coordinates.
    """
    base = target.date()
    candidates = []
    for day in (base - timedelta(days=1), base, base + timedelta(days=1)):
        t = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        gap = abs((t - target).total_seconds()) / 3600.0
        if gap <= MAX_GAP_HOURS:
            candidates.append((gap, t))
    candidates.sort()

    for _gap, t in candidates:
        try:
            ds = xr.open_dataset(URL_TEMPLATE.format(date=t))
        except Exception:
            continue
        ftle = ds["ftle"].isel(time=0)
        lon1d = np.asarray(ds["lons"].values)
        lat1d = np.asarray(ds["lats"].values)
        if lon1d.ndim == 2:
            lon1d = lon1d[0]
        if lat1d.ndim == 2:
            lat1d = lat1d[:, 0]
        ftle = ftle.assign_coords(lat=("lat", lat1d), lon=("lon", lon1d))
        return ftle.load(), t
    return None


def _simplify(points: np.ndarray, tol: float) -> np.ndarray:
    """Iterative Douglas-Peucker on an ``(N, 2)`` polyline; ``tol`` in degrees."""
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = b - a
        length = np.hypot(*seg)
        v = points[i + 1:j] - a
        if length == 0:
            d = np.hypot(v[:, 0], v[:, 1])
        else:
            d = np.abs(seg[0] * v[:, 1] - seg[1] * v[:, 0]) / length
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack.append((i, i + 1 + k))
            stack.append((i + 1 + k, j))
    return points[keep]


def _path_length(points: np.ndarray) -> float:
    """Total length of an ``(N, 2)`` polyline in degrees (planar approximation)."""
    return float(np.hypot(np.diff(points[:, 0]), np.diff(points[:, 1])).sum())


def to_ftle_geojson(field: xr.DataArray, valid: datetime) -> tuple[dict, dict]:
    """Render the FTLE field as a single simplified iso-FTLE line contour and
    return ``(geojson, meta)``.

    The contour is lightly smoothed, extracted at ``LEVEL_FRAC`` of the p2..p98
    range, Douglas-Peucker simplified, length-pruned of noise specks, and rounded
    to ``ROUND_DP`` decimals. ``geojson`` is a one-feature ``FeatureCollection``
    (a ``MultiLineString``); ``meta`` carries the valid-time, units and level.
    """
    f = field.sortby("lat").sortby("lon")
    smoothed = f.rolling(
        lat=SMOOTH_CELLS, lon=SMOOTH_CELLS, center=True, min_periods=1
    ).mean()
    lats = f["lat"].values.astype(float)
    lons = f["lon"].values.astype(float)
    vals = np.asarray(smoothed.values, dtype=float)

    finite = vals[np.isfinite(vals)]
    vmin, vmax = (float(x) for x in np.percentile(finite, CLIP_PERCENTILES))
    level = vmin + LEVEL_FRAC * (vmax - vmin)

    gen = contourpy.contour_generator(lons, lats, vals)
    coordinates = []
    for line in gen.lines(level):
        pts = np.asarray(line, dtype=float)
        if len(pts) < 4:
            continue
        pts = _simplify(pts, SIMPLIFY_TOL_DEG)
        if len(pts) < 3 or _path_length(pts) < MIN_LEN_DEG:
            continue
        coordinates.append(
            [[round(float(x), ROUND_DP), round(float(y), ROUND_DP)] for x, y in pts]
        )

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"level": round(level, 4), "frac": LEVEL_FRAC, "rank": 0},
                "geometry": {"type": "MultiLineString", "coordinates": coordinates},
            }
        ],
    }
    meta = {
        "valid_time": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units": "day-1",
        "levels": [
            {
                "value": round(level, 4),
                "frac": LEVEL_FRAC,
                "rank": 0,
                "color": LEVEL_COLOR,
            }
        ],
    }
    return geojson, meta
