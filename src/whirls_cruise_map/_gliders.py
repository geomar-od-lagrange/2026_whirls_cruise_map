"""Fetch the WHIRLS glider-group platforms — the XSPAR spar buoy, the seagliders,
and the profiling floats — from the IPSL WHIRLS observations portal (the same
source the operational-centre map draws; see docs/gliders.md).

Each platform *type* is a folder on the portal — a plain Apache directory
listing — holding one ``*_track.csv`` (or ``*.csv``) per platform. We discover
every CSV by scanning the folder's autoindex for ``.csv`` links (so new
platforms appear with no code change), download and parse each, and return
time-sorted position tracks. (IPSL also serves the same files from a THREDDS
``catalog.xml`` DatasetScan; the portal is preferred as a lighter, more reliable,
CORS-open static host — see docs/gliders.md.)

The **floats** sit under the same tree but ship a single aggregate CSV with the
platform identity in a column, not one file per platform, so they get their own
fetch/split (:func:`fetch_float_source` / :func:`parse_float_source`) — see the
floats section below. All three types converge on the same :class:`Platform`.

Best-effort throughout: any failure — a dead folder, a bad CSV, an unparseable
row — is swallowed so the build still produces every other artifact. A total
failure returns ``[]`` / ``None`` and the map simply shows no gliders/floats.
"""
from __future__ import annotations

import csv
import re
import urllib.request
from datetime import datetime, timezone
from typing import NamedTuple

# WHIRLS observations portal — the operational centre's own data host.
BASE = "https://observations.ipsl.fr/aeris/whirls/data/observations"

# (type, folder URL). `type` keys the client colour/label; the operational map
# groups both under "Gliders", but XSPAR is a surface spar buoy and the
# seagliders are underwater, so we keep them apart. A new WAVEGLIDERS/ folder
# exists on the portal but is empty and would need a client type, so it is not
# wired here yet (plan 020 follow-up).
_GROUPS = [
    ("xspar", f"{BASE}/GLIDERS/XSPAR/"),
    ("seaglider", f"{BASE}/GLIDERS/SEAGLIDERS/"),
]


class Platform(NamedTuple):
    """One glider-group platform: ``id`` (a glider's CSV filename, or a float's
    mapped label), ``type`` (``"xspar"`` / ``"seaglider"`` / ``"float"``), and
    time-sorted ``(time, lat, lon)`` fixes (tz-aware UTC)."""

    id: str
    type: str
    fixes: list[tuple[datetime, float, float]]


# The portal's Apache rejects requests without an ``Accept`` header (403), which
# urllib omits by default; a descriptive ``User-Agent`` is courtesy, not required.
_HEADERS = {"User-Agent": "whirls-cruise-map ingest", "Accept": "*/*"}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _csv_datasets(index_html: str, dir_url: str) -> list[tuple[str, str]]:
    """``(id, csv_url)`` for every ``.csv`` linked from a folder's Apache
    autoindex. ``id`` is the filename without a trailing ``_track.csv`` or
    ``.csv`` (so ``sg283_track.csv`` -> ``sg283``, ``seaexplorer.csv`` ->
    ``seaexplorer``); the URL resolves the relative href against ``dir_url``.

    The autoindex also links its parent and sort columns (``href="/…"``,
    ``?C=N;O=D``); requiring a ``.csv`` suffix and no ``/`` in the href keeps
    only the data files."""
    out = []
    for name in re.findall(r'href="([^"]+\.csv)"', index_html, flags=re.IGNORECASE):
        if "/" in name:
            continue
        pid = re.sub(r"(_track)?\.csv$", "", name, flags=re.IGNORECASE)
        out.append((pid, dir_url + name))
    return out


def _parse_time(raw: str) -> datetime | None:
    """Parse a fix time to tz-aware UTC; ``None`` if unparseable.

    The WHIRLS feeds mix four time encodings, and the format no longer tracks
    the platform *type* — even the seagliders differ — so we detect it per
    value instead of keying on the type:

    - Unix epoch seconds, e.g. ``1783078052.0`` (a Seaglider emits this);
    - ISO ``YYYY-MM-DD HH:MM:SS`` with no offset, read as UTC (a Seaglider);
    - ISO with an explicit offset, e.g. ``2026-07-02 00:00:00+00:00`` (XSPAR);
    - day-first ``DD/MM/YYYY HH:MM:SS`` (the SeaExplorer glider), read as UTC.
    """
    raw = raw.strip()
    if not raw:
        return None
    # A bare number is Unix epoch seconds; an ISO string fails float() and falls
    # through to the parsers below.
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
        # SeaExplorer exports day-first ``DD/MM/YYYY HH:MM:SS`` (no offset, UTC).
        try:
            dt = datetime.strptime(raw, "%d/%m/%Y %H:%M:%S")
        except ValueError:
            return None
    # Naive timestamps are UTC; offset-aware ones are normalised to UTC.
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _delimiter(line: str) -> str:
    """``;`` if a line has more semicolons than commas, else ``,``."""
    return ";" if line.count(";") > line.count(",") else ","


def _read_rows(text: str):
    """``(lower-cased header, data-row iterator)`` from CSV ``text``; ``([], iter(()))``
    if empty.

    Feeds vary in dialect: the SeaExplorer glider exports a BOM-prefixed,
    ``;``-separated *header* over ``,``-separated *data* rows, while everything
    else is plain comma throughout. We strip a leading BOM and sniff the
    delimiter of the header and of the data independently (each from its own
    line), so a mixed file still maps columns by name and splits its rows
    correctly. ``csv`` over ``splitlines()`` handles LF and any stray CR-only
    endings alike.
    """
    lines = text.lstrip("\ufeff").splitlines()
    if not lines:
        return [], iter(())
    header = [
        h.strip().lower()
        for h in next(csv.reader([lines[0]], delimiter=_delimiter(lines[0])))
    ]
    data = lines[1:]
    reader = csv.reader(data, delimiter=_delimiter(data[0]) if data else ",")
    return header, reader


