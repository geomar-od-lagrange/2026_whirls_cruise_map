"""Fetch the WHIRLS glider-group platforms — the XSPAR spar buoy, the seagliders,
the wave gliders, and the profiling floats — from the IPSL WHIRLS observations
portal (the same source the operational-centre map draws; see docs/gliders.md).

Each platform *type* is a folder on the portal — a plain Apache directory
listing — holding one ``*_track.csv`` (or ``*.csv``) per platform. We discover
every CSV by scanning the folder's autoindex for ``.csv`` links (so new
platforms appear with no code change), download and parse each, and return
time-sorted position tracks. (IPSL also serves the same files from a THREDDS
``catalog.xml`` DatasetScan; the portal is preferred as a lighter, more reliable,
CORS-open static host — see docs/gliders.md.)

The **floats** sit under the same tree but need their own fetch/split
(:func:`fetch_float_sources` / :func:`parse_float_source`): they come one file
per float in two CSV schemas — identity in a ``filename`` column (``mr_float_*``)
or in the file name (``uvp_float_*``) — beside an aggregate CSV that is skipped.
See the floats section below.

The **wave gliders** likewise mix shapes: one is a plain CSV (discovered and
parsed like any glider), the other is published only as a **NetCDF**, which the
CSV scan skips — so it has its own fetch/parse
(:func:`fetch_waveglider_nc_sources` / :func:`parse_waveglider_nc`, read as a
static portal file with xarray). See the wave-gliders section below. All types
converge on the same :class:`Platform`.

Best-effort throughout: any failure — a dead folder, a bad CSV, an unparseable
row — is swallowed so the build still produces every other artifact. A total
failure returns ``[]`` / ``None`` and the map simply shows no gliders/floats.
"""
from __future__ import annotations

import csv
import math
import os
import re
from datetime import datetime, timezone

from . import _portal, _time
from typing import NamedTuple

# WHIRLS observations portal — the operational centre's own data host.
BASE = "https://observations.ipsl.fr/aeris/whirls/data/observations"

# (type, folder URL). `type` keys the client colour/label; the operational map
# groups these under "Gliders", but XSPAR is a surface spar buoy, the seagliders
# are underwater, and the wave gliders are a surface class of their own, so we
# keep them apart. The WAVEGLIDERS/ folder's *CSV* wave glider (melktert) is
# discovered here like any other; its NetCDF-only wave glider (wg1169) needs the
# separate `fetch_waveglider_nc_sources` path below.
_GROUPS = [
    ("xspar", f"{BASE}/GLIDERS/XSPAR/"),
    ("seaglider", f"{BASE}/GLIDERS/SEAGLIDERS/"),
    ("waveglider", f"{BASE}/GLIDERS/WAVEGLIDERS/"),
]


