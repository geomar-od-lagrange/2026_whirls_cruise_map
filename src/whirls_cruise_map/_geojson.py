"""Turn the track DB into GeoJSON for the Leaflet map."""

from __future__ import annotations

import math

import pandas as pd

_EARTH_RADIUS_M = 6_371_000.0


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _point(row) -> tuple[float, float, pd.Timestamp]:
    """(Latitude, Longitude, time) for an ``itertuples`` row."""
    return (row.Latitude, row.Longitude, row.date_UTC)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(rlat2)
    x = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(
        rlat2
    ) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _segment_motion(prev_pt, cur_pt) -> tuple[float | None, float | None]:
    """Mean speed (m/s) and initial heading (deg true) over ``prev_pt`` ->
    ``cur_pt``. ``prev_pt`` is ``None`` at a track's first fix; heading is
    ``None`` when the two fixes coincide (bearing undefined)."""
    if prev_pt is None:
        return None, None
    dt = (cur_pt[2] - prev_pt[2]).total_seconds()
    if dt <= 0:
        return None, None
    dist = _haversine_m(prev_pt[0], prev_pt[1], cur_pt[0], cur_pt[1])
    heading = (
        _bearing_deg(prev_pt[0], prev_pt[1], cur_pt[0], cur_pt[1]) if dist > 0 else None
    )
    return dist / dt, heading


def _round(x, ndigits: int) -> float | None:
    """``round(x, ndigits)`` for a finite number, else ``None``. Collapses
    missing/``NaN``/``±Infinity`` to ``null`` so the JSON has no non-finite
    tokens (which ``JSON.parse`` rejects) and the popup renders a dash."""
    if x is None:
        return None
    x = float(x)
    return round(x, ndigits) if math.isfinite(x) else None


def _fix_record(row, prev_pt) -> dict:
    """One fix's popup payload: time, battery, the drifter's *reported* velocity
    (``U_speed_mps`` / ``U_Dir_deg``, carried through verbatim — unreliable
    pre-deployment, hence shown alongside the derived value, not instead of it),
    and the velocity *derived* from the ``prev_pt`` -> this-fix segment (mean
    speed m/s + heading deg)."""
    speed, heading = _segment_motion(prev_pt, _point(row))
    return {
        "date_UTC": row.date_UTC.isoformat(),
        "batteryState": row.batteryState if pd.notna(row.batteryState) else None,
        "U_speed_mps": _round(row.U_speed_mps, 4),
        "U_Dir_deg": _round(row.U_Dir_deg, 1),
        "derived_speed_mps": _round(speed, 4),
        "derived_heading_deg": _round(heading, 1),
    }


def latest_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one Point per drifter at its most-recent valid fix.

    Coordinates are [Longitude, Latitude]. Properties: ``D_number``, ``batch``,
    and the latest fix's :func:`_fix_record` payload (``date_UTC``,
    ``batteryState``, reported + derived velocity). The derived velocity is taken
    from the prior fix, so a single-fix drifter reports ``null`` there.
    """
    features = []
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        # Whole-row last fix (and the one before it for the derived velocity);
        # taking the row entire avoids the per-column mixing that .last() does.
        rows = list(group.itertuples(index=False))
        last = rows[-1]
        prev_pt = _point(rows[-2]) if len(rows) >= 2 else None
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [last.Longitude, last.Latitude],
                },
                "properties": {
                    "D_number": d_number,
                    "batch": last.batch,
                    **_fix_record(last, prev_pt),
                },
            }
        )
    return _feature_collection(features)


def tracks_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one LineString per drifter over its time-sorted fixes.

    Coordinates are [Longitude, Latitude] pairs in time order. A single-fix
    drifter cannot form a valid (>=2 point) LineString, so it is skipped here;
    it still appears in :func:`latest_geojson`. Properties: ``D_number``,
    ``batch``, ``n_fixes``, and ``fixes`` — a per-vertex list aligned with
    ``coordinates``, each a :func:`_fix_record` payload, so the client can draw a
    dot per intermediate fix carrying the same popup as the latest-position
    marker (its own time, battery, and reported + derived velocity).

    ``batch`` is the drifter's *latest* fix's batch — the same key
    :func:`latest_geojson` puts the marker under — so the client can toggle a
    drifter's track together with its marker even once a drifter's batch changes
    across its fixes (e.g. ``pre_deploy`` -> a deployment batch mid-track).
    """
    features = []
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        rows = list(group.itertuples(index=False))
        if len(rows) < 2:
            continue
        coords, fixes, prev_pt = [], [], None
        for row in rows:
            coords.append([row.Longitude, row.Latitude])
            fixes.append(_fix_record(row, prev_pt))
            prev_pt = _point(row)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "D_number": d_number,
                    "batch": rows[-1].batch,
                    "n_fixes": len(rows),
                    "fixes": fixes,
                },
            }
        )
    return _feature_collection(features)
