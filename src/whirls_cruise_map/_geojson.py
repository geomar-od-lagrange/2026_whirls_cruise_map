"""Turn the track DB into GeoJSON for the Leaflet map."""

from __future__ import annotations

import math
from typing import NamedTuple

import pandas as pd

from . import _geo
from ._clean import PRE_DEPLOY_BATCH
from ._forecast import _COORD_NDIGITS


class Point(NamedTuple):
    """One track fix as ``(lat, lon, time)`` — the single internal ordering the motion
    helpers (:func:`_segment_motion`, :func:`_bearing_deg`) and coordinate emitter
    consume. Built at every boundary — drifter rows via :func:`_point`, glider fix
    tuples via :func:`_glider_point` — so no call site hand-permutes raw tuple indices,
    where a transposed index would silently yield wrong speeds/headings or a
    lon/lat-swapped geometry (DER-1 / IDIOM-4)."""

    lat: float
    lon: float
    time: pd.Timestamp

# A glider track's leading fixes can be the launch vessel carrying it out to the
# deployment site. A Seaglider's own horizontal speed is ~0.25 m/s (0.1–0.4 m/s
# through water; up to ~1 m/s over ground with the current), while a ship steams
# at several m/s — so an inbound speed above this cleanly marks a still-aboard
# transit fix. Set in the wide gap between the two: above any glide+current ground
# speed here, well below ship transit (4+ m/s observed). See _drop_leading_transit.
GLIDER_TRANSIT_MPS = 2.0


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _coord(lon, lat) -> list[float]:
    """One ``[lon, lat]`` geometry vertex, cropped to the shared display bound
    (:data:`._forecast._COORD_NDIGITS`, 4 dp ~ 11 m — sub-pixel at the map's max
    zoom and at the GPS fix scatter), so no full-precision float tails ship."""
    return [round(float(lon), _COORD_NDIGITS), round(float(lat), _COORD_NDIGITS)]


def _point(row) -> Point:
    """A :class:`Point` for a drifter ``itertuples`` row (named columns, no ordering
    ambiguity)."""
    return Point(row.Latitude, row.Longitude, row.date_UTC)


def _glider_point(fix: tuple) -> Point:
    """A :class:`Point` from a glider ``(time, lat, lon)`` fix tuple (the shape
    :attr:`._gliders.Platform.fixes` holds) — the **one** place that ordering is
    named, so every glider call site goes through it instead of re-indexing
    ``fix[1]``/``fix[2]``/``fix[0]`` by hand."""
    return Point(fix[1], fix[2], fix[0])


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(rlat2)
    x = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(
        rlat2
    ) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _segment_motion(prev_pt: Point | None, cur_pt: Point) -> tuple[float | None, float | None]:
    """Mean speed (m/s) and initial heading (deg true) over ``prev_pt`` ->
    ``cur_pt`` (both :class:`Point`). ``prev_pt`` is ``None`` at a track's first fix;
    heading is ``None`` when the two fixes coincide (bearing undefined)."""
    if prev_pt is None:
        return None, None
    dt = (cur_pt.time - prev_pt.time).total_seconds()
    if dt <= 0:
        return None, None
    dist = _geo.haversine_m(prev_pt.lat, prev_pt.lon, cur_pt.lat, cur_pt.lon)
    heading = (
        _bearing_deg(prev_pt.lat, prev_pt.lon, cur_pt.lat, cur_pt.lon) if dist > 0 else None
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


def _glider_fix_record(pt: Point, prev_pt: Point | None) -> dict:
    """One glider fix's popup payload: time plus the velocity *derived* from the
    ``prev_pt`` -> this-fix segment. Gliders carry no reported velocity or
    battery, so — unlike :func:`_fix_record` — only the derived pair is emitted;
    the client shows a dash for the fields a glider lacks. ``pt``/``prev_pt`` are
    :class:`Point`."""
    speed, heading = _segment_motion(prev_pt, pt)
    return {
        "date_UTC": pt.time.isoformat(),
        "derived_speed_mps": _round(speed, 4),
        "derived_heading_deg": _round(heading, 1),
    }


def _drop_leading_transit(
    fixes: list, threshold: float = GLIDER_TRANSIT_MPS
) -> list:
    """Drop a glider track's leading vessel-transit fixes, returning the deployed
    remainder.

    Walk from the start while each fix's *inbound* speed exceeds ``threshold`` —
    the launch vessel carrying the glider — and keep from the first fix it reached
    at its own (sub-threshold) speed: its deployment. **Only this leading run is
    cut.** Once deployed, every later fix is kept unchanged, however fast — the map
    shows raw positions, and a post-deployment speed spike is treated as noise, not
    a re-truncation. The convention matches drifter deployment detection
    (:func:`_deploy.deployment_starts`): the drop point (last transit fix) is
    excluded, so the drawn track begins at the first free fix.

    A track with no leading transit (its first hop is already sub-threshold) is
    returned whole. One carried the whole way (every hop above threshold) returns
    empty — no free track yet, only the marker, exactly as a still-attached drifter.
    ``fixes`` is the ``(time, lat, lon)`` list; the first fix has no inbound speed
    and so is never transit on its own.
    """
    last_transit = 0
    for i in range(1, len(fixes)):
        speed, _ = _segment_motion(_glider_point(fixes[i - 1]), _glider_point(fixes[i]))
        if speed is not None and speed > threshold:
            last_transit = i
        else:
            break
    return fixes[last_transit + 1 :] if last_transit else fixes


def gliders_geojson(platforms: list) -> dict:
    """FeatureCollection for the glider platforms (see :mod:`._gliders`).

    Per platform: a ``Point`` at its most-recent fix and, when it has >=2 fixes,
    a ``LineString`` track. Coordinates are [Longitude, Latitude], cropped to the
    shared 4 dp display bound (:func:`_coord`). Properties
    carry ``id`` and ``type`` (``"xspar"`` / ``"seaglider"`` / ``"waveglider"`` /
    ``"float"``, keying
    the client's colour and label); the Point adds the latest :func:`_glider_fix_record`, the
    LineString a per-vertex ``fixes`` list aligned with ``coordinates`` (so the
    client draws a popup-bearing dot per fix, as it does for drifter tracks).

    The **Point** is always the raw latest fix. The **LineString** is the glider's
    *deployed* track only: its leading vessel-transit fixes are dropped
    (:func:`_drop_leading_transit`), so — like a truncated drifter — the first
    drawn fix is the first free one (its derived velocity blank, deriving from
    nothing). A glider still being carried out (no free track yet) has fewer than
    two deployed fixes and so draws only its marker.
    """
    features = []
    for p in platforms:
        raw = p.fixes
        last = _glider_point(raw[-1])
        prev = _glider_point(raw[-2]) if len(raw) >= 2 else None
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": _coord(last.lon, last.lat)},
                "properties": {
                    "id": p.id,
                    "type": p.type,
                    **_glider_fix_record(last, prev),
                },
            }
        )
        fixes = _drop_leading_transit(raw)
        if len(fixes) < 2:
            continue
        coords, fix_recs, prev_pt = [], [], None
        for f in fixes:
            pt = _glider_point(f)
            coords.append(_coord(pt.lon, pt.lat))
            fix_recs.append(_glider_fix_record(pt, prev_pt))
            prev_pt = pt
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "id": p.id,
                    "type": p.type,
                    "n_fixes": len(fixes),
                    "fixes": fix_recs,
                },
            }
        )
    return _feature_collection(features)