def _parse_csv(text: str) -> list[tuple[datetime, float, float]]:
    """Time-sorted ``(time, lat, lon)`` fixes from a track CSV.

    Column order differs between (and within) platform types — some feeds put
    ``longitude`` before ``latitude`` — so we map by header *name*, not position.
    """
    header, reader = _read_rows(text)
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

    Each folder listing and each CSV is fetched independently so one dead
    platform can't suppress the rest.
    """
    sources = []
    for gtype, dir_url in _GROUPS:
        try:
            datasets = _csv_datasets(_get(dir_url), dir_url)
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
# The WHIRLS floats sit under the same ``GLIDERS`` tree as the seagliders/XSPAR.
# Their ``FLOATS`` folder holds an aggregate ``floats_track.csv`` (every float's
# fixes interleaved) *beside* one per-float position file. We read the per-float
# files — the same fixes, but fresher: the aggregate lags them — and skip the
# aggregate so a float isn't counted twice. Discovered from the folder listing
# like the gliders, so a new float file appears with no code change.
#
# The per-float files come in two CSV schemas:
#
#   - ``mr_float_<institution>_positions.csv`` — header
#     ``time,latitude,longitude,filename``; float identity is the leading
#     ``_``-token of the ``filename`` column (``65a0_015_01_technical.txt`` ->
#     ``65a0``), mirroring the WHIRLS operational map's own rule (and grouping
#     stays correct if one file ever carries more than one float).
#   - ``uvp_float_<id>_locations.csv`` — header
#     ``profile,utc_time,latitude,longitude``; there is no ``filename`` column,
#     so the (single) float's identity is the ``<id>`` in the file name.
#
# ``parse_float_source`` reads either: it accepts ``utc_time`` as an alias for
# ``time`` and, absent a ``filename`` column, takes identity from a
# ``uvp_float_<id>_locations`` source name. A no-``filename`` source that is *not*
# a UVP file (the aggregate ``floats_track``) still yields nothing — there is no
# way to separate its interleaved floats.
FLOATS_DIR = f"{BASE}/GLIDERS/FLOATS/"
_FLOATS_AGGREGATE = "floats_track.csv"

# A UVP float carries its id in the file name (``uvp_float_6596_locations`` ->
# ``6596``) rather than a ``filename`` column. Matching the exact established
# pattern keeps the aggregate (and any other no-``filename`` file) yielding
# nothing.
_UVP_FILE_RE = re.compile(r"^uvp_float_([^_]+)_locations$", re.IGNORECASE)

# The operational map's own id -> label mapping (``65a0`` = U. Gothenburg float,
# ``6594`` = Southampton float). An unmapped id falls back to itself, so a further
# float (the UVP ``6596`` / ``6597``, whose institution isn't established from the
# file) appears — labelled by its raw id — with no code change.
_FLOAT_LABELS = {"65a0": "UGOT", "6594": "SOTON"}


def fetch_float_sources() -> list[Source]:
    """Every per-institution float position CSV under the FLOATS folder,
    downloaded but not parsed; ``[]`` on failure. The aggregate
    ``floats_track.csv`` is skipped (same fixes interleaved, and it lags the
    per-institution files). Each ``id`` is the source filename, so ingest
    publishes it raw beside the glider CSVs (``data/raw/gliders/<id>.csv``). Each
    listing and each CSV is fetched independently so one dead file can't suppress
    the rest."""
    try:
        datasets = _csv_datasets(_get(FLOATS_DIR), FLOATS_DIR)
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
    """Parse one per-float position CSV into its float(s), across both float
    schemas (see the section comment above).

    Identity comes from the ``filename`` column when present — the leading
    ``_``-token (``65a0_015_01_technical.txt`` -> ``65a0``), grouped rather than
    assuming one-float-per-file so it stays correct if a file ever interleaves
    several — else from a ``uvp_float_<id>_locations`` source name (one float per
    file). :data:`_FLOAT_LABELS` maps the id to a human label, falling back to the
    raw id. The time column is ``time`` or its ``utc_time`` alias. Each float's
    fixes come back time-sorted in the glider :class:`Platform` shape, so floats
    ride the same downstream (``write_gliders``, ``gliders_geojson``, the
    forecast) unchanged. Platforms are returned sorted by id for a stable
    ``gliders.csv``. A source with neither a ``filename`` column nor a UVP file
    name (the aggregate ``floats_track``) yields nothing — its interleaved floats
    can't be separated."""
    header, reader = _read_rows(src.text)
    ti = next((header.index(n) for n in ("time", "utc_time") if n in header), None)
    if ti is None:
        return []
    try:
        lai, loi = header.index("latitude"), header.index("longitude")
    except ValueError:
        return []
    fi = header.index("filename") if "filename" in header else None
    file_id = None
    if fi is None:
        match = _UVP_FILE_RE.match(src.id)
        if match is None:
            return []
        file_id = match.group(1).strip().lower()
    need = max(ti, lai, loi, fi if fi is not None else 0)
    by_id: dict[str, list[tuple[datetime, float, float]]] = {}
    for row in reader:
        if len(row) <= need:
            continue
        if fi is None:
            raw_id = file_id
        else:
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
