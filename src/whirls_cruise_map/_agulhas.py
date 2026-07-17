"""Fetch the R/V S.A. Agulhas II track from the IPSL WHIRLS observations portal.

The South African Agulhas II is the cruise's second vessel. Unlike the Marion
Dufresne — served live to the browser from the Flotte Océanographique Française
API (see docs/ship.md) — the Agulhas is published as a CSV on the WHIRLS
observations portal. It is fetched server-side at ingest time — published
cleaned to ``data/ship_agulhas_ii.csv`` and, by the derive stage, baked into the
map's ``agulhas.json`` for the client to load same-origin. The portal is in fact
CORS-open, so the client *could* read it cross-origin, but baking keeps the map
working from the last-good copy when the source is briefly unavailable and
avoids re-fetching upstream on every page load.

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

from . import _portal, _time

CSV_URL = (
    "https://observations.ipsl.fr/aeris/whirls/data/observations/SHIPS/"
    "agulhas_positions.csv"
)

def _float_or_none(raw: str | None) -> float | None:
    """``float(raw)`` or ``None`` for an empty/missing/unparseable cell
    (``speed_kn`` is blank when the vessel is stopped)."""
    try:
        return float(raw) if raw not in (None, "") else None
    except (ValueError, TypeError):
        return None


def fetch_raw() -> str | None:
    """The Agulhas positions CSV text as fetched; ``None`` on any failure.

    Kept separate from :func:`parse` so ingest can publish the untouched source
    (``data/raw/agulhas_ii.csv``) before parsing it.
    """
    try:
        return _portal.get(CSV_URL)
    except Exception:
        return None


def parse(text: str) -> list[dict]:
    """Parse the positions CSV text into time-sorted Agulhas fixes.

    Each fix is a dict shaped like the live MD API's array elements so the client
    ship renderer consumes both without conversion:
    ``{date, lat, lon, speed_kn, course_deg, status, area}`` — ``date`` an
    ISO-8601 UTC ``…Z`` string, ``speed_kn``/``course_deg`` ``float | None``.
    Per-row garbage is skipped via :func:`_parse_time` / :func:`_float_or_none`.
    """
    fixes = []
    for row in csv.DictReader(text.splitlines()):
        t = _time.parse_fix_time(row.get("reported_at", ""))
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
    fixes.sort(key=lambda f: f["date"])
    return fixes
