"""Fetch today's CMEMS surface currents and render them for the map.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N, widened by
10 deg on every side and expressed in -180..180 for the web map.

From one CMEMS field (the single time nearest now) we derive two things at two
resolutions:

- ``to_velocity_json`` — a coarsened leaflet-velocity ``[u, v]`` grid for the
  animated flow trails (the vector grid ships to the browser, so it is coarsened
  for size/animation; the trail texture does not need full resolution).
- ``to_speed_png`` — a near-native speed raster (cmocean ``speed``, Web-Mercator
  warped, land transparent) plus small metadata that drives the client overlay
  and legend.
"""
from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cmocean
import copernicusmarine
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.image as mpimg  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"

# Coarsen the native 1/12-deg grid for the animated trails only. The speed raster
# stays near-native (it is a small image either way and looks markedly sharper).
COARSEN_STRIDE = 3

# Speed shading.
SPEED_CMAP = cmocean.cm.speed
SPEED_CLIP_PERCENTILE = 99
COLORBAR_STOPS = 16


# --- fetch -----------------------------------------------------------------

def fetch_field(bbox: dict = BBOX) -> xr.Dataset:
    """Download surface ``uo``/``vo`` over ``bbox`` for the time nearest now and
    return the single-time 2-D ``(latitude, longitude)`` field, land kept as NaN.

    Relies on the local copernicusmarine login.
    """
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "currents.nc"
        copernicusmarine.subset(
            dataset_id=DATASET_ID,
            variables=["uo", "vo"],
            minimum_longitude=bbox["lon_min"],
            maximum_longitude=bbox["lon_max"],
            minimum_latitude=bbox["lat_min"],
            maximum_latitude=bbox["lat_max"],
            minimum_depth=0.49,
            maximum_depth=0.5,
            start_datetime=now,
            end_datetime=now,
            output_filename=str(out),
            overwrite=True,
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()

    if "depth" in ds.dims:
        ds = ds.isel(depth=0)
    if "time" in ds.dims:
        ds = ds.sel(time=np.datetime64(now.replace(tzinfo=None)), method="nearest")
    return ds.squeeze(drop=True)


def valid_time(field: xr.Dataset) -> str:
    """ISO-8601 valid time of the field (UTC, ``Z`` suffix)."""
    return np.datetime_as_string(field["time"].values, unit="s") + "Z"


# --- flow trails (leaflet-velocity grid) -----------------------------------

def _component_header(field: xr.DataArray, number: int, name: str) -> dict:
    """leaflet-velocity header for one component; ``field`` must be ordered
    latitude-descending, longitude-ascending."""
    lats = field["latitude"].values
    lons = field["longitude"].values
    return {
        "parameterUnit": "m.s-1",
        "parameterCategory": 2,
        "parameterNumber": number,
        "parameterNumberName": name,
        "nx": int(lons.size),
        "ny": int(lats.size),
        "lo1": float(lons.min()),
        "lo2": float(lons.max()),
        "la1": float(lats.max()),
        "la2": float(lats.min()),
        "dx": float(abs(lons[1] - lons[0])),
        "dy": float(abs(lats[1] - lats[0])),
        "refTime": valid_time(field),
        "forecastTime": 0,
    }


def _component(field: xr.DataArray, number: int, name: str) -> dict:
    """Turn a 2-D field into a leaflet-velocity object: data is row-major from the
    north-west corner (latitude descending, longitude ascending); land NaN -> 0."""
    field = field.sortby("latitude", ascending=False).sortby(
        "longitude", ascending=True
    )
    data = np.nan_to_num(field.values, nan=0.0).astype(float)
    return {
        "header": _component_header(field, number, name),
        "data": data.ravel(order="C").tolist(),
    }


def to_velocity_json(field: xr.Dataset, stride: int = COARSEN_STRIDE) -> list[dict]:
    """Coarsened leaflet-velocity ``[u_object, v_object]`` for the flow trails."""
    coarse = field.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )
    return [
        _component(coarse["uo"], number=2, name="Eastward current"),
        _component(coarse["vo"], number=3, name="Northward current"),
    ]


# --- speed shading (Mercator-warped PNG) -----------------------------------

def _mercator_y(lat_deg: np.ndarray) -> np.ndarray:
    """Web-Mercator (EPSG:3857) y for a latitude in degrees (unscaled)."""
    lat = np.radians(lat_deg)
    return np.log(np.tan(np.pi / 4 + lat / 2))


def _warp_to_mercator(speed: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Resample rows from even latitude to even Mercator-y so a plain imageOverlay
    (which stretches linearly in the map's Mercator CRS) registers correctly.
    ``lats`` ascending; returned rows are evenly spaced in Mercator y, south->north.
    """
    y = _mercator_y(lats)
    y_even = np.linspace(y[0], y[-1], lats.size)
    lat_targets = np.degrees(2.0 * np.arctan(np.exp(y_even)) - np.pi / 2)
    warped = np.empty((lat_targets.size, speed.shape[1]), dtype=float)
    for j in range(speed.shape[1]):
        # np.interp spreads land NaN into the adjacent target rows, so coastlines
        # mask a half-cell wider than the true coast — fine (no green onto land).
        warped[:, j] = np.interp(lat_targets, lats, speed[:, j])
    return warped


def _colorbar_stops(n: int = COLORBAR_STOPS) -> list[str]:
    """Hex stops sampled along the speed colour map, low -> high."""
    return [mcolors.to_hex(SPEED_CMAP(i / (n - 1))) for i in range(n)]


def to_speed_png(field: xr.Dataset) -> tuple[bytes, dict]:
    """Render |velocity| as a Mercator-warped RGBA PNG (cmocean ``speed``, clipped
    at the 99th percentile, land transparent) and return ``(png_bytes, meta)``.

    ``meta`` carries the latlng ``bounds`` for ``L.imageOverlay`` plus ``vmax``,
    ``units``, ``valid_time`` and ``colorbar`` stops for the client legend.
    """
    f = field.sortby("latitude").sortby("longitude")  # both ascending
    lats = f["latitude"].values
    lons = f["longitude"].values
    speed = np.hypot(f["uo"].values, f["vo"].values)  # land NaN preserved

    vmax = float(np.nanpercentile(speed, SPEED_CLIP_PERCENTILE))
    warped = _warp_to_mercator(speed, lats)  # south -> north

    norm = np.clip(warped / vmax, 0.0, 1.0)
    rgba = SPEED_CMAP(norm)
    rgba[np.isnan(warped), 3] = 0.0  # land transparent
    rgba = rgba[::-1, :, :]  # PNG rows north -> south (top -> bottom)

    buf = io.BytesIO()
    mpimg.imsave(buf, rgba, format="png")

    meta = {
        "valid_time": valid_time(field),
        "bounds": [
            [float(lats.min()), float(lons.min())],  # SW
            [float(lats.max()), float(lons.max())],  # NE
        ],
        "vmax": vmax,
        "units": "m/s",
        "colorbar": _colorbar_stops(),
    }
    return buf.getvalue(), meta
