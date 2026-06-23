"""Fetch today's CMEMS surface currents as a leaflet-velocity grid.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N. Widened by
10 deg on every side and expressed in -180..180 for the web map.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import copernicusmarine
import numpy as np
import xarray as xr

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}

DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"

# Coarsen the native 1/12-deg grid by this factor for a lighter web payload. The
# particle animation does not need full model resolution at this map scale;
# stride 3 (~1/4 deg) keeps the flow legible while shrinking the JSON ~9x.
COARSEN_STRIDE = 3


def _component_header(field: xr.DataArray, number: int, name: str) -> dict:
    """Build the leaflet-velocity header for one component from the field's grid.

    ``field`` must already be ordered latitude-descending, longitude-ascending.
    """
    lats = field["latitude"].values
    lons = field["longitude"].values
    valid_time = np.datetime_as_string(field["time"].values, unit="s") + "Z"
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
        "refTime": valid_time,
        "forecastTime": 0,
    }


def _component(field: xr.DataArray, number: int, name: str) -> dict:
    """Turn a 2-D ``(latitude, longitude)`` field into a leaflet-velocity object.

    Latitude is forced descending (north first) and longitude ascending, so the
    row-major flattened ``data`` starts at the north-west corner and scans
    west->east within each row, rows north->south. Land NaNs become 0.
    """
    field = field.sortby("latitude", ascending=False).sortby(
        "longitude", ascending=True
    )
    data = np.nan_to_num(field.values, nan=0.0).astype(float)
    return {
        "header": _component_header(field, number, name),
        "data": data.ravel(order="C").tolist(),
    }


def fetch_currents(bbox: dict = BBOX) -> list[dict]:
    """Subset CMEMS global analysis/forecast surface ``uo``/``vo`` over ``bbox``
    for the nearest time to now, and return leaflet-velocity's two-component
    JSON: ``[u_object, v_object]``, each ``{"header": {...}, "data": [...]}``.

    The header carries ``nx``, ``ny``, ``lo1`` (west), ``la1`` (north), ``lo2``
    (east), ``la2`` (south), ``dx``, ``dy``, ``refTime``, and
    ``parameterCategory``/``parameterNumber`` (u: 2, v: 3). ``data`` is a flat
    row-major array starting at the north-west corner, latitude descending and
    longitude ascending. Relies on the local copernicusmarine login.
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
            minimum_depth=0.0,
            maximum_depth=1.0,
            start_datetime=now,
            end_datetime=now,
            output_filename=str(out),
            overwrite=True,
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()

    # Reduce to a single 2-D field: surface depth and the time nearest now.
    if "depth" in ds.dims:
        ds = ds.isel(depth=0)
    if "time" in ds.dims:
        ds = ds.sel(time=np.datetime64(now.replace(tzinfo=None)), method="nearest")
    ds = ds.squeeze(drop=True)

    ds = ds.isel(
        latitude=slice(None, None, COARSEN_STRIDE),
        longitude=slice(None, None, COARSEN_STRIDE),
    )

    return [
        _component(ds["uo"], number=2, name="Eastward current"),
        _component(ds["vo"], number=3, name="Northward current"),
    ]
