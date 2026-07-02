"""Fetch today's CMEMS surface currents and render them for the map.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N, widened by
10 deg on every side and expressed in -180..180 for the web map.

From one CMEMS field (the single time nearest now) we derive two things at two
resolutions:

- ``to_velocity_json`` — a coarsened leaflet-velocity ``[u, v]`` grid for the
  animated flow trails. Its magnitude is compressed sub-linearly (see
  ``VELOCITY_GAMMA``) so the slow eddies animate visibly while the Agulhas jet
  does not run away; direction is preserved.
- ``to_speed_png`` — a near-native speed raster (cmocean ``speed``, Web-Mercator
  warped, land transparent) plus small metadata for the client.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cmocean
import copernicusmarine
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

from . import _raster  # noqa: E402

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"

# Hourly surface product for the *time-dependent* forecast/hindcast advection
# field. 6-hourly (``DATASET_ID``) resolves the inertial band here (T_f ~15-24 h
# > 12 h Nyquist), but only ~3 samples per inertial cycle, so linear-in-time
# interpolation chords the loop; hourly (~20/cycle) traces it smoothly for a
# negligible fetch cost (measured +0.8 s over 6-hourly for a +/-12 h window). The
# overlays (currents.json, speed.png) still use the single-time ``DATASET_ID``
# snapshot; only the advection field is the hourly window. See docs/forecast.md.
WINDOW_DATASET_ID = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
WINDOW_BACK_H = 12  # hours of hourly field to fetch behind now (hindcast + bracket)
WINDOW_FWD_H = 12   # ... and ahead of now (forecast + bracket); +/-6 h advection

# Coarsen the native 1/12-deg grid for the animated trails only. The speed raster
# stays near-native (it is a small image either way and looks markedly sharper).
COARSEN_STRIDE = 3

# Sub-linear compression of the trail-animation velocity magnitude (gamma < 1).
# gamma=0.5 (sqrt) lifts the slow eddies relative to the fast jet (~10x -> ~3x).
VELOCITY_GAMMA = 0.5

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


def fetch_field_window(
    bbox: dict = BBOX, back_h: int = WINDOW_BACK_H, fwd_h: int = WINDOW_FWD_H
) -> xr.Dataset:
    """Download hourly surface ``uo``/``vo`` over ``bbox`` for the window
    ``[now-back_h, now+fwd_h]`` and return the 3-D ``(time, latitude, longitude)``
    field with the **time dimension preserved**, land kept as NaN.

    This feeds the *time-dependent* forecast/hindcast advection: the particle is
    pushed by the current at its own clock time, so it traces the inertial loop the
    model already carries instead of the straight streamline of a single frozen
    snapshot. ``coordinates_selection_method="outside"`` makes the returned steps
    *bracket* the window so the stepper always interpolates between two real times
    at the edges. Relies on the local copernicusmarine login.
    """
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "currents_window.nc"
        copernicusmarine.subset(
            dataset_id=WINDOW_DATASET_ID,
            variables=["uo", "vo"],
            minimum_longitude=bbox["lon_min"],
            maximum_longitude=bbox["lon_max"],
            minimum_latitude=bbox["lat_min"],
            maximum_latitude=bbox["lat_max"],
            minimum_depth=0.49,
            maximum_depth=0.5,
            start_datetime=now - timedelta(hours=back_h),
            end_datetime=now + timedelta(hours=fwd_h),
            coordinates_selection_method="outside",
            output_filename=str(out),
            overwrite=True,
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()

    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds


def valid_time(field: xr.Dataset) -> str:
    """ISO-8601 valid time of the field (UTC, ``Z`` suffix). For a single-time
    field; the windowed field carries a ``time`` dimension instead."""
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
    north-west corner (latitude descending, longitude ascending); land NaN -> 0.

    leaflet-velocity needs a hole-free regular grid and has no land mask, so land
    is filled with zero velocity rather than left absent. The client then
    bilinearly interpolates across the ocean->0 boundary, so coastal ocean
    velocities bleed onto the adjacent land and animated particles drift ashore.
    The coarsening in :func:`to_velocity_json` widens the bleed (the decimated
    coastline is offset by up to a coarse cell). Unlike the speed shading, which
    masks land with per-pixel alpha (:func:`to_speed_png`), the trails have no
    such mask. Accepted as a known cosmetic limitation; see plans/BACKLOG.md.
    """
    field = field.sortby("latitude", ascending=False).sortby(
        "longitude", ascending=True
    )
    data = np.nan_to_num(field.values, nan=0.0).astype(float)
    return {
        "header": _component_header(field, number, name),
        "data": data.ravel(order="C").tolist(),
    }


def _scale_for_animation(coarse: xr.Dataset) -> xr.Dataset:
    """Compress the velocity magnitude sub-linearly, keeping direction, so the
    slow eddies animate. ``m' = vref * (m/vref)**gamma`` => factor ``(m/vref)**
    (gamma-1)``; ``vref`` is the field's 99th-percentile speed (its fixed point)."""
    spd = np.hypot(coarse["uo"], coarse["vo"])
    ocean = np.where(spd.values > 0, spd.values, np.nan)
    vref = float(np.nanpercentile(ocean, 99))
    factor = xr.where(spd > 0, (spd / vref) ** (VELOCITY_GAMMA - 1.0), 0.0)
    return coarse.assign(uo=coarse["uo"] * factor, vo=coarse["vo"] * factor)


def to_velocity_json(field: xr.Dataset, stride: int = COARSEN_STRIDE) -> list[dict]:
    """Coarsened, magnitude-compressed leaflet-velocity ``[u, v]`` for the trails."""
    coarse = field.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )
    coarse = _scale_for_animation(coarse)
    return [
        _component(coarse["uo"], number=2, name="Eastward current"),
        _component(coarse["vo"], number=3, name="Northward current"),
    ]


# --- speed shading (Mercator-warped PNG) -----------------------------------

def _colorbar_stops(n: int = COLORBAR_STOPS) -> list[str]:
    """Hex stops sampled along the speed colour map, low -> high."""
    return [mcolors.to_hex(SPEED_CMAP(i / (n - 1))) for i in range(n)]


def to_speed_png(field: xr.Dataset) -> tuple[bytes, dict]:
    """Render |velocity| as a Mercator-warped RGBA PNG (cmocean ``speed``, clipped
    at the 99th percentile, land transparent) and return ``(png_bytes, meta)``."""
    f = field.sortby("latitude").sortby("longitude")  # both ascending
    lats = f["latitude"].values
    lons = f["longitude"].values
    speed = np.hypot(f["uo"].values, f["vo"].values)  # land NaN preserved
    vmax = float(np.nanpercentile(speed, SPEED_CLIP_PERCENTILE))

    def to_rgba(warped):
        rgba = SPEED_CMAP(np.clip(warped / vmax, 0.0, 1.0))
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    png, bounds = _raster.mercator_rgba_png(speed, lats, lons, to_rgba)
    meta = {
        "valid_time": valid_time(field),
        "bounds": bounds,
        "vmax": vmax,
        "units": "m/s",
        "colorbar": _colorbar_stops(),
    }
    return png, meta
