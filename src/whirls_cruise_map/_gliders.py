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


def _parse_time(raw: str) -> datetime | None:
    """Parse a fix time to tz-aware UTC; ``None`` if unparseable.

    The WHIRLS feeds mix three time encodings, and the format no longer tracks
    the platform *type* — even the two seagliders differ — so we detect it per
    value instead of keying on the type:

    - Unix epoch seconds, e.g. ``1783078052.0`` (one seaglider emits this);
    - ISO ``YYYY-MM-DD HH:MM:SS`` with no offset, read as UTC (another seaglider);
    - ISO with an explicit offset, e.g. ``2026-07-02 00:00:00+00:00`` (XSPAR).
    """
    raw = raw.strip()
    if not raw:
        return None
    # A bare number is Unix epoch seconds; an ISO string fails float() and falls
    # through to the parser below.
    try:
        epoch = float(raw)
    except ValueError:
        pass
    else:
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # Naive timestamps are UTC; offset-aware ones are normalised to UTC.
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_csv(text: str) -> list[tuple[datetime, float, float]]:
    """Time-sorted ``(time, lat, lon)`` fixes from a track CSV.

    Column order differs between (and within) platform types — some feeds put
    ``longitude`` before ``latitude`` — so we map by header *name*, not position.
    ``csv`` over ``splitlines()`` handles LF and any stray CR-only endings alike.
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
        t = _parse_time(row[ti])
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
                fixes = _parse_csv(_get(csv_url))
            except Exception:
                continue
            if fixes:
                platforms.append(Platform(pid, gtype, fixes))
    return platforms
