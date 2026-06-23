"""Fetch the SPASSO FTLE field and render it as a red, alpha-ramped overlay.

Source: the WHIRLS cruise's own SPASSO v2.1 product on IPSL THREDDS (public, no
auth), a daily 00Z backward-FTLE field over the Cape Basin. High FTLE = attracting
Lagrangian coherent structures (eddy/filament rims, transport barriers).

The field is equirectangular and covers only the central Cape Basin box, so it is
warped to Web Mercator (shared with the speed shading) and overlaid at its own
bounds, co-registered with the speed layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr

from . import _raster

URL_TEMPLATE = (
    "https://thredds-x.ipsl.fr/thredds/dodsC/WHIRLS/SATELLITE/FTLE/"
    "{date:%Y%m%d}_FTLE_Copernicus_PHY.nc"
)
MAX_GAP_HOURS = 24.0          # give up if the nearest available file is farther
CLIP_PERCENTILES = (2.0, 98.0)  # full min..max washes to an opaque blanket


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


def to_ftle_png(field: xr.DataArray, valid: datetime) -> tuple[bytes, dict]:
    """Render the FTLE field as a Mercator-warped red, alpha-ramped RGBA PNG
    (alpha 0 at the p2 clip, 1 at the p98 clip) and return ``(png_bytes, meta)``."""
    f = field.sortby("lat").sortby("lon")
    lats = f["lat"].values
    lons = f["lon"].values
    vals = np.asarray(f.values, dtype=float)
    vmin, vmax = (float(x) for x in np.percentile(vals, CLIP_PERCENTILES))

    def to_rgba(warped):
        rgba = np.zeros((*warped.shape, 4), dtype=float)
        rgba[..., 0] = 1.0  # red
        rgba[..., 3] = np.clip((warped - vmin) / (vmax - vmin), 0.0, 1.0)
        return rgba

    png, bounds = _raster.mercator_rgba_png(vals, lats, lons, to_rgba)
    meta = {
        "valid_time": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bounds": bounds,
        "vmin": vmin,
        "vmax": vmax,
        "units": "day-1",
    }
    return png, meta
