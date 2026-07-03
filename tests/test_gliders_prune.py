"""Leading vessel-transit prune for glider tracks (_geojson._drop_leading_transit).

The rule: drop only the *leading* run of ship-speed fixes; once a glider first
reaches its own (sub-threshold) speed it is deployed, and every later fix is kept
unchanged — a post-deployment speed spike is noise, not a re-truncation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from whirls_cruise_map._geojson import (
    GLIDER_TRANSIT_MPS,
    _drop_leading_transit,
    gliders_geojson,
)
from whirls_cruise_map._gliders import Platform

BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _fix(minutes: float, lon: float, lat: float = -37.0):
    """A (time, lat, lon) fix `minutes` after BASE. Fixes are spaced in longitude;
    a big jump over a short interval is ship-fast, a tiny jump over a long interval
    is glider-slow — the two straddle GLIDER_TRANSIT_MPS with wide margin."""
    return (BASE + timedelta(minutes=minutes), lat, lon)


# A big lon step in one minute is many m/s (transit); a tiny step over an hour is
# a fraction of a m/s (glider). Exact values don't matter — only which side of the
# threshold they land on.
def _sanity_threshold():
    assert GLIDER_TRANSIT_MPS == 2.0


def test_leading_transit_dropped_and_post_deploy_spike_kept():
    fixes = [
        _fix(0, 12.00),    # 0: start, aboard
        _fix(1, 12.10),    # 1: +0.10deg/min -> transit
        _fix(2, 12.20),    # 2: +0.10deg/min -> transit  (last transit fix)
        _fix(62, 12.201),  # 3: +0.001deg/hr -> deployed  (first free fix)
        _fix(122, 12.202), # 4: glider-slow
        _fix(123, 12.30),  # 5: +0.098deg/min -> FAST spike AFTER deploy (noise)
        _fix(183, 12.301), # 6: glider-slow
    ]
    kept = _drop_leading_transit(fixes)
    # Keep from the first free fix (index 3); the drop point (index 2) is excluded.
    assert kept == fixes[3:]
    # The post-deployment fast spike (index 5) is retained, not re-pruned.
    assert fixes[5] in kept


def test_no_leading_transit_keeps_whole_track():
    fixes = [_fix(0, 12.00), _fix(60, 12.001), _fix(120, 12.002)]
    assert _drop_leading_transit(fixes) == fixes


def test_carried_all_the_way_yields_empty():
    fixes = [_fix(0, 12.0), _fix(1, 12.1), _fix(2, 12.2), _fix(3, 12.3)]
    assert _drop_leading_transit(fixes) == []


def test_single_fix_is_untouched():
    fixes = [_fix(0, 12.0)]
    assert _drop_leading_transit(fixes) == fixes


def test_gliders_geojson_uses_deployed_track_but_raw_latest_point():
    fixes = [
        _fix(0, 12.00),
        _fix(1, 12.10),    # transit
        _fix(2, 12.20),    # transit
        _fix(62, 12.201),  # first free
        _fix(122, 12.202),
        _fix(182, 12.203),
    ]
    fc = gliders_geojson([Platform("sg999", "seaglider", fixes)])
    point = next(f for f in fc["features"] if f["geometry"]["type"] == "Point")
    line = next(f for f in fc["features"] if f["geometry"]["type"] == "LineString")

    # Point is the raw latest fix (unaffected by the leading prune).
    assert point["geometry"]["coordinates"] == [fixes[-1][2], fixes[-1][1]]

    # LineString is the deployed remainder: starts at the first free fix.
    assert line["properties"]["n_fixes"] == 3
    assert line["geometry"]["coordinates"][0] == [fixes[3][2], fixes[3][1]]
    # First drawn fix derives its velocity from nothing (blank), like a truncated
    # drifter's first free fix.
    assert line["properties"]["fixes"][0]["derived_speed_mps"] is None
