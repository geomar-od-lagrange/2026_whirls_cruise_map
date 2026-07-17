"""The ``/data`` seam: persist cleaned instrument & ship tracks as CSVs and read
them back for the derive stage.

``/data`` is at once the **download product** (human-inspectable CSVs a user can
audit against the raw sources) and the **build input** (derive reads these tables
back instead of re-fetching). See ``docs/data.md`` and
``plans/018-ingest-derive-data-seam.md``.

Layout under the data dir::

    drifters.csv              cleaned drifter fixes
    gliders.csv               cleaned glider-group fixes (XSPAR + seagliders + wave gliders + floats)
    ship_marion_dufresne.csv  cleaned R/V Marion Dufresne fixes
    ship_agulhas_ii.csv       cleaned R/V S.A. Agulhas II fixes (+ SOG/COG/status/area)
    platforms.csv             one row per platform (batch, deployed_at, coverage)
    manifest.json             file index + per-file provenance + freshness stamp
    raw/drifters_raw.csv      concatenated snapshot CSVs, pre-clean
    raw/gliders/<id>.csv      per-platform track CSVs as fetched
    raw/marion_dufresne.json  FOF positions API response as fetched
    raw/agulhas_ii.csv        IPSL observations-portal CSV as fetched

Every cleaned per-fix table shares the core columns ``platform_id,
platform_type, time_utc, lat, lon`` (``time_utc`` ISO-8601 UTC ``…Z``), plus
per-source native columns. The reader functions reconstruct the exact in-memory
shapes the derive consumers (:mod:`._geojson`, :mod:`._forecast`) expect, so the
map is fully re-derivable from ``/data`` alone (the CMEMS overlays aside).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from . import _time
from ._clean import PRE_DEPLOY_BATCH, load_deployments
from ._gliders import Platform

# Provenance strings recorded per file in manifest.json (the upstream a reader
# can check the cleaning against). These are eager module-level imports — each URL
# lives in the module that owns that source — pulled in only for the four constant
# strings (ING-6: the storage seam does not otherwise depend on those modules).
from ._agulhas import CSV_URL as _AGULHAS_URL
from ._fetch import SHARE_URL as _DRIFTER_URL
from ._gliders import BASE as _GLIDER_URL
from ._ship import POSITIONS_URL as _MD_URL

_CORE = ["platform_id", "platform_type", "time_utc", "lat", "lon"]
_TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------- #
# atomic writes + time formatting
# --------------------------------------------------------------------------- #
def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a sibling ``*.tmp`` + :func:`os.replace`, so
    a concurrent reader (nginx serving, or the derive stage) never sees a
    half-written file. A failed write removes its own ``*.tmp`` rather than
    littering the served tree with orphans that accumulate across retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def iso_utc(when) -> str:
    """Format a tz-aware datetime / :class:`pandas.Timestamp` as ISO-8601 UTC
    ``…Z`` at second precision (the app's uniform time convention). Delegates to the
    shared :func:`._time.iso_z` (audit IDIOM-2), coercing via ``pd.Timestamp`` first so
    a ``datetime``/``datetime64``/string input is accepted as before."""
    return _time.iso_z(pd.Timestamp(when))


def _iso_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).dt.strftime(_TIME_FMT)


# --------------------------------------------------------------------------- #
# write side (ingest) — each returns a manifest entry
# --------------------------------------------------------------------------- #
def _write_csv(
    data_dir: Path, name: str, df: pd.DataFrame, kind: str, source: str
) -> dict:
    atomic_write_text(data_dir / name, df.to_csv(index=False))
    return {
        "name": name,
        "kind": kind,
        "source": source,
        "rows": int(len(df)),
        "columns": list(df.columns),
    }


def write_raw_drifters(data_dir: Path, concat: pd.DataFrame) -> dict:
    """The concatenated snapshot CSVs, pre-clean (see :func:`._clean.concat_snapshots`)."""
    return _write_csv(data_dir, "raw/drifters_raw.csv", concat, "raw", _DRIFTER_URL)


def write_raw_text(data_dir: Path, name: str, text: str, source: str) -> dict:
    """A source payload published verbatim under ``raw/`` (glider CSV, MD JSON,
    Agulhas CSV)."""
    atomic_write_text(data_dir / "raw" / name, text)
    return {"name": f"raw/{name}", "kind": "raw", "source": source}


def write_raw_bytes(data_dir: Path, name: str, data: bytes, source: str) -> dict:
    """A binary source payload published verbatim under ``raw/`` (the wave-glider
    NetCDF), the bytes counterpart of :func:`write_raw_text`."""
    atomic_write_bytes(data_dir / "raw" / name, data)
    return {"name": f"raw/{name}", "kind": "raw", "source": source}


def write_drifters(data_dir: Path, tracks: pd.DataFrame) -> dict:
    """Cleaned drifter fixes, ordered by ``(time_utc, platform_id)``.

    ``tracks`` is the valid-fix frame from :func:`._clean.tracks` (columns
    ``D_number, date_UTC, Latitude, Longitude, U_speed_mps, U_Dir_deg,
    batteryState``); ``batch`` is not repeated per fix — it lives once in
    ``platforms.csv``.

    Rows are ordered **chronologically, then by id to break ties**, not grouped
    by platform. This is a download-transport choice, not a semantic one: with
    the newest fixes always at end-of-file, a rebuild only *appends* there, so a
    bandwidth-limited client can pull just the new tail with an HTTP range
    request (``curl -C -``) instead of re-fetching the whole file. The map build
    is indifferent to it — :func:`read_drifters` re-sorts by
    ``(platform_id, time_utc)`` on read. The property is best-effort: a fix that
    arrives late with an old timestamp inserts mid-file and shifts the byte
    prefix, so a range client must detect a changed prefix and fall back to a
    full download. ``time_utc`` is fixed-width ISO-8601 ``…Z``, so a lexical
    sort of the string equals a chronological one."""
    out = pd.DataFrame(
        {
            "platform_id": tracks["D_number"].astype(str),
            "platform_type": "drifter",
            "time_utc": _iso_series(tracks["date_UTC"]),
            "lat": tracks["Latitude"],
            "lon": tracks["Longitude"],
            "u_speed_mps": tracks["U_speed_mps"],
            "u_dir_deg": tracks["U_Dir_deg"],
            "battery_state": tracks["batteryState"],
        }
    )
    out = out.sort_values(["time_utc", "platform_id"], kind="stable", ignore_index=True)
    return _write_csv(data_dir, "drifters.csv", out, "cleaned", _DRIFTER_URL)


def write_gliders(data_dir: Path, platforms: list[Platform]) -> dict:
    """Cleaned glider-group fixes, one long table over all platforms
    (``platform_type`` is ``xspar`` / ``seaglider`` / ``waveglider`` / ``float``)."""
    rows = [
        (p.id, p.type, iso_utc(t), lat, lon)
        for p in platforms
        for (t, lat, lon) in p.fixes
    ]
    out = pd.DataFrame(rows, columns=_CORE)
    return _write_csv(data_dir, "gliders.csv", out, "cleaned", _GLIDER_URL)


def write_ship_md(data_dir: Path, track: list) -> dict:
    """Cleaned R/V Marion Dufresne fixes (``(time, lat, lon)`` tuples)."""
    rows = [("marion_dufresne", "ship", iso_utc(t), lat, lon) for (t, lat, lon) in track]
    out = pd.DataFrame(rows, columns=_CORE)
    return _write_csv(data_dir, "ship_marion_dufresne.csv", out, "cleaned", _MD_URL)


def write_ship_agulhas(data_dir: Path, positions: list[dict]) -> dict:
    """Cleaned R/V S.A. Agulhas II fixes (the :func:`._agulhas.parse` dicts, whose
    ``date`` is already ISO-8601 ``…Z``), with the reported SOG/COG/status/area."""
    out = pd.DataFrame(
        [
            {
                "platform_id": "agulhas_ii",
                "platform_type": "ship",
                "time_utc": p["date"],
                "lat": p["lat"],
                "lon": p["lon"],
                "speed_kn": p["speed_kn"],
                "course_deg": p["course_deg"],
                "status": p["status"],
                "area": p["area"],
            }
            for p in positions
        ],
        columns=_CORE + ["speed_kn", "course_deg", "status", "area"],
    )
    return _write_csv(data_dir, "ship_agulhas_ii.csv", out, "cleaned", _AGULHAS_URL)


PLATFORM_COLUMNS = [
    "platform_id",
    "platform_type",
    "batch",
    "deployed_at",
    "first_fix",
    "last_fix",
    "n_fixes",
]


def write_platforms(data_dir: Path, records: list[dict]) -> dict:
    """One row per platform: identity, drifter ``batch`` / ``deployed_at``
    annotations (empty for non-drifters), and fix-coverage (``first_fix``,
    ``last_fix``, ``n_fixes``; an awaiting drifter has ``n_fixes`` 0 and empty
    fix times)."""
    out = pd.DataFrame(records, columns=PLATFORM_COLUMNS)
    return _write_csv(data_dir, "platforms.csv", out, "metadata", _DRIFTER_URL)


def write_manifest(data_dir: Path, entries: list[dict], built_at: str) -> None:
    """The directory index: build time + one entry per file (name, kind,
    provenance URL, and — for tables — row count and columns)."""
    manifest = {"built_at": built_at, "files": entries}
    atomic_write_text(data_dir / "manifest.json", json.dumps(manifest, indent=2))


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_INDEX_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2026 Whirls Cruise — datasets</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 60rem; margin: 2rem auto;
         padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  p { color: #555; } @media (prefers-color-scheme: dark) { p { color: #aaa; } }
  a { color: #2563eb; } @media (prefers-color-scheme: dark) { a { color: #7aa7ff; } }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #8883; }
  th { font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  code, td:first-child a { font-family: ui-monospace, monospace; }
  .stamp { font-size: .85rem; }
</style>
</head>
<body>
<h1>2026 Whirls Cruise — datasets</h1>
<p>Cleaned and raw drifter, glider, and ship tracks, rebuilt from live upstreams.
See the <a href="../map/">map</a>, or <a href="manifest.json">manifest.json</a> for
the machine-readable index (per-file columns and provenance).</p>
<p class="stamp">Built __BUILT_AT__</p>
<table>
<thead><tr><th>File</th><th>Kind</th><th class="num">Rows</th><th class="num">Size</th><th>Source</th></tr></thead>
<tbody>
"""

_INDEX_TAIL = "</tbody>\n</table>\n</body>\n</html>\n"


def write_index(data_dir: Path, entries: list[dict], built_at: str) -> None:
    """A browsable ``index.html`` landing page listing every ``/data`` file
    (GitLab Pages and nginx serve it for a bare directory request; neither
    autoindexes). Built from the same ``entries`` as :func:`write_manifest`,
    grouped cleaned → metadata → raw."""
    order = {"cleaned": 0, "metadata": 1, "raw": 2}
    rows = []
    for e in sorted(entries, key=lambda e: (order.get(e["kind"], 9), e["name"])):
        path = data_dir / e["name"]
        size = _human_size(path.stat().st_size) if path.exists() else "—"
        nrows = f"{e['rows']:,}" if "rows" in e else "—"
        source = f'<a href="{e["source"]}">source</a>' if e.get("source") else "—"
        rows.append(
            f'<tr><td><a href="{e["name"]}">{e["name"]}</a></td>'
            f'<td>{e["kind"]}</td><td class="num">{nrows}</td>'
            f'<td class="num">{size}</td><td>{source}</td></tr>'
        )
    # str.replace, not str.format — the embedded CSS is full of literal `{}`.
    head = _INDEX_HEAD.replace("__BUILT_AT__", built_at)
    atomic_write_text(data_dir / "index.html", head + "\n".join(rows) + _INDEX_TAIL)


# --------------------------------------------------------------------------- #
# read side (derive) — reconstruct the shapes derive consumers expect
# --------------------------------------------------------------------------- #
_EMPTY_TRACKS = pd.DataFrame(
    columns=[
        "D_number",
        "date_UTC",
        "Latitude",
        "Longitude",
        "U_speed_mps",
        "U_Dir_deg",
        "batteryState",
        "batch",
    ]
)


def read_drifters(data_dir: Path) -> pd.DataFrame:
    """Reconstruct the cleaned-tracks frame that :mod:`._geojson` /
    :mod:`._forecast` consume (columns ``D_number, date_UTC, Latitude, Longitude,
    U_speed_mps, U_Dir_deg, batteryState, batch``), sorted by ``D_number`` then
    ``date_UTC``. ``batch`` is joined from ``platforms.csv`` (per-platform, so the
    derive side sees the ingest-time roster snapshot). Missing file -> empty."""
    path = data_dir / "drifters.csv"
    if not path.exists():
        return _EMPTY_TRACKS.copy()
    df = pd.read_csv(path, dtype={"platform_id": str})
    out = pd.DataFrame(
        {
            "D_number": df["platform_id"],
            "date_UTC": pd.to_datetime(df["time_utc"], utc=True),
            "Latitude": df["lat"],
            "Longitude": df["lon"],
            "U_speed_mps": df["u_speed_mps"],
            "U_Dir_deg": df["u_dir_deg"],
            "batteryState": df["battery_state"],
        }
    )
    out["batch"] = out["D_number"].map(_batch_map(data_dir)).fillna(PRE_DEPLOY_BATCH)
    return out.sort_values(["D_number", "date_UTC"], ignore_index=True)


def read_gliders(data_dir: Path) -> list[Platform]:
    """Reconstruct the :class:`._gliders.Platform` list (id, type, time-sorted
    ``(datetime, lat, lon)`` fixes) that :func:`._geojson.gliders_geojson` and the
    forecast consume. Missing file -> ``[]``."""
    path = data_dir / "gliders.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"platform_id": str, "platform_type": str})
    df["t"] = pd.to_datetime(df["time_utc"], utc=True)
    platforms = []
    for (pid, ptype), g in df.groupby(["platform_id", "platform_type"], sort=False):
        g = g.sort_values("t", kind="stable")
        fixes = [
            (row.t.to_pydatetime(), float(row.lat), float(row.lon))
            for row in g.itertuples(index=False)
        ]
        platforms.append(Platform(pid, ptype, fixes))
    return platforms


def read_deploy_starts(data_dir: Path) -> dict[str, pd.Timestamp]:
    """Reconstruct the ``{D_number: first-free-drift time}`` map for track
    truncation (see :func:`._deploy.deployment_starts`) from ``platforms.csv``:
    drifter rows with a non-empty ``deployed_at``. Missing file -> ``{}``."""
    plat = _read_platforms(data_dir)
    if plat.empty:
        return {}
    drifters = plat[plat["platform_type"] == "drifter"]
    out = {}
    for row in drifters.itertuples(index=False):
        if isinstance(row.deployed_at, str) and row.deployed_at:
            out[str(row.platform_id)] = pd.Timestamp(row.deployed_at)
    return out


def read_awaiting(data_dir: Path) -> list[str]:
    """The D_numbers with no valid fix (drifter rows with ``n_fixes`` 0), sorted.
    Missing file -> ``[]``."""
    plat = _read_platforms(data_dir)
    if plat.empty:
        return []
    mask = (plat["platform_type"] == "drifter") & (plat["n_fixes"] == 0)
    return sorted(plat.loc[mask, "platform_id"].astype(str))


def read_agulhas(data_dir: Path) -> list[dict]:
    """Reconstruct the Agulhas fix dicts (the ``agulhas.json`` payload shape) from
    ``ship_agulhas_ii.csv``. Missing file -> ``[]``."""
    path = data_dir / "ship_agulhas_ii.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path)
    return [
        {
            "date": row.time_utc,
            "lat": float(row.lat),
            "lon": float(row.lon),
            "speed_kn": None if pd.isna(row.speed_kn) else float(row.speed_kn),
            "course_deg": None if pd.isna(row.course_deg) else float(row.course_deg),
            "status": None if pd.isna(row.status) else str(row.status),
            "area": None if pd.isna(row.area) else str(row.area),
        }
        for row in df.itertuples(index=False)
    ]


def _read_platforms(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "platforms.csv"
    if not path.exists():
        return pd.DataFrame(columns=PLATFORM_COLUMNS)
    return pd.read_csv(path, dtype={"platform_id": str})


def _batch_map(data_dir: Path) -> dict[str, str]:
    """``{D_number: batch}`` from ``platforms.csv``; falls back to the package
    roster if the metadata table is absent."""
    plat = _read_platforms(data_dir)
    if plat.empty:
        return load_deployments()
    drifters = plat[plat["platform_type"] == "drifter"]
    return {
        str(r.platform_id): (r.batch if isinstance(r.batch, str) and r.batch else PRE_DEPLOY_BATCH)
        for r in drifters.itertuples(index=False)
    }