def latest_geojson(tracks: pd.DataFrame) -> dict:
    """FeatureCollection of one Point per drifter at its most-recent valid fix.

    Coordinates are [Longitude, Latitude], cropped to the shared 4 dp display
    bound (:func:`_coord`). Properties: ``D_number``, ``batch``,
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
                    "coordinates": _coord(last.Longitude, last.Latitude),
                },
                "properties": {
                    "D_number": d_number,
                    "batch": last.batch,
                    **_fix_record(last, prev_pt),
                },
            }
        )
    return _feature_collection(features)


def tracks_geojson(
    tracks: pd.DataFrame, deploy_starts: dict | None = None
) -> dict:
    """FeatureCollection of one LineString per drifter over its time-sorted fixes.

    Coordinates are [Longitude, Latitude] pairs in time order, cropped to the
    shared 4 dp display bound (:func:`_coord`). A single-fix
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

    ``deploy_starts`` (``{D_number: first-free-drift time}``, from
    :func:`_deploy.deployment_starts`) truncates a **deployed** drifter's track to
    its free drift: fixes before its start — the port/transit leg while still on
    the vessel — are dropped, so the line is the "true track" only. Derived
    velocity is then computed within the free track, so the first free fix derives
    from nothing (blank), which is correct — its real predecessor was a
    vessel-following fix. A start past the last fix drops the drifter (still
    attached, not yet freely drifting).

    **Pre-deployment drifters keep their full track** — they are still staging or
    aboard, with no free drift to isolate, and their whole path (port, on deck)
    is what a viewer wants to see. Truncation is therefore applied only to
    drifters in a deployment batch; a `pre_deploy` drifter is never truncated even
    if it briefly detached from the vessel.
    """
    deploy_starts = deploy_starts or {}
    features = []
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        rows = list(group.itertuples(index=False))
        start = deploy_starts.get(d_number)
        # Truncate only deployed drifters to their free drift; pre-deployment
        # drifters show their full track.
        if start is not None and rows[-1].batch != PRE_DEPLOY_BATCH:
            rows = [r for r in rows if r.date_UTC >= start]
        if len(rows) < 2:
            continue
        coords, fixes, prev_pt = [], [], None
        for row in rows:
            coords.append(_coord(row.Longitude, row.Latitude))
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
