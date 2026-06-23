"""Turn the track DB into GeoJSON for the Leaflet map."""
from __future__ import annotations

import pandas as pd


def latest_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one Point per drifter at its most-recent valid fix.

    Coordinates are [Longitude, Latitude]. Properties: ``D_number``,
    ``date_UTC`` (ISO 8601 string), ``batteryState``, ``batch``.
    """
    raise NotImplementedError


def tracks_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one LineString per drifter over its time-sorted fixes.

    Coordinates are [Longitude, Latitude] pairs in time order. Drifters with a
    single fix may be emitted as a one-point track or skipped. Properties:
    ``D_number``, ``batch``, ``n_fixes``.
    """
    raise NotImplementedError
