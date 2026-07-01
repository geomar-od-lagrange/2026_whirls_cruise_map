"""Parse drifter snapshot CSVs into a tidy track DB.

Snapshot CSV columns:
    D_number, date_UTC, Latitude, Longitude, U_speed_mps, U_Dir_deg, batteryState

The canonical identity of a fix is (D_number, date_UTC); the same fix may repeat
across snapshots, so we de-duplicate on that pair. No assumption is made about
how often drifters report.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SENTINEL = -99999  # Latitude/Longitude value meaning "no fix yet"
DATE_FORMAT = "%d-%b-%Y %H:%M:%S"  # e.g. "21-Jun-2026 11:26:09" (UTC)
PRE_DEPLOY_BATCH = "pre_deploy"
DEPLOYMENTS_PATH = Path(__file__).with_name("deployments.json")


def load_deployments(path: Path = DEPLOYMENTS_PATH) -> dict[str, str]:
    """Invert the deployment roster into a ``D_number -> batch`` map.

    The roster (``deployments.json``, ``batch -> [D_number, ...]``) is
    operational cruise data, curated per deployment as drifters go overboard: a
    drifter joins a deployment batch once it is confirmed in the water and
    drifting freely. Drifters absent from the roster stay
    :data:`PRE_DEPLOY_BATCH`.
    """
    roster: dict[str, list[str]] = json.loads(path.read_text())
    return {
        d_number: batch
        for batch, d_numbers in roster.items()
        for d_number in d_numbers
    }


def load_raw(csv_paths: list[Path]) -> pd.DataFrame:
    """Concatenate all snapshots and de-duplicate on (D_number, date_UTC).

    Parse ``date_UTC`` to tz-aware UTC datetimes. Keep sentinel (-99999) rows so
    :func:`awaiting` can see drifters that have never reported. Add a ``batch``
    column from the deployment roster (:func:`load_deployments`), defaulting to
    :data:`PRE_DEPLOY_BATCH` for drifters not yet rostered. ``U_speed_mps`` /
    ``U_Dir_deg`` are carried through unmodified but not relied upon (they may be
    invalid before deployment).
    """
    raw = pd.concat(
        (pd.read_csv(path) for path in csv_paths), ignore_index=True
    )
    raw["date_UTC"] = pd.to_datetime(
        raw["date_UTC"], format=DATE_FORMAT, utc=True
    )
    raw = raw.drop_duplicates(subset=["D_number", "date_UTC"], ignore_index=True)
    raw["batch"] = raw["D_number"].map(load_deployments()).fillna(PRE_DEPLOY_BATCH)
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
