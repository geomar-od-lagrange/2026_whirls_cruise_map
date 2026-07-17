"""Tests for ``_clean.clean`` date parsing — a single malformed upstream
``date_UTC`` must not take down the whole ingest (issue #14).
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from whirls_cruise_map import _clean

_COLUMNS = [
    "D_number",
    "date_UTC",
    "Latitude",
    "Longitude",
    "U_speed_mps",
    "U_Dir_deg",
    "batteryState",
]


def _raw(*date_utc: str) -> pd.DataFrame:
    rows = [(f"D{i}", d, -37.0, 12.0, 0.3, 90.0, "A") for i, d in enumerate(date_utc)]
    return pd.DataFrame(rows, columns=_COLUMNS)


def test_full_timestamp_parses_as_before():
    out = _clean.clean(_raw("21-Jun-2026 11:26:09"))
    assert list(out["date_UTC"]) == [pd.Timestamp("2026-06-21 11:26:09", tz="UTC")]


def test_date_only_value_coerced_to_midnight_utc():
    """A date-only value (missing HH:MM:SS) lands at 00:00:00Z instead of
    aborting the build."""
    out = _clean.clean(_raw("11-Jul-2026"))
    assert list(out["date_UTC"]) == [pd.Timestamp("2026-07-11 00:00:00", tz="UTC")]


def test_mixed_full_and_date_only_all_survive():
    out = _clean.clean(_raw("21-Jun-2026 11:26:09", "11-Jul-2026"))
    assert list(out["date_UTC"]) == [
        pd.Timestamp("2026-06-21 11:26:09", tz="UTC"),
        pd.Timestamp("2026-07-11 00:00:00", tz="UTC"),
    ]


def test_unparseable_row_dropped_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger=_clean.__name__):
        out = _clean.clean(_raw("21-Jun-2026 11:26:09", "not-a-date"))
    assert list(out["date_UTC"]) == [pd.Timestamp("2026-06-21 11:26:09", tz="UTC")]
    assert "not-a-date" in caplog.text


def test_concat_snapshots_empty_raises_an_actionable_error():
    """ING-2: an empty snapshot glob (a valid zip whose internal folder was
    renamed/emptied) must fail with a cause-naming message, not the bare pandas
    'No objects to concatenate'."""
    with pytest.raises(ValueError, match="empty or was renamed"):
        _clean.concat_snapshots([])


def test_awaiting_is_clean_minus_tracks():
    """ING-3: `awaiting` is the drifters in `clean` absent from the already-computed
    `tracks` (no valid fix), without recomputing the sentinel filter itself."""
    clean = pd.DataFrame(
        [
            ("D0", pd.Timestamp("2026-07-01", tz="UTC"), -37.0, 12.0, 0.3, 90.0, "A"),
            ("D1", pd.Timestamp("2026-07-01", tz="UTC"), _clean.SENTINEL, _clean.SENTINEL, 0.0, 0.0, "A"),
        ],
        columns=_COLUMNS,
    )
    tracks = _clean.tracks(clean)
    assert _clean.awaiting(clean, tracks) == ["D1"]
