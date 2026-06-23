"""Fetch today's CMEMS surface currents as a leaflet-velocity grid.

Canonical cruise study region (2026_whirls_cruise_prep
``archetypes/notebooks/001_study_region.py``): 0..25 E, -45..-25 N. Widened by
10 deg on every side and expressed in -180..180 for the web map.
"""
from __future__ import annotations

BBOX = {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0}


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
    raise NotImplementedError
