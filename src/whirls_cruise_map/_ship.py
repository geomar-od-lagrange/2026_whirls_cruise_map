"""Fetch the R/V Marion Dufresne track for build-time deployment detection.

The same public Flotte Océanographique Française source the client polls live
(see docs/ship.md); here we pull a one-shot snapshot covering the whole drifter
reporting window, so :mod:`_deploy` can tell where each drifter detached from the
vessel. Best-effort: any failure returns an empty track, and the caller then
skips truncation (full tracks, as before).
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

# `MD` is the Marion Dufresne. Start well before the cruise so port/transit fixes
# are covered; the API returns whatever it holds within the window.
POSITIONS_URL = (
    "https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions"
)
TRACK_START = "2026-06-20T00:00:00.000Z"


def _parse_time(s: str) -> datetime | None:
    """Parse the API's `date` (e.g. ``2026-07-01T20:20:00.000+0000``) to a
    tz-aware UTC datetime; ``None`` if unparseable."""
    try:
        return datetime.fromisoformat(s.replace("+0000", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, AttributeError):
        return None


def fetch_raw() -> str | None:
    """The FOF positions API response text over the track window; ``None`` on any
    failure.

    Kept separate from :func:`parse` so ingest can publish the untouched source
    (``data/raw/marion_dufresne.json``) before parsing it.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    url = f"{POSITIONS_URL}?startDate={TRACK_START}&endDate={now}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def parse(text: str) -> list[tuple[datetime, float, float]]:
    """Parse the positions API response into time-sorted ``(time, lat, lon)``
    vessel fixes; ``[]`` on a decode error or an unexpected shape."""
    try:
        raw = json.loads(text)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    fixes = []
    for p in raw:
        if not isinstance(p, dict):
            continue  # a well-formed JSON list of non-objects must not raise
        t = _parse_time(p.get("date", ""))
        lat, lon = p.get("lat"), p.get("lon")
        if t is not None and isinstance(lat, (int, float)) and isinstance(
            lon, (int, float)
        ):
            fixes.append((t, float(lat), float(lon)))
    fixes.sort(key=lambda f: f[0])
    return fixes
