"""Parsing a WHIRLS float CSV into one Platform per float
(_gliders.parse_float_source).

The float's identity lives in the `filename` column (not the file name), so the
parser groups by the filename's leading id, maps it to a label, and time-sorts
each float's fixes. A per-institution file is normally one float, but grouping
stays correct even if a file interleaves several — the property exercised here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from whirls_cruise_map._gliders import Source, parse_float_source

# Two floats interleaved in one CSV — 65a0 (UGOT) and 6594 (SOTON) — with rows
# deliberately out of per-float time order, so both the group-by-id split and the
# per-float time-sort are exercised.
INTERLEAVED = Source(
    "mr_float_positions",
    "float",
    "time,latitude,longitude,filename\n"
    "2026-07-01 14:11:33,-37.43,11.62,65a0_007_00_technical.txt\n"
    "2026-07-03 10:48:43,-38.47,11.55,6594_014_00_technical.txt\n"
    "2026-07-01 17:20:50,-37.42,11.60,65a0_007_01_technical.txt\n"
    "2026-07-03 13:49:58,-38.43,11.61,6594_014_01_technical.txt\n",
)


def _by_id(platforms):
    return {p.id: p for p in platforms}


def test_splits_into_one_platform_per_float_with_labels():
    platforms = parse_float_source(INTERLEAVED)
    byid = _by_id(platforms)
    # Grouped by the filename's leading id, mapped to the operational labels.
    assert set(byid) == {"UGOT", "SOTON"}
    assert all(p.type == "float" for p in platforms)
    assert len(byid["UGOT"].fixes) == 2
    assert len(byid["SOTON"].fixes) == 2


def test_fixes_are_time_sorted_and_utc():
    ugot = _by_id(parse_float_source(INTERLEAVED))["UGOT"]
    times = [t for (t, _lat, _lon) in ugot.fixes]
    assert times == sorted(times)
    assert times[0] == datetime(2026, 7, 1, 14, 11, 33, tzinfo=timezone.utc)
    # The interleaved 6594 rows did not leak into the 65a0 float.
    assert all(lat < -37.0 and lat > -38.0 for (_t, lat, _lon) in ugot.fixes)


def test_unmapped_float_id_falls_back_to_itself():
    src = Source(
        "floats_track",
        "float",
        "time,latitude,longitude,filename\n"
        "2026-07-01 14:11:33,-37.43,11.62,7abc_001_00_technical.txt\n",
    )
    (platform,) = parse_float_source(src)
    assert platform.id == "7abc"
    assert platform.type == "float"


def test_missing_filename_column_yields_nothing():
    # Without the identity column there is no way to separate floats; skip rather
    # than emit one merged zig-zag track.
    src = Source(
        "floats_track",
        "float",
        "time,latitude,longitude\n2026-07-01 14:11:33,-37.43,11.62\n",
    )
    assert parse_float_source(src) == []


def test_empty_or_headerless_input_is_empty():
    assert parse_float_source(Source("floats_track", "float", "")) == []
