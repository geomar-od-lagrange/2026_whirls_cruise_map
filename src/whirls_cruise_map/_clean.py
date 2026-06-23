"""Parse drifter snapshot CSVs into a tidy track DB.

Snapshot CSV columns:
    D_number, date_UTC, Latitude, Longitude, U_speed_mps, U_Dir_deg, batteryState

The canonical identity of a fix is (D_number, date_UTC); the same fix may repeat
across snapshots, so we de-duplicate on that pair. No assumption is made about
how often drifters report.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

SENTINEL = -99999  # Latitude/Longitude value meaning "no fix yet"
DATE_FORMAT = "%d-%b-%Y %H:%M:%S"  # e.g. "21-Jun-2026 11:26:09" (UTC)
PRE_DEPLOY_BATCH = "pre_deploy"


def load_raw(csv_paths: list[Path]) -> pd.DataFrame:
    """Concatenate all snapshots and de-duplicate on (D_number, date_UTC).

    Parse ``date_UTC`` to tz-aware UTC datetimes. Keep sentinel (-99999) rows so
    :func:`awaiting` can see drifters that have never reported. Add a ``batch``
    column set to ``PRE_DEPLOY_BATCH``. ``U_speed_mps`` / ``U_Dir_deg`` are
    carried through unmodified but not relied upon (they may be invalid before
    deployment).
    """
    raw = pd.concat(
        (pd.read_csv(path) for path in csv_paths), ignore_index=True
    )
    raw["date_UTC"] = pd.to_datetime(
        raw["date_UTC"], format=DATE_FORMAT, utc=True
    )
    raw = raw.drop_duplicates(subset=["D_number", "date_UTC"], ignore_index=True)
    raw["batch"] = PRE_DEPLOY_BATCH
    return raw


def tracks(raw: pd.DataFrame) -> pd.DataFrame:
    """Return only valid fixes (drop rows where Latitude or Longitude == SENTINEL),
    sorted by D_number then date_UTC."""
    valid = raw[(raw["Latitude"] != SENTINEL) & (raw["Longitude"] != SENTINEL)]
    return valid.sort_values(["D_number", "date_UTC"], ignore_index=True)


def awaiting(raw: pd.DataFrame) -> list[str]:
    """D_numbers that have no valid fix in any snapshot, sorted."""
    fixed = set(tracks(raw)["D_number"])
    return sorted(set(raw["D_number"]) - fixed)
