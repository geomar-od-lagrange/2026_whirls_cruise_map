"""Fetch the WHIRLS glider platforms — the XSPAR spar buoy and the seagliders —
from the IPSL WHIRLS THREDDS server (the same source the operational-centre map
draws; see docs/gliders.md).

Each platform *type* is a THREDDS folder with a DatasetScan ``catalog.xml`` and
one ``*_track.csv`` per platform under ``fileServer``. We discover every CSV in
each catalog (so new platforms appear with no code change), download and parse
it, and return time-sorted position tracks.

Best-effort throughout: any failure — a dead catalog, a bad CSV, an unparseable
row — is swallowed so the build still produces every other artifact. A total
failure returns ``[]`` and the map simply shows no gliders.
"""
from __future__ import annotations

import csv
import io
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import NamedTuple

THREDDS = "https://thredds-x.ipsl.fr/thredds"

# (type, catalog URL). `type` keys the client colour/label; the operational map
# groups both under "Gliders", but XSPAR is a surface spar buoy and the
# seagliders are underwater, so we keep them apart.
_GROUPS = [
    ("xspar", f"{THREDDS}/catalog/WHIRLS/OBSERVATIONS/GLIDERS/XSPAR/catalog.xml"),
    (
        "seaglider",
        f"{THREDDS}/catalog/WHIRLS/OBSERVATIONS/GLIDERS/SEAGLIDERS/catalog.xml",
    ),
]

_XSPAR_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(\d{1,2}):(\d{2})$")


class Platform(NamedTuple):
    """One glider platform: ``id`` (from the CSV filename), ``type``
    (``"xspar"`` / ``"seaglider"``), and time-sorted ``(time, lat, lon)`` fixes
    (tz-aware UTC)."""

    id: str
    type: str
    fixes: list[tuple[datetime, float, float]]


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _csv_datasets(catalog_xml: str) -> list[tuple[str, str]]:
    """``(id, csv_url)`` for every ``*_track.csv`` in a THREDDS catalog. ``id``
    is the filename without ``_track.csv``; the URL is the dataset's ``urlPath``
    under ``fileServer``."""
    root = ET.fromstring(catalog_xml)
    out = []
    # THREDDS namespaces the elements; match on the local tag/attribute name so
    # we don't hard-code the InvCatalog namespace URI.
    for el in root.iter():
        if not el.tag.endswith("}dataset") and el.tag != "dataset":
            continue
        url_path = el.get("urlPath")
        if not url_path or not url_path.endswith(".csv"):
            continue
        name = url_path.rsplit("/", 1)[-1]
        pid = re.sub(r"_track\.csv$", "", name, flags=re.IGNORECASE)
        out.append((pid, f"{THREDDS}/fileServer/{url_path}"))
    return out


def _parse_time(gtype: str, raw: str) -> datetime | None:
    """Parse a fix time to tz-aware UTC; ``None`` if unparseable.

    Seaglider times are ISO ``YYYY-MM-DD HH:MM:SS`` (UTC). XSPAR times are
    ``M/D/YY H:MM``; we mirror the operational site's ``parseXsparGliderDate`` —
    expand a 2-digit year, and if the result predates 2020 (the upstream year
    field is unreliable) fall back to the current UTC year, so a stale-looking
    ``7/2/16`` reads as this year's fix.
    """
    raw = raw.strip()
    if gtype == "xspar":
        m = _XSPAR_DATE.match(raw)
        if not m:
            return None
        month, day, year, hour, minute = (int(g) for g in m.groups())
        if year < 100:
            year += 2000
        if year < 2020:
            year = datetime.now(timezone.utc).year
        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_csv(gtype: str, text: str) -> list[tuple[datetime, float, float]]:
    """Time-sorted ``(time, lat, lon)`` fixes from a track CSV.

    Column order differs between platforms (XSPAR ``Time,Latitude,Longitude``;
    seaglider ``time,longitude,latitude``), so we map by header *name*, not
    position. XSPAR uses CR-only line endings; ``csv`` over the split lines
    handles both that and the seaglider's ``\\n``.
    """
    lines = text.splitlines()
    if not lines:
        return []
    reader = csv.reader(lines)
    header = [h.strip().lower() for h in next(reader)]
    try:
        ti, lai, loi = (
            header.index("time"),
            header.index("latitude"),
            header.index("longitude"),
        )
    except ValueError:
        return []
    fixes = []
    for row in reader:
        if len(row) <= max(ti, lai, loi):
            continue
        t = _parse_time(gtype, row[ti])
        try:
            lat, lon = float(row[lai]), float(row[loi])
        except ValueError:
            continue
        if t is not None:
            fixes.append((t, lat, lon))
    fixes.sort(key=lambda f: f[0])
    return fixes


def fetch_gliders() -> list[Platform]:
    """Every glider platform with at least one fix; ``[]`` on total failure.

    Each catalog and each CSV is fetched independently so one dead platform
    can't suppress the rest.
    """
    platforms = []
    for gtype, catalog_url in _GROUPS:
        try:
            datasets = _csv_datasets(_get(catalog_url))
        except Exception:
            continue
        for pid, csv_url in datasets:
            try:
                fixes = _parse_csv(gtype, _get(csv_url))
            except Exception:
                continue
            if fixes:
                platforms.append(Platform(pid, gtype, fixes))
    return platforms
