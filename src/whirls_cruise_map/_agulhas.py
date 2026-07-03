"""Fetch the R/V S.A. Agulhas II track from the IPSL WHIRLS THREDDS server.

The South African Agulhas II is the cruise's second vessel. Unlike the Marion
Dufresne — served live to the browser from the CORS-open Flotte Océanographique
Française API (see docs/ship.md) — the Agulhas is published as a CSV on IPSL's
THREDDS fileServer, which sends no ``Access-Control-Allow-Origin`` header. A
browser therefore cannot read it cross-origin, so it is fetched here at build
time and baked into ``site/data/agulhas.json`` for the client to load
same-origin.

The CSV is itself an hourly scrape of myshiptracking.com (its ``source_url`` /
``scraped_at_utc`` columns), so — unlike the MD API — it carries **reported**
speed-over-ground and course-over-ground, plus a moving/stopped ``status`` and a
free-text ``area``, but no met data.

Best-effort throughout, like :mod:`_gliders`: any failure — a dead host, a bad
CSV, an unparseable row — is swallowed so the build still produces every other
artifact. A total failure returns ``[]`` and the map simply omits the vessel.
"""
from __future__ import annotations

import csv
import urllib.request
from datetime import datetime, timezone

CSV_URL = (
    "https://thredds-x.ipsl.fr/thredds/fileServer/WHIRLS/OBSERVATIONS/SHIPS/"
    "agulhas_positions.csv"
)


def _parse_time(raw: str) -> datetime | None:
    """Parse the CSV's ``reported_at`` (``YYYY-MM-DD HH:MM``, no zone) as UTC;
    ``None`` if unparseable. The column carries no timezone, but the file's own
    ``scraped_at_utc`` is UTC and the whole app is UTC, so UTC is assumed."""
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, AttributeError):
        return None


def _float_or_none(raw: str | None) -> float | None:
    """``float(raw)`` or ``None`` for an empty/missing/unparseable cell
    (``speed_kn`` is blank when the vessel is stopped)."""
    try:
        return float(raw) if raw not in (None, "") else None
    except (ValueError, TypeError):
        return None


def fetch_positions() -> list[dict]:
    """Time-sorted Agulhas fixes; ``[]`` on any failure.

    Each fix is a dict shaped like the live MD API's array elements so the client
    ship renderer consumes both without conversion:
    ``{date, lat, lon, speed_kn, course_deg, status, area}`` — ``date`` an
    ISO-8601 UTC ``…Z`` string, ``speed_kn``/``course_deg`` ``float | None``.
    """
    # One try/except around the whole fetch+parse so the contract holds: a dead
    # host, a decode error, or a malformed CSV all yield [] rather than raising.
    # (Per-row garbage is already skipped below via _parse_time/_float_or_none.)
    try:
        with urllib.request.urlopen(CSV_URL, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
        fixes = []
        for row in csv.DictReader(text.splitlines()):
            t = _parse_time(row.get("reported_at", ""))
            lat, lon = _float_or_none(row.get("lat")), _float_or_none(row.get("lon"))
            if t is None or lat is None or lon is None:
                continue
            fixes.append(
                {
                    "date": t.isoformat().replace("+00:00", "Z"),
                    "lat": lat,
                    "lon": lon,
                    "speed_kn": _float_or_none(row.get("speed_kn")),
                    "course_deg": _float_or_none(row.get("course_deg")),
                    "status": (row.get("status") or "").strip() or None,
                    "area": (row.get("area") or "").strip() or None,
                }
            )
    except Exception:
        return []
    fixes.sort(key=lambda f: f["date"])
    return fixes
