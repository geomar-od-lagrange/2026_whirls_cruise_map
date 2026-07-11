"""Tests for ``_clean.clean`` date parsing — a single malformed upstream
``date_UTC`` must not take down the whole ingest (issue #14).
"""
from __future__ import annotations

import logging

import pandas as pd

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
