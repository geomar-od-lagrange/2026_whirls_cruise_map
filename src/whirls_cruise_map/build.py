"""Build the cruise-map artifacts in two stages across the ``/data`` seam.

**ingest** fetches every instrument & ship track (drifters, gliders, R/V Marion
Dufresne, R/V S.A. Agulhas II), cleans and unifies them, and writes the download
``/data`` tree — human-inspectable CSVs plus their raw sources and a
``manifest.json`` (see :mod:`._data`, ``docs/data.md``).

**derive** reads those tables back and builds the map's ``data/`` artifacts:

- *fast* (no secrets, no egress): ``latest.geojson``, ``tracks.geojson``,
  ``awaiting.json``, ``gliders.geojson``, ``agulhas.json``, ``build.json``.
- *slow* (CMEMS, needs a Copernicus login): ``currents.json`` + ``speed.png``
  (+meta), ``vorticity.png`` (+meta), ``forecast.geojson``, ``hindcast.geojson``,
  ``inertial_field.json``.

The two stages write disjoint trees, every write is atomic (``*.tmp`` +
``os.replace``), and each layer is best-effort, so a dead upstream drops one
artifact and leaves the rest (and the last-good file) untouched.

Usage::

    python -m whirls_cruise_map.build                     # ingest + derive (all)
    python -m whirls_cruise_map.build --stage ingest
    python -m whirls_cruise_map.build --stage derive --tier fast
    python -m whirls_cruise_map.build --stage derive --tier slow

Output roots default to the Pages layout and are overridable by
``--data`` / ``WHIRLS_DATA`` (downloads) and ``--map`` / ``WHIRLS_SITE_DATA``
(the map's data), so a CronJob can write to PVC mounts.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import (
    _agulhas,
    _clean,
    _currents,
    _data,
    _deploy,
    _fetch,
    _forecast,
    _geojson,
    _gliders,
    _inertial,
    _ship,
    _vorticity,
)
from ._clean import PRE_DEPLOY_BATCH

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "site" / "data"            # download /data (ingest output)
SITE_DATA = REPO_ROOT / "site" / "map" / "data"  # the map's data (derive output)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj) -> None:
    _data.atomic_write_text(path, json.dumps(obj))


# --------------------------------------------------------------------------- #
# ingest: live upstreams -> cleaned /data tables (+ raw + manifest)
# --------------------------------------------------------------------------- #
def _platform_records(
    tracks,
    awaiting: list[str],
    deploy_starts: dict,
    gliders: list,
    md_track: list,
    agulhas: list[dict],
) -> list[dict]:
    """One inventory record per platform for ``platforms.csv`` — drifters (with
    fixes, then awaiting), gliders, then the two ships."""
    roster = _clean.load_deployments()
    records = []
    for d_number, g in tracks.groupby("D_number"):
        times = g["date_UTC"]
        start = deploy_starts.get(d_number)
        records.append(
            {
                "platform_id": d_number,
                "platform_type": "drifter",
                "batch": g["batch"].iloc[-1],
                "deployed_at": _data.iso_utc(start) if start is not None else "",
                "first_fix": _data.iso_utc(times.min()),
                "last_fix": _data.iso_utc(times.max()),
                "n_fixes": len(g),
            }
        )
    for d_number in awaiting:
        records.append(
            {
                "platform_id": d_number,
                "platform_type": "drifter",
                "batch": roster.get(d_number, PRE_DEPLOY_BATCH),
                "deployed_at": "",
                "first_fix": "",
                "last_fix": "",
                "n_fixes": 0,
            }
        )
    for p in gliders:
        ts = [f[0] for f in p.fixes]
        records.append(
            {
                "platform_id": p.id,
                "platform_type": p.type,
                "batch": "",
                "deployed_at": "",
                "first_fix": _data.iso_utc(min(ts)),
                "last_fix": _data.iso_utc(max(ts)),
                "n_fixes": len(p.fixes),
            }
        )
    if md_track:
        ts = [f[0] for f in md_track]
        records.append(
            {
                "platform_id": "marion_dufresne",
                "platform_type": "ship",
                "batch": "",
                "deployed_at": "",
                "first_fix": _data.iso_utc(min(ts)),
                "last_fix": _data.iso_utc(max(ts)),
                "n_fixes": len(md_track),
            }
        )
    if agulhas:
        records.append(
            {
                "platform_id": "agulhas_ii",
                "platform_type": "ship",
                "batch": "",
                "deployed_at": "",
                "first_fix": agulhas[0]["date"],
                "last_fix": agulhas[-1]["date"],
                "n_fixes": len(agulhas),
            }
        )
    return records


def ingest(data_dir: Path) -> None:
    """Fetch, clean, and persist every instrument & ship track into ``data_dir``."""
    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    # Drifters (the core source). A failed share pull raises and leaves the
    # last-good /data untouched; every other source below is best-effort.
    with tempfile.TemporaryDirectory() as tmp:
        concat = _clean.concat_snapshots(_fetch.fetch_snapshots(Path(tmp)))
    entries.append(_data.write_raw_drifters(data_dir, concat))
    clean = _clean.clean(concat)
    tracks = _clean.tracks(clean)
    awaiting = _clean.awaiting(clean)
    entries.append(_data.write_drifters(data_dir, tracks))
    print(
        f"drifters: {tracks['D_number'].nunique()} with fixes, "
        f"{len(awaiting)} awaiting first fix"
    )

    # R/V Marion Dufresne: fetched for deployment detection and as a /data
    # product. Best-effort — a failure yields no track and no truncation.
    md_raw = _ship.fetch_raw()
    md_track = _ship.parse(md_raw) if md_raw is not None else []
    if md_raw is not None:
        entries.append(_data.write_raw_text(data_dir, "marion_dufresne.json", md_raw, _ship.POSITIONS_URL))
    if md_track:
        entries.append(_data.write_ship_md(data_dir, md_track))
    deploy_starts = _deploy.deployment_starts(tracks, md_track)
    print(
        f"MD track: {len(md_track)} fixes; "
        f"deployment detected for {len(deploy_starts)} drifters"
    )

    # Glider-group platforms (XSPAR + seagliders + floats), from WHIRLS THREDDS,
    # all folded into gliders.csv. Each source CSV is published raw before
    # parsing. Gliders and floats are fetched in separate best-effort blocks (one
    # failing can't suppress the other), then written together; a total failure
    # of both leaves no gliders.csv.
    gliders = []
    try:
        for src in _gliders.fetch_sources():
            entries.append(_data.write_raw_text(data_dir, f"gliders/{src.id}.csv", src.text, _gliders.THREDDS))
            p = _gliders.parse_source(src)
            if p is not None:
                gliders.append(p)
        print(f"gliders: {len(gliders)} platforms")
    except Exception as exc:
        print(f"WARNING: glider ingest failed: {exc}")

    # Floats: the per-institution position CSVs under the FLOATS catalog (the
    # aggregate floats_track.csv is skipped — same fixes, but it lags). Each
    # source is published raw before parsing; identity comes from its filename
    # column (see _gliders). Best-effort, so a float failure can't suppress the
    # gliders written above.
    try:
        floats = []
        for src in _gliders.fetch_float_sources():
            entries.append(_data.write_raw_text(data_dir, f"gliders/{src.id}.csv", src.text, _gliders.THREDDS))
            floats.extend(_gliders.parse_float_source(src))
        gliders.extend(floats)
        print(f"floats: {len(floats)} platforms")
    except Exception as exc:
        print(f"WARNING: float ingest failed: {exc}")

    if gliders:
        entries.append(_data.write_gliders(data_dir, gliders))

    # R/V S.A. Agulhas II, from IPSL THREDDS CSV (no-CORS; baked here).
    agulhas = []
    try:
        a_raw = _agulhas.fetch_raw()
        if a_raw is not None:
            entries.append(_data.write_raw_text(data_dir, "agulhas_ii.csv", a_raw, _agulhas.CSV_URL))
            agulhas = _agulhas.parse(a_raw)
            entries.append(_data.write_ship_agulhas(data_dir, agulhas))
        print(f"agulhas: {len(agulhas)} fixes")
    except Exception as exc:
        print(f"WARNING: Agulhas ingest failed: {exc}")

    entries.append(
        _data.write_platforms(
            data_dir,
            _platform_records(tracks, awaiting, deploy_starts, gliders, md_track, agulhas),
        )
    )
    built_at = _stamp()
    _data.write_manifest(data_dir, entries, built_at)
    _data.write_index(data_dir, entries, built_at)
    print(f"ingest: wrote {len(entries)} files + index.html to {data_dir}")


# --------------------------------------------------------------------------- #
# derive: /data tables -> the map's artifacts
# --------------------------------------------------------------------------- #
def _derive_fast(data_dir: Path, map_dir: Path) -> None:
    """Egress-free map layers from the local /data tables. Each layer is
    independent — one failing leaves the others (and the last-good file)."""
    # Stamp freshness first so the sidebar shows data age even if a layer fails.
    _write_json(map_dir / "build.json", {"built_at": _stamp()})

    try:
        tracks = _data.read_drifters(data_dir)
        _write_json(map_dir / "latest.geojson", _geojson.latest_geojson(tracks))
        _write_json(
            map_dir / "tracks.geojson",
            _geojson.tracks_geojson(tracks, _data.read_deploy_starts(data_dir)),
        )
        _write_json(map_dir / "awaiting.json", _data.read_awaiting(data_dir))
        print(f"derive-fast: positions for {tracks['D_number'].nunique()} drifters")
    except Exception as exc:
        print(f"WARNING: drifter layers failed: {exc}")

    # Always write gliders.geojson (empty FeatureCollection if none), for parity
    # with agulhas.json — the client fetches it optionally either way.
    try:
        gliders = _data.read_gliders(data_dir)
        _write_json(map_dir / "gliders.geojson", _geojson.gliders_geojson(gliders))
        print(f"derive-fast: {len(gliders)} glider platforms")
    except Exception as exc:
        print(f"WARNING: gliders.geojson failed: {exc}")

    try:
        _write_json(map_dir / "agulhas.json", _data.read_agulhas(data_dir))
    except Exception as exc:
        print(f"WARNING: agulhas.json failed: {exc}")


def _derive_slow(data_dir: Path, map_dir: Path) -> None:
    """CMEMS-derived overlays (needs a Copernicus login). Each render is
    independent; forecast/hindcast advect the fresh drifter & glider positions
    read from /data (read only where needed, so a bad CSV can't skip currents)."""
    # One single-time field feeds the coarse vector grid (trails), the speed
    # raster, and the ζ/f raster — each independent, and none needs the tracks.
    field = None
    try:
        field = _currents.fetch_field()
    except Exception as exc:
        print(f"WARNING: CMEMS field fetch failed, skipping currents overlays: {exc}")

    if field is not None:
        try:
            _write_json(map_dir / "currents.json", _currents.to_velocity_json(field))
            png, meta = _currents.to_speed_png(field)
            _data.atomic_write_bytes(map_dir / "speed.png", png)
            _write_json(map_dir / "currents_meta.json", meta)
            print(
                f"wrote currents.json + speed.png "
                f"(valid {meta['valid_time']}, vmax {meta['vmax']:.2f} {meta['units']})"
            )
        except Exception as exc:
            print(f"WARNING: currents render failed: {exc}")

        try:
            vpng, vmeta = _vorticity.to_vorticity_png(field)
            _data.atomic_write_bytes(map_dir / "vorticity.png", vpng)
            _write_json(map_dir / "vorticity_meta.json", vmeta)
            print(
                f"wrote vorticity.png (valid {vmeta['valid_time']}, "
                f"|ζ/f| clip {vmeta['vmax']:.2f})"
            )
        except Exception as exc:
            print(f"WARNING: vorticity render failed: {exc}")

    # A separate hourly window advects the forecast/hindcast particle through the
    # current at its own clock time (so the path traces the inertial loop), and
    # feeds the near-inertial animation decomposition.
    window = None
    try:
        window = _currents.fetch_field_window()
    except Exception as exc:
        print(f"WARNING: CMEMS window fetch failed, skipping forecast/hindcast: {exc}")

    if window is not None:
        tracks = _data.read_drifters(data_dir)
        gliders = _data.read_gliders(data_dir)
        try:
            forecast = _forecast.forecast_geojson(window, tracks, gliders)
            _write_json(map_dir / "forecast.geojson", forecast)
            print(f"wrote forecast.geojson ({len(forecast['features'])} forecasts)")
        except Exception as exc:
            print(f"WARNING: forecast step failed: {exc}")

        try:
            hindcast = _forecast.hindcast_geojson(window, tracks, gliders)
            _write_json(map_dir / "hindcast.geojson", hindcast)
            print(f"wrote hindcast.geojson ({len(hindcast['features'])} hindcasts)")
        except Exception as exc:
            print(f"WARNING: hindcast step failed: {exc}")

        try:
            decomp = _inertial.decompose(window)
            _write_json(map_dir / "inertial_field.json", _inertial.to_inertial_field_json(decomp))
            print(f"wrote inertial_field.json (valid {decomp.attrs['t_ref']})")
        except Exception as exc:
            print(f"WARNING: inertial field step failed: {exc}")


def derive(data_dir: Path, map_dir: Path, tier: str = "all") -> None:
    map_dir.mkdir(parents=True, exist_ok=True)
    if tier in ("fast", "all"):
        _derive_fast(data_dir, map_dir)
    if tier in ("slow", "all"):
        _derive_slow(data_dir, map_dir)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build the cruise-map data artifacts.")
    ap.add_argument("--stage", choices=["ingest", "derive", "all"], default="all")
    ap.add_argument(
        "--tier",
        choices=["fast", "slow", "all"],
        default="all",
        help="which derive layers to build (ignored for --stage ingest)",
    )
    ap.add_argument("--data", default=os.environ.get("WHIRLS_DATA", str(DATA)))
    ap.add_argument("--map", default=os.environ.get("WHIRLS_SITE_DATA", str(SITE_DATA)))
    args = ap.parse_args(argv)

    data_dir, map_dir = Path(args.data), Path(args.map)
    if args.stage in ("ingest", "all"):
        ingest(data_dir)
    if args.stage in ("derive", "all"):
        derive(data_dir, map_dir, args.tier)


if __name__ == "__main__":
    main()
