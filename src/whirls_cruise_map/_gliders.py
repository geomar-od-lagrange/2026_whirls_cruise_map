"""Fetch the WHIRLS glider-group platforms — the XSPAR spar buoy, the seagliders,
and the profiling floats — from the IPSL WHIRLS THREDDS server (the same source
the operational-centre map draws; see docs/gliders.md).

For gliders, each platform *type* is a THREDDS folder with a DatasetScan
``catalog.xml`` and one ``*_track.csv`` per platform under ``fileServer``. We
discover every CSV in each catalog (so new platforms appear with no code change),
download and parse it, and return time-sorted position tracks.

The **floats** sit under the same tree but ship a single aggregate CSV with the
platform identity in a column, not one file per platform, so they get their own
fetch/split (:func:`fetch_float_source` / :func:`parse_float_source`) — see the
floats section below. All three types converge on the same :class:`Platform`.

Best-effort throughout: any failure — a dead catalog, a bad CSV, an unparseable
row — is swallowed so the build still produces every other artifact. A total
failure returns ``[]`` / ``None`` and the map simply shows no gliders/floats.
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
    """One glider-group platform: ``id`` (a glider's CSV filename, or a float's
    mapped label), ``type`` (``"xspar"`` / ``"seaglider"`` / ``"float"``), and
    time-sorted ``(time, lat, lon)`` fixes (tz-aware UTC)."""

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


class Source(NamedTuple):
    """A glider-group source CSV as fetched: ``id`` (the filename), ``type``
    (``"xspar"`` / ``"seaglider"`` / ``"float"``), and the raw CSV ``text``. Kept
    separate from :class:`Platform` so ingest can publish the untouched source
    (``data/raw/gliders/<id>.csv``) before parsing it. One glider source parses to
    one platform; the ``float`` source is the aggregate that splits into many."""

    id: str
    type: str
    text: str


def fetch_sources() -> list[Source]:
    """Every discovered platform track CSV, downloaded but not parsed; ``[]`` on
    total failure.

    Each catalog and each CSV is fetched independently so one dead platform can't
    suppress the rest.
    """
    sources = []
    for gtype, catalog_url in _GROUPS:
        try:
            datasets = _csv_datasets(_get(catalog_url))
        except Exception:
            continue
        for pid, csv_url in datasets:
            try:
                text = _get(csv_url)
            except Exception:
                continue
            sources.append(Source(pid, gtype, text))
    return sources


def parse_source(src: Source) -> Platform | None:
    """Parse one :class:`Source` into a :class:`Platform`; ``None`` if it has no
    usable fix."""
    fixes = _parse_csv(src.text)
    return Platform(src.id, src.type, fixes) if fixes else None


# --------------------------------------------------------------------------- #
# floats — the same GLIDERS tree, read per-institution (skip the aggregate)
# --------------------------------------------------------------------------- #
# The WHIRLS floats sit under the same THREDDS ``GLIDERS`` tree as the
# seagliders/XSPAR. Their ``FLOATS`` folder holds an aggregate ``floats_track.csv``
# (every float's fixes interleaved) *beside* one
# ``mr_float_<institution>_positions.csv`` per float. We read the per-institution
# files — the same fixes, but fresher: the aggregate lags them — and skip the
# aggregate so a float isn't counted twice. Discovered from the catalog like the
# gliders, so a new institution's float file appears with no code change.
#
# Float identity still lives in a ``filename`` column (``65a0_015_01_technical.txt``)
# rather than the file name, so parsing groups by that column's leading id (each
# per-institution file is normally one float, but grouping stays correct if one
# ever carries more, and reuses the operational map's identity rule).
FLOATS_CATALOG = (
    f"{THREDDS}/catalog/WHIRLS/OBSERVATIONS/GLIDERS/FLOATS/catalog.xml"
)
_FLOATS_AGGREGATE = "floats_track.csv"

# The operational map's own id -> label mapping (``65a0`` = U. Gothenburg float,
# ``6594`` = Southampton float). An unmapped id falls back to itself, so a third
# float appears — labelled by its raw id — with no code change.
_FLOAT_LABELS = {"65a0": "UGOT", "6594": "SOTON"}


def fetch_float_sources() -> list[Source]:
    """Every per-institution float position CSV under the FLOATS catalog,
    downloaded but not parsed; ``[]`` on failure. The aggregate
    ``floats_track.csv`` is skipped (same fixes interleaved, and it lags the
    per-institution files). Each ``id`` is the source filename, so ingest
    publishes it raw beside the glider CSVs (``data/raw/gliders/<id>.csv``). Each
    catalog and each CSV is fetched independently so one dead file can't suppress
    the rest."""
    try:
        datasets = _csv_datasets(_get(FLOATS_CATALOG))
    except Exception:
        return []
    sources = []
    for _pid, csv_url in datasets:
        name = csv_url.rsplit("/", 1)[-1]
        if name == _FLOATS_AGGREGATE:
            continue
        try:
            text = _get(csv_url)
        except Exception:
            continue
        sources.append(Source(name.removesuffix(".csv"), "float", text))
    return sources


def parse_float_source(src: Source) -> list[Platform]:
    """Parse one per-institution float CSV into its float(s).

    A float's identity is the leading ``_``-token of the ``filename`` column
    (``65a0_015_01_technical.txt`` -> ``65a0``), which :data:`_FLOAT_LABELS` maps
    to a human id. We group by that rather than assume one-float-per-file: it
    reuses the operational map's identity rule and stays correct if a file ever
    carries more than one float. Each float's fixes come back time-sorted in the
    glider :class:`Platform` shape, so floats ride the same downstream
    (``write_gliders``, ``gliders_geojson``, the forecast) unchanged. Platforms
    are returned sorted by id for a stable ``gliders.csv``."""
    lines = src.text.splitlines()
    if not lines:
        return []
    reader = csv.reader(lines)
    header = [h.strip().lower() for h in next(reader)]
    try:
        ti, lai, loi, fi = (
            header.index("time"),
            header.index("latitude"),
            header.index("longitude"),
            header.index("filename"),
        )
    except ValueError:
        return []
    by_id: dict[str, list[tuple[datetime, float, float]]] = {}
    for row in reader:
        if len(row) <= max(ti, lai, loi, fi):
            continue
        raw_id = row[fi].split("_", 1)[0].strip().lower()
        if not raw_id:
            continue
        t = _parse_time(row[ti])
        try:
            lat, lon = float(row[lai]), float(row[loi])
        except ValueError:
            continue
        if t is not None:
            by_id.setdefault(raw_id, []).append((t, lat, lon))
    platforms = []
    for raw_id in sorted(by_id):
        fixes = sorted(by_id[raw_id], key=lambda f: f[0])
        platforms.append(Platform(_FLOAT_LABELS.get(raw_id, raw_id), "float", fixes))
    return platforms
