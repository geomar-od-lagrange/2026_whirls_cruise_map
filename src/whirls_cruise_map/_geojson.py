"""Turn the track DB into GeoJSON for the Leaflet map."""
from __future__ import annotations

import pandas as pd


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def latest_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one Point per drifter at its most-recent valid fix.

    Coordinates are [Longitude, Latitude]. Properties: ``D_number``,
    ``date_UTC`` (ISO 8601 string), ``batteryState``, ``batch``.
    """
    # tail(1), not last(): .last() fills each column's last *non-null* value
    # independently, which would mix fields across fixes once any field is null.
    latest = tracks.sort_values("date_UTC").groupby("D_number").tail(1)
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["Longitude"], row["Latitude"]],
            },
            "properties": {
                "D_number": row["D_number"],
                "date_UTC": row["date_UTC"].isoformat(),
                "batteryState": row["batteryState"],
                "batch": row["batch"],
            },
        }
        for _, row in latest.iterrows()
    ]
    return _feature_collection(features)


def tracks_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one LineString per drifter over its time-sorted fixes.

    Coordinates are [Longitude, Latitude] pairs in time order. A single-fix
    drifter cannot form a valid (>=2 point) LineString, so it is skipped here;
    it still appears in :func:`latest_geojson`. Properties: ``D_number``,
    ``batch``, ``n_fixes``.
    """
    features = []
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        if len(group) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": group[["Longitude", "Latitude"]].values.tolist(),
                },
                "properties": {
                    "D_number": d_number,
                    "batch": group["batch"].iloc[0],
                    "n_fixes": len(group),
                },
            }
        )
    return _feature_collection(features)