class Platform(NamedTuple):
    """One glider-group platform: ``id`` (a glider's CSV filename, a wave glider's
    NetCDF id, or a float's mapped label), ``type`` (``"xspar"`` / ``"seaglider"``
    / ``"waveglider"`` / ``"float"``), and time-sorted ``(time, lat, lon)`` fixes
    (tz-aware UTC)."""

    id: str
    type: str
    fixes: list[tuple[datetime, float, float]]


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
        t = _time.parse_fix_time(row[ti])
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
    (``"xspar"`` / ``"seaglider"`` / ``"waveglider"`` / ``"float"``), and the raw
    CSV ``text``. Kept separate from :class:`Platform` so ingest can publish the
    untouched source (``data/raw/gliders/<id>.csv``) before parsing it. One glider
    source parses to one platform; the ``float`` source is the aggregate that
    splits into many. (The NetCDF-only wave glider does not use this text-carrying
    shape — see :func:`fetch_waveglider_nc_sources`.)"""

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
            datasets = _csv_datasets(_portal.get(dir_url), dir_url)
        except Exception:
            continue
        for pid, csv_url in datasets:
            try:
                text = _portal.get(csv_url)
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
        datasets = _csv_datasets(_portal.get(FLOATS_DIR), FLOATS_DIR)
    except Exception:
        return []
    sources = []
    for _pid, csv_url in datasets:
        name = csv_url.rsplit("/", 1)[-1]
        if name == _FLOATS_AGGREGATE:
            continue
        try:
            text = _portal.get(csv_url)
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
        t = _time.parse_fix_time(row[ti])
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


# --------------------------------------------------------------------------- #
# wave gliders — one CSV (auto-discovered), one NetCDF (read here)
# --------------------------------------------------------------------------- #
# The WAVEGLIDERS folder mixes shapes: a plain track CSV (``melktert_track.csv``),
# picked up by the shared autoindex+CSV path above (it is a ``.csv`` under the
# ``waveglider`` group in ``_GROUPS``), and a wave glider published only as an L1
# **NetCDF** (``wg1169_WHIRLS_Cruise_L1.nc``, beside a ``.ncml`` NcML pointer).
# The CSV scan matches only ``.csv``, so the NetCDF needs the fetch/parse pair
# here.
#
# We read the ``.nc`` as a **static file from the observations portal**, not via
# THREDDS OPeNDAP as the operational map does: plan 020 moved this project off the
# heavy, intermittently failing THREDDS server onto the portal, which serves the
# ``.nc`` directly. The file carries a CF ``time`` coordinate (which the
# operational map omits, reading only lat/lon), so our track is time-stamped like
# every other platform's and rides the same downstream unchanged.
WAVEGLIDERS_DIR = f"{BASE}/GLIDERS/WAVEGLIDERS/"


def _nc_datasets(index_html: str, dir_url: str) -> list[tuple[str, str]]:
    """``(filename, nc_url)`` for every ``.nc`` linked from a folder's autoindex.

    The filename is kept whole (so ingest can publish it raw and
    :func:`parse_waveglider_nc` can take identity from it). The pattern matches a
    ``.nc`` suffix but **not** the sibling ``.ncml`` NcML aggregation pointer (in
    ``…​.ncml"`` the ``.nc`` is followed by ``ml``, not the closing quote), and —
    like :func:`_csv_datasets` — drops absolute hrefs (parent/other folders)."""
    out = []
    for name in re.findall(r'href="([^"]+\.nc)"', index_html, flags=re.IGNORECASE):
        if "/" in name:
            continue
        out.append((name, dir_url + name))
    return out


def fetch_waveglider_nc_sources() -> list[tuple[str, bytes]]:
    """Every NetCDF-only wave glider under the WAVEGLIDERS folder, as
    ``(filename, nc_bytes)``; ``[]`` on failure.

    Discovered from the folder autoindex like the gliders (so a second wave-glider
    ``.nc`` appears with no code change) and downloaded as **bytes** — a NetCDF is
    binary, unlike the text-carrying :class:`Source`. The CSV wave glider in the
    same folder is handled by the shared :func:`fetch_sources` path and is not
    re-fetched here. Each listing and each file is fetched independently so one
    dead file can't suppress the rest."""
    try:
        datasets = _nc_datasets(_portal.get(WAVEGLIDERS_DIR), WAVEGLIDERS_DIR)
    except Exception:
        return []
    sources = []
    for name, nc_url in datasets:
        try:
            data = _portal.get_bytes(nc_url)
        except Exception:
            continue
        sources.append((name, data))
    return sources


def parse_waveglider_nc(name: str, data: bytes) -> Platform | None:
    """Parse one wave-glider NetCDF (``name`` + raw ``data`` bytes) into a
    :class:`Platform`; ``None`` if it lacks ``time`` / ``latitude`` / ``longitude``,
    is unreadable, or has no finite fix.

    Identity is the leading ``_``-token of the file name
    (``wg1169_WHIRLS_Cruise_L1.nc`` -> ``wg1169``), matching the operational map's
    own id. ``xarray`` (a project dep) is imported lazily so the CSV path stays
    dependency-light; it decodes the CF ``time`` coordinate to UTC. The bytes are
    staged to a temp file because the netCDF4 engine reads a path, not a buffer.
    Non-finite lat/lon rows are dropped; fixes come back time-sorted in the shared
    :class:`Platform` shape, so wave gliders ride the same downstream unchanged."""
    import tempfile

    import numpy as np
    import xarray as xr

    pid = name.split("_", 1)[0].strip()
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        with xr.open_dataset(tmp) as ds:
            if not {"time", "latitude", "longitude"} <= set(ds.variables):
                return None
            times = np.asarray(ds["time"].values, dtype="datetime64[ns]")
            secs = times.astype("datetime64[s]").astype("int64")
            nat = np.isnat(times)
            lat = np.asarray(ds["latitude"].values, dtype=float)
            lon = np.asarray(ds["longitude"].values, dtype=float)
    except Exception:
        return None
    finally:
        os.unlink(tmp)
    fixes = []
    for i in range(min(len(secs), len(lat), len(lon))):
        # Skip a NaT time or non-finite position, and guard the epoch conversion
        # against an out-of-range value — one bad row must not sink the whole
        # track (mirrors the CSV path's per-value _parse_time tolerance).
        if nat[i] or not (math.isfinite(lat[i]) and math.isfinite(lon[i])):
            continue
        try:
            t = datetime.fromtimestamp(int(secs[i]), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        fixes.append((t, float(lat[i]), float(lon[i])))
    fixes.sort(key=lambda f: f[0])
    return Platform(pid, "waveglider", fixes) if fixes else None
