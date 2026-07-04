"""Round-trip tests for the ``/data`` seam (``_data``): ingest writes cleaned
CSVs; derive reads them back into the exact in-memory shapes the derive
consumers (``_geojson``, ``_forecast``) expect.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from whirls_cruise_map import _data, build
from whirls_cruise_map._clean import PRE_DEPLOY_BATCH
from whirls_cruise_map._gliders import Platform


def _ts(*args) -> pd.Timestamp:
    return pd.Timestamp(datetime(*args, tzinfo=timezone.utc))


def _tracks_df() -> pd.DataFrame:
    """Three drifters: one multi-fix in a real batch, one single-fix in
    ``pre_deploy``, and one multi-fix in a different real batch."""
    rows = [
        # D1: multi-fix, deployment_1
        ("D1", _ts(2026, 7, 1, 0, 0, 0), -37.0, 12.0, 0.3, 90.0, "A", "deployment_1"),
        ("D1", _ts(2026, 7, 1, 6, 0, 0), -37.1, 12.1, 0.4, 95.0, "A", "deployment_1"),
        ("D1", _ts(2026, 7, 1, 12, 0, 0), -37.2, 12.2, 0.5, 100.0, "A", "deployment_1"),
        # D2: single-fix, pre_deploy
        ("D2", _ts(2026, 7, 2, 0, 0, 0), -36.0, 11.0, 0.1, 10.0, "G", "pre_deploy"),
        # D3: multi-fix, deployment_2
        ("D3", _ts(2026, 7, 3, 0, 0, 0), -38.0, 13.0, 0.2, 200.0, "F", "deployment_2"),
        ("D3", _ts(2026, 7, 3, 6, 0, 0), -38.1, 13.1, 0.25, 205.0, "F", "deployment_2"),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "D_number",
            "date_UTC",
            "Latitude",
            "Longitude",
            "U_speed_mps",
            "U_Dir_deg",
            "batteryState",
            "batch",
        ],
    )


def _no_tmp_files(data_dir) -> bool:
    return not list(data_dir.rglob("*.tmp"))


# --------------------------------------------------------------------------- #
# 1. drifter round-trip (incl. batch join through platforms.csv)
# --------------------------------------------------------------------------- #
def test_drifters_round_trip(tmp_path):
    tracks = _tracks_df()
    records = build._platform_records(
        tracks, awaiting=[], deploy_starts={}, gliders=[], md_track=[], agulhas=[]
    )

    _data.write_drifters(tmp_path, tracks)
    _data.write_platforms(tmp_path, records)

    out = _data.read_drifters(tmp_path)

    assert list(out.columns) == [
        "D_number",
        "date_UTC",
        "Latitude",
        "Longitude",
        "U_speed_mps",
        "U_Dir_deg",
        "batteryState",
        "batch",
    ]
    assert len(out) == len(tracks)

    # tz-aware UTC datetimes.
    assert isinstance(out["date_UTC"].dtype, pd.DatetimeTZDtype)
    assert str(out["date_UTC"].dt.tz) == "UTC"

    # Sort order: D_number then date_UTC.
    assert list(out["D_number"]) == ["D1", "D1", "D1", "D2", "D3", "D3"]
    assert out.groupby("D_number")["date_UTC"].apply(lambda s: s.is_monotonic_increasing).all()

    # Batch preserved per platform, joined from platforms.csv.
    batch_by_platform = out.groupby("D_number")["batch"].unique()
    assert batch_by_platform["D1"].tolist() == ["deployment_1"]
    assert batch_by_platform["D2"].tolist() == ["pre_deploy"]
    assert batch_by_platform["D3"].tolist() == ["deployment_2"]

    # Values round-trip for one row.
    d1_first = out[out["D_number"] == "D1"].iloc[0]
    assert d1_first["Latitude"] == pytest.approx(-37.0)
    assert d1_first["Longitude"] == pytest.approx(12.0)
    assert d1_first["U_speed_mps"] == pytest.approx(0.3)
    assert d1_first["U_Dir_deg"] == pytest.approx(90.0)
    assert d1_first["batteryState"] == "A"
    assert d1_first["date_UTC"] == _ts(2026, 7, 1, 0, 0, 0)

    assert _no_tmp_files(tmp_path)


def test_drifters_batch_falls_back_to_roster_without_platforms_csv(tmp_path):
    """No platforms.csv written -> batch comes from the package roster / falls
    back to PRE_DEPLOY_BATCH (a drifter unknown to the roster)."""
    tracks = _tracks_df()
    _data.write_drifters(tmp_path, tracks)

    out = _data.read_drifters(tmp_path)
    # None of D1/D2/D3 are in the real deployment roster, so all fall back.
    assert set(out["batch"].unique()) == {PRE_DEPLOY_BATCH}


# --------------------------------------------------------------------------- #
# 2. read_deploy_starts
# --------------------------------------------------------------------------- #
def test_read_deploy_starts_round_trip(tmp_path):
    tracks = _tracks_df()
    deploy_starts = {"D1": _ts(2026, 7, 1, 3, 0, 0)}
    records = build._platform_records(
        tracks, awaiting=[], deploy_starts=deploy_starts, gliders=[], md_track=[], agulhas=[]
    )
    _data.write_platforms(tmp_path, records)

    out = _data.read_deploy_starts(tmp_path)

    assert set(out) == {"D1"}
    assert out["D1"] == _ts(2026, 7, 1, 3, 0, 0)
    assert out["D1"].tzinfo is not None


def test_read_deploy_starts_missing_file_is_empty(tmp_path):
    assert _data.read_deploy_starts(tmp_path) == {}


# --------------------------------------------------------------------------- #
# 3. read_awaiting
# --------------------------------------------------------------------------- #
def test_read_awaiting_round_trip(tmp_path):
    tracks = _tracks_df()
    awaiting = ["D9", "D5"]
    records = build._platform_records(
        tracks, awaiting=awaiting, deploy_starts={}, gliders=[], md_track=[], agulhas=[]
    )
    _data.write_platforms(tmp_path, records)

    out = _data.read_awaiting(tmp_path)

    # Sorted, and only the n_fixes==0 drifters -- not D1/D2/D3, which have fixes.
    assert out == sorted(awaiting)
    assert "D1" not in out
    assert "D2" not in out
    assert "D3" not in out


# --------------------------------------------------------------------------- #
# 4. glider round-trip
# --------------------------------------------------------------------------- #
def test_gliders_round_trip(tmp_path):
    xspar_fixes = [
        (datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc), -37.5, 12.5),
        (datetime(2026, 7, 1, 6, 0, 0, tzinfo=timezone.utc), -37.4, 12.4),
    ]
    seaglider_fixes = [
        (datetime(2026, 7, 2, 0, 0, 0, tzinfo=timezone.utc), -38.0, 13.0),
    ]
    platforms = [
        Platform("xspar1", "xspar", xspar_fixes),
        Platform("sg001", "seaglider", seaglider_fixes),
    ]

    _data.write_gliders(tmp_path, platforms)
    out = _data.read_gliders(tmp_path)

    assert {p.id for p in out} == {"xspar1", "sg001"}
    by_id = {p.id: p for p in out}

    xspar_out = by_id["xspar1"]
    assert xspar_out.type == "xspar"
    # Time-sorted, unlike the input order.
    times = [f[0] for f in xspar_out.fixes]
    assert times == sorted(times)
    assert all(t.tzinfo is not None for t in times)
    assert xspar_out.fixes[0][1] == pytest.approx(-37.4)
    assert xspar_out.fixes[0][2] == pytest.approx(12.4)
    assert xspar_out.fixes[1][1] == pytest.approx(-37.5)

    sg_out = by_id["sg001"]
    assert sg_out.type == "seaglider"
    assert len(sg_out.fixes) == 1
    assert sg_out.fixes[0][1] == pytest.approx(-38.0)
    assert sg_out.fixes[0][2] == pytest.approx(13.0)

    assert _no_tmp_files(tmp_path)


# --------------------------------------------------------------------------- #
# 5. Agulhas round-trip
# --------------------------------------------------------------------------- #
def test_agulhas_round_trip(tmp_path):
    positions = [
        {
            "date": "2026-07-01T00:00:00Z",
            "lat": -37.0,
            "lon": 12.0,
            "speed_kn": 12.3,
            "course_deg": 90.0,
            "status": "underway",
            "area": "Cape Basin",
        },
        {
            "date": "2026-07-01T06:00:00Z",
            "lat": -37.1,
            "lon": 12.1,
            "speed_kn": None,
            "course_deg": 0.0,
            "status": None,
            "area": None,
        },
    ]

    _data.write_ship_agulhas(tmp_path, positions)
    out = _data.read_agulhas(tmp_path)

    assert out == positions
    assert _no_tmp_files(tmp_path)


# --------------------------------------------------------------------------- #
# 6. missing-file tolerance
# --------------------------------------------------------------------------- #
def test_reads_on_empty_dir_return_empty(tmp_path):
    out = _data.read_drifters(tmp_path)
    assert out.empty
    assert list(out.columns) == [
        "D_number",
        "date_UTC",
        "Latitude",
        "Longitude",
        "U_speed_mps",
        "U_Dir_deg",
        "batteryState",
        "batch",
    ]

    assert _data.read_gliders(tmp_path) == []
    assert _data.read_awaiting(tmp_path) == []
    assert _data.read_deploy_starts(tmp_path) == {}
    assert _data.read_agulhas(tmp_path) == []


# --------------------------------------------------------------------------- #
# 7. atomicity
# --------------------------------------------------------------------------- #
def test_writes_leave_no_tmp_files(tmp_path):
    tracks = _tracks_df()
    records = build._platform_records(
        tracks, awaiting=["D9"], deploy_starts={}, gliders=[], md_track=[], agulhas=[]
    )
    _data.write_drifters(tmp_path, tracks)
    _data.write_platforms(tmp_path, records)
    _data.write_gliders(tmp_path, [Platform("xspar1", "xspar", [
        (datetime(2026, 7, 1, tzinfo=timezone.utc), -37.0, 12.0)
    ])])
    _data.write_ship_agulhas(tmp_path, [
        {
            "date": "2026-07-01T00:00:00Z",
            "lat": -37.0,
            "lon": 12.0,
            "speed_kn": 1.0,
            "course_deg": 2.0,
            "status": "underway",
            "area": "x",
        }
    ])
    _data.write_manifest(tmp_path, [{"name": "drifters.csv"}], "2026-07-04T00:00:00Z")

    assert _no_tmp_files(tmp_path)


# --------------------------------------------------------------------------- #
# 8. manifest
# --------------------------------------------------------------------------- #
def test_write_manifest_is_valid_json(tmp_path):
    entries = [
        {"name": "drifters.csv", "kind": "cleaned", "source": "http://x", "rows": 3, "columns": ["a"]}
    ]
    _data.write_manifest(tmp_path, entries, "2026-07-04T12:00:00Z")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["built_at"] == "2026-07-04T12:00:00Z"
    assert manifest["files"] == entries


# --------------------------------------------------------------------------- #
# 9. browsable /data index (GitLab Pages / nginx don't autoindex)
# --------------------------------------------------------------------------- #
def test_write_index_lists_files(tmp_path):
    entry = _data.write_drifters(tmp_path, _tracks_df())  # a real file for the size
    entries = [
        entry,
        {"name": "raw/agulhas_ii.csv", "kind": "raw", "source": "http://ipsl/agulhas"},
    ]
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "agulhas_ii.csv").write_text("reported_at,lat,lon\n")

    _data.write_index(tmp_path, entries, "2026-07-04T12:00:00Z")

    html = (tmp_path / "index.html").read_text()
    # File links present, grouped, with the build stamp interpolated…
    assert '<a href="drifters.csv">drifters.csv</a>' in html
    assert '<a href="raw/agulhas_ii.csv">raw/agulhas_ii.csv</a>' in html
    assert "Built 2026-07-04T12:00:00Z" in html
    # …and the CSS braces survived (regression: must use str.replace, not
    # str.format, which would choke on the literal `{}` in the stylesheet).
    assert "__BUILT_AT__" not in html and "{built_at}" not in html
    assert "color-scheme" in html
    assert _no_tmp_files(tmp_path)
