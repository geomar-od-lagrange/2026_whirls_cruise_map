"""Parsing a WHIRLS float CSV into one Platform per float
(_gliders.parse_float_source).

The floats come in two CSV schemas. In `mr_float_*` files the identity lives in a
`filename` column (not the file name), so the parser groups by the filename's
leading id, maps it to a label, and time-sorts each float's fixes — a
per-institution file is normally one float, but grouping stays correct even if a
file interleaves several. In `uvp_float_<id>_locations` files there is no
`filename` column and the time column is `utc_time`; identity comes from the file
name instead. Both schemas are exercised here.
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


def test_aggregate_without_filename_or_uvp_name_yields_nothing():
    # The aggregate floats_track has neither a `filename` column nor a UVP file
    # name, so there is no way to separate its interleaved floats; skip rather
    # than emit one merged zig-zag track.
    src = Source(
        "floats_track",
        "float",
        "time,latitude,longitude\n2026-07-01 14:11:33,-37.43,11.62\n",
    )
    assert parse_float_source(src) == []


# A UVP float file: no `filename` column, `utc_time` (offset-aware) time column,
# and identity in the file name (`uvp_float_6596_locations` -> `6596`).
UVP = Source(
    "uvp_float_6596_locations",
    "float",
    "profile,utc_time,latitude,longitude\n"
    "1,2026-07-02 08:15:00+00:00,-37.10,11.80\n"
    "3,2026-07-01 08:15:00+00:00,-37.20,11.75\n"
    "2,2026-07-03 08:15:00+00:00,-37.05,11.83\n",
)


def test_uvp_float_identity_from_file_name():
    (platform,) = parse_float_source(UVP)
    # No mapping for 6596, so it falls back to its raw id.
    assert platform.id == "6596"
    assert platform.type == "float"
    assert len(platform.fixes) == 3


def test_uvp_utc_time_column_parsed_and_sorted():
    (platform,) = parse_float_source(UVP)
    times = [t for (t, _lat, _lon) in platform.fixes]
    assert times == sorted(times)
    # utc_time carries an explicit +00:00 offset, normalised to tz-aware UTC.
    assert times[0] == datetime(2026, 7, 1, 8, 15, tzinfo=timezone.utc)


def test_empty_or_headerless_input_is_empty():
    assert parse_float_source(Source("floats_track", "float", "")) == []
