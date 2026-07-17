"""Parse drifter snapshot CSVs into a tidy track DB.

Snapshot CSV columns:
    D_number, date_UTC, Latitude, Longitude, U_speed_mps, U_Dir_deg, batteryState

The canonical identity of a fix is (D_number, date_UTC); the same fix may repeat
across snapshots, so we de-duplicate on that pair. No assumption is made about
how often drifters report.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

_log = logging.getLogger(__name__)

SENTINEL = -99999  # Latitude/Longitude value meaning "no fix yet"
DATE_FORMAT = "%d-%b-%Y %H:%M:%S"  # e.g. "21-Jun-2026 11:26:09" (UTC)
DATE_ONLY_FORMAT = "%d-%b-%Y"  # upstream sometimes drops the time component
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


def concat_snapshots(csv_paths: list[Path]) -> pd.DataFrame:
    """Concatenate every snapshot CSV verbatim into one frame — no parsing,
    de-duplication, or filtering.

    This is the *raw* drifter table, published at ``data/raw/drifters_raw.csv``
    (see :mod:`._data`): it keeps duplicate fixes (the same fix recurs across
    snapshots), sentinel (-99999) rows, and the source ``date_UTC`` strings
    unchanged, so :func:`clean` can be audited against exactly what it consumed.
    """
    if not csv_paths:
        # A valid share zip whose internal folder was renamed/emptied yields no CSVs
        # (an upstream layout change, not a transient fetch failure). `pd.concat(())`
        # would raise a bare "No objects to concatenate"; name the real cause instead
        # so the failure is actionable (ING-2).
        raise ValueError(
            "no drifter snapshot CSVs found — the share zip's internal folder is "
            "empty or was renamed (upstream layout change)"
        )
    return pd.concat((pd.read_csv(path) for path in csv_paths), ignore_index=True)


def _parse_dates(raw_dates: pd.Series) -> pd.Series:
    """Parse ``date_UTC`` to tz-aware UTC, tolerating a missing time component.

    Values matching the full :data:`DATE_FORMAT` (``date + HH:MM:SS``) parse
    directly. Upstream occasionally emits a **date-only** value (e.g.
    ``"11-Jul-2026"``); those are parsed with :data:`DATE_ONLY_FORMAT` and land
    at ``00:00:00Z`` rather than aborting the whole ingest. Anything still
    unparseable becomes ``NaT`` and is logged, so the caller can drop it — one
    malformed upstream row must not take down every build (see issue #14).
    """
    parsed = pd.to_datetime(raw_dates, format=DATE_FORMAT, utc=True, errors="coerce")
    date_only = parsed.isna() & raw_dates.notna()
    if date_only.any():
        parsed.loc[date_only] = pd.to_datetime(
            raw_dates[date_only], format=DATE_ONLY_FORMAT, utc=True, errors="coerce"
        )
    unparseable = parsed.isna() & raw_dates.notna()
    if unparseable.any():
        bad = sorted(set(raw_dates[unparseable].astype(str)))
        _log.warning(
            "Dropping %d drifter row(s) with unparseable date_UTC: %s",
            int(unparseable.sum()),
            ", ".join(bad),
        )
    return parsed


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """De-duplicate the concatenated snapshots into the cleaned drifter table.

    Parse ``date_UTC`` to tz-aware UTC datetimes (tolerating date-only values and
    dropping unparseable ones, see :func:`_parse_dates`); the canonical identity
    of a fix is ``(D_number, date_UTC)``, so drop duplicates on that pair. Keep
    sentinel (-99999) rows so :func:`awaiting` can see drifters that have never
    reported. Force ``D_number`` to string so it matches the deployment roster's
    (JSON, hence string) keys, then add a ``batch`` column from
    :func:`load_deployments`, defaulting to :data:`PRE_DEPLOY_BATCH` for drifters
    not yet rostered. ``U_speed_mps`` / ``U_Dir_deg`` are carried through
    unmodified but not relied upon (they may be invalid before deployment).
    """
    out = raw.copy()
    out["D_number"] = out["D_number"].astype(str)
    out["date_UTC"] = _parse_dates(out["date_UTC"])
    out = out.dropna(subset=["date_UTC"]).reset_index(drop=True)
    # `keep="first"` (the default) keeps the earliest snapshot's copy of a repeated
    # (D_number, date_UTC) fix. This assumes a fix's identity implies an identical
    # payload — i.e. upstream never revises a fix's coordinates under the same
    # timestamp. If it ever does, the correction would be silently dropped; switch to
    # `keep="last"` only after confirming the snapshot concatenation order is
    # chronological (ING-5).
    out = out.drop_duplicates(subset=["D_number", "date_UTC"], ignore_index=True)
    out["batch"] = out["D_number"].map(load_deployments()).fillna(PRE_DEPLOY_BATCH)
    return out


def tracks(raw: pd.DataFrame) -> pd.DataFrame:
    """Return only valid fixes (drop rows where Latitude or Longitude == SENTINEL),
    sorted by D_number then date_UTC."""
    valid = raw[(raw["Latitude"] != SENTINEL) & (raw["Longitude"] != SENTINEL)]
    return valid.sort_values(["D_number", "date_UTC"], ignore_index=True)


def awaiting(clean: pd.DataFrame, tracks: pd.DataFrame) -> list[str]:
    """D_numbers present in ``clean`` but with no valid fix — i.e. absent from the
    already-computed ``tracks`` frame — sorted. Takes ``tracks`` rather than
    recomputing :func:`tracks` (its sentinel filter + sort) that the caller already
    holds (ING-3)."""
    return sorted(set(clean["D_number"]) - set(tracks["D_number"]))
