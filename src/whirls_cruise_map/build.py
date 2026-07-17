"""Build the cruise-map artifacts in two stages across the ``/data`` seam.

**ingest** fetches every instrument & ship track (drifters, gliders, R/V Marion
Dufresne, R/V S.A. Agulhas II), cleans and unifies them, and writes the download
``/data`` tree — human-inspectable CSVs plus their raw sources and a
``manifest.json`` (see :mod:`._data`, ``docs/data.md``).

**derive** reads those tables back and builds the map's ``data/`` artifacts:

- *fast* (no secrets, no egress): ``latest.geojson``, ``tracks.geojson``,
  ``awaiting.json``, ``gliders.geojson``, ``agulhas.json``, ``build.json``.
- *slow* (CMEMS, needs a Copernicus login): the absolute-time ``speed_<t>Z.webp`` and
  ``flowvis_<t>Z.webp`` (static flow streamlines) frames (+``currents_meta.json``) and
  ``vorticity_<t>Z.webp`` frames (+meta), incrementally (re)rendered over the frame span
  (see ``_currents.plan_render``), and ``inertial_field.json``.

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
import gc
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import (
    _agulhas,
    _clean,
    _currents,
    _data,
    _deploy,
    _fetch,
    _field_store,
    _geojson,
    _gliders,
    _inertial,
    _ship,
    _time,
    _vorticity,
)
from ._clean import PRE_DEPLOY_BATCH

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "site" / "data"            # download /data (ingest output)
SITE_DATA = REPO_ROOT / "site" / "map" / "data"  # the map's data (derive output)


def _stamp() -> str:
    return _time.now_iso()


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
    awaiting = _clean.awaiting(clean, tracks)
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

    # Glider-group platforms (XSPAR + seagliders + wave gliders + floats), from the
    # WHIRLS observations portal, all folded into gliders.csv. Each source is
    # published raw before parsing. The CSV platforms, the NetCDF-only wave glider,
    # and the floats are fetched in separate best-effort blocks (one failing can't
    # suppress the others), then written together; a total failure of all leaves no
    # gliders.csv. The CSV block covers XSPAR, the seagliders, and the CSV wave
    # glider (melktert) via the shared autoindex discovery.
    gliders = []
    try:
        for src in _gliders.fetch_sources():
            entries.append(_data.write_raw_text(data_dir, f"gliders/{src.id}.csv", src.text, _gliders.BASE))
            p = _gliders.parse_source(src)
            if p is not None:
                gliders.append(p)
        print(f"gliders: {len(gliders)} platforms")
    except Exception as exc:
        print(f"WARNING: glider ingest failed: {exc}")

    # Wave gliders published only as a NetCDF (wg1169): read as static portal files
    # (not THREDDS — see _gliders), published raw as bytes before parsing. The CSV
    # wave glider (melktert) already came through fetch_sources above; this covers
    # only the .nc siblings. Best-effort, so a NetCDF failure can't suppress the
    # CSV platforms.
    try:
        wavegliders = []
        for name, data in _gliders.fetch_waveglider_nc_sources():
            entries.append(_data.write_raw_bytes(data_dir, f"gliders/{name}", data, _gliders.BASE))
            p = _gliders.parse_waveglider_nc(name, data)
            if p is not None:
                wavegliders.append(p)
        gliders.extend(wavegliders)
        print(f"wave gliders (NetCDF): {len(wavegliders)} platforms")
    except Exception as exc:
        print(f"WARNING: wave-glider NetCDF ingest failed: {exc}")

    # Floats: the per-institution position CSVs under the FLOATS folder (the
    # aggregate floats_track.csv is skipped — same fixes, but it lags). Each
    # source is published raw before parsing; identity comes from its filename
    # column (see _gliders). Best-effort, so a float failure can't suppress the
    # gliders written above.
    try:
        floats = []
        for src in _gliders.fetch_float_sources():
            entries.append(_data.write_raw_text(data_dir, f"gliders/{src.id}.csv", src.text, _gliders.BASE))
            floats.extend(_gliders.parse_float_source(src))
        gliders.extend(floats)
        print(f"floats: {len(floats)} platforms")
    except Exception as exc:
        print(f"WARNING: float ingest failed: {exc}")

    if gliders:
        entries.append(_data.write_gliders(data_dir, gliders))

    # R/V S.A. Agulhas II, from the IPSL observations-portal CSV (baked here for
    # resilience, though the portal is CORS-open; see _agulhas).
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


def _write_frames(map_dir: Path, frames: list[dict]) -> None:
    """Atomically write each ``{file, image}`` render frame under ``map_dir`` — the one
    write loop the three raster renderers (speed, flow, vorticity) share."""
    for fr in frames:
        _data.atomic_write_bytes(map_dir / fr["file"], fr["image"])


def _render_currents(map_dir: Path, shading, grid: list, to_render: list, now: datetime) -> None:
    """Speed + flow rasters, the static land/sea basemap, and ``currents_meta.json`` off
    the fetched ``shading`` window. Owns its ``try/except`` so a failure here leaves the
    vorticity render and last-good files intact."""
    try:
        frames, meta = _currents.to_speed_frames(shading, to_render)
        _write_frames(map_dir, frames)
        _write_frames(map_dir, _currents.to_flowvis_frames(shading, to_render))
        # Static land/sea basemap (#29): one gray-land/blue-sea WebP baked from the
        # field's own NaN land pattern, co-registered with the shading bounds. Time-
        # invariant, so it rides along with the (already-fetched) window at no extra
        # egress; the frontend draws it in a pane below the shadings, reusing meta.bounds.
        landmask, _ = _currents.to_landmask_webp(shading)
        _data.atomic_write_bytes(map_dir / "landmask.webp", landmask)
        meta["landmask"] = "landmask.webp"
        meta["valid_time"] = _currents.nearest_valid_time(grid, now)
        meta["frames"] = _currents.frame_manifest("speed", grid)
        meta["flow_frames"] = _currents.frame_manifest("flowvis", grid, ext="webp")
        _write_json(map_dir / "currents_meta.json", meta)
        print(
            f"rendered {len(frames)}/{len(grid)} speed + flow frames "
            f"(now {meta['valid_time']}, vmax {meta['vmax']:.2f} {meta['units']})"
        )
    except Exception as exc:
        print(f"WARNING: currents render failed: {exc}")


def _render_vorticity(map_dir: Path, shading, grid: list, to_render: list, now: datetime) -> None:
    """ζ/f rasters + ``vorticity_meta.json`` off the same ``shading`` window. Owns its
    ``try/except`` (independent of the currents render above)."""
    try:
        vframes, vmeta = _vorticity.to_vorticity_frames(shading, to_render)
        _write_frames(map_dir, vframes)
        vmeta["valid_time"] = _currents.nearest_valid_time(grid, now)
        vmeta["frames"] = _currents.frame_manifest("vorticity", grid)
        _write_json(map_dir / "vorticity_meta.json", vmeta)
        print(
            f"rendered {len(vframes)}/{len(grid)} vorticity frames "
            f"(now {vmeta['valid_time']}, |ζ/f| clip {vmeta['vmax']:.2f})"
        )
    except Exception as exc:
        print(f"WARNING: vorticity render failed: {exc}")


def _render_shadings(map_dir: Path, refetch_all: bool) -> None:
    """Fetch the 6-hourly shading window once and render every overlay that rides it
    (speed/flow, ζ/f), then prune stale frames. The window is a **local** here, so it is
    released the moment this returns — freeing the ~225 MB before the inertial step
    builds its own, with no manual ``del``/``gc.collect()`` (the spike the old code
    hand-managed is now just a normal scope exit).

    The frame grid spans every 12 h step from FIELD_TMIN (floored to 00Z) through the
    6-hourly product's forecast edge; only frames not yet final (missing, recent, or
    forecast) are (re)rendered, and the fetch covers just that span. One fetch feeds the
    coarse vector grid (trails), the speed raster and the ζ/f raster; none needs the
    tracks. See _currents.plan_render / docs/currents.md."""
    try:
        now = datetime.now(timezone.utc)
        t_lo = _currents.frame_tmin()
        # --refetch-all forces the whole span to re-render (CMEMS reprocessing behind the
        # analysis edge): treat the on-disk frame set as empty so plan_render re-plans
        # every frame; pruning still drops anything no longer in the span.
        existing = set() if refetch_all else _currents.existing_frame_times(map_dir)
        fetch_lo = _currents.first_pending_frame(t_lo, existing, now)
        shading = _currents.fetch_shading_window(
            t_lo=fetch_lo, t_hi=now + timedelta(hours=_currents.FORECAST_REACH_H)
        )
        t_hi = _currents.window_frame_edge(shading, t_lo)
        grid = _currents.frame_span(t_lo, t_hi)
        to_render = _currents.plan_render(grid, existing, now)
    except Exception as exc:
        print(f"WARNING: CMEMS field fetch failed, skipping currents overlays: {exc}")
        return
    if not grid:
        return

    _render_currents(map_dir, shading, grid, to_render, now)
    _render_vorticity(map_dir, shading, grid, to_render, now)

    # Prune retired offset-named frames and any absolute frame no longer in the span
    # so stale artifacts never linger (meta/non-frame files are left untouched).
    try:
        removed = _currents.prune_stale_frames(map_dir, grid)
        if removed:
            print(f"pruned {len(removed)} stale frame file(s)")
    except Exception as exc:
        print(f"WARNING: frame pruning failed: {exc}")


def _render_inertial(map_dir: Path) -> None:
    """Load a fresh hourly window (the shading window is already freed by the time this
    runs) and write the near-inertial decomposition. Owns its ``try/except``.

    A separate hourly window feeds the near-inertial animation decomposition, sampling
    the current at its own clock time. The deployment forecast API reads the field store
    directly instead (whirls_cruise_map._api), so this window only serves this one
    consumer here — see _currents.py."""
    now = datetime.now(timezone.utc)
    try:
        window = _field_store.load_window(
            t0=now - timedelta(hours=_currents.WINDOW_BACK_H),
            t1=now + timedelta(hours=_currents.WINDOW_FWD_H),
        )
    except Exception as exc:
        print(f"WARNING: field store window load failed, skipping inertial field: {exc}")
        return
    try:
        # The decomposition relies on the window spanning under an inertial period (so
        # the joint least-squares keeps mean vs NI separated — see _inertial.decompose).
        # ``window`` is already sized to WINDOW_BACK_H/WINDOW_FWD_H (24 h) plus one
        # bracket hour each end (_field_store.load_window brackets outside [t0, t1]);
        # slice off the extra top-bracket hour so the decomposition gets exactly the
        # 24 h + low-bracket span it was tuned against. Low edge unchanged, so
        # t_ref-nearest-now is unchanged too.
        span_h = _currents.WINDOW_BACK_H + _currents.WINDOW_FWD_H
        hi = window["time"].values[0] + np.timedelta64(span_h + 1, "h")
        narrow = window.sel(time=slice(None, hi))
        decomp = _inertial.decompose(narrow)
        _write_json(map_dir / "inertial_field.json", _inertial.to_inertial_field_json(decomp))
        print(f"wrote inertial_field.json (valid {decomp.attrs['t_ref']})")
    except Exception as exc:
        print(f"WARNING: inertial field step failed: {exc}")


def _derive_slow(data_dir: Path, map_dir: Path, refetch_all: bool = False) -> None:
    """CMEMS-derived overlays (needs a Copernicus login). A thin orchestrator: top up the
    incremental per-day field store, render the shading overlays off one shared window
    (released before the next step), then the near-inertial field. Each render is
    independent — one failing leaves the others and the last-good file — so each owns its
    own ``try/except`` in the helper it lives in."""
    # Top up the field store first (fetch every missing or non-final day; see
    # _field_store) so the window reads below are served entirely from disk — no CMEMS
    # fetch on the render path. A killed run resumes here next time; --refetch-all forces
    # a full re-pull.
    try:
        manifest = _field_store.update_store(refetch_all=refetch_all)
        n_final = sum(1 for d in manifest["days"].values() if d["final"])
        print(f"field store: {len(manifest['days'])} day(s) on disk, {n_final} final")
    except Exception as exc:
        print(f"WARNING: field store update failed: {exc}")

    _render_shadings(map_dir, refetch_all)
    # `_render_shadings`'s shading window (and its render locals) are unbound on its
    # return, so a plain refcount frees them here — but the slow derive is OOM-sensitive
    # (plans/045-slow-derive-oom.md) and the shading window is an xarray Dataset that can
    # carry an internal reference cycle, which only a collection reclaims. One forced
    # collection at the phase boundary guarantees the ~225 MB is gone before the inertial
    # step loads its own window (the spike the old in-orchestrator `del`/`gc` was written
    # against). The per-variable hand-management is gone; this is a single, documented
    # phase-boundary release.
    gc.collect()
    _render_inertial(map_dir)


def derive(data_dir: Path, map_dir: Path, tier: str = "all", refetch_all: bool = False) -> None:
    map_dir.mkdir(parents=True, exist_ok=True)
    if tier in ("fast", "all"):
        _derive_fast(data_dir, map_dir)
    if tier in ("slow", "all"):
        _derive_slow(data_dir, map_dir, refetch_all=refetch_all)


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
    ap.add_argument(
        "--refetch-all",
        action="store_true",
        help=(
            "force a full re-pull of every day in the incremental field store "
            "(see _field_store), instead of just missing/non-final days; the "
            "escape hatch if the rollover assumption (CMEMS revises nothing "
            "behind the analysis edge) is ever found to not hold"
        ),
    )
    ap.add_argument("--data", default=os.environ.get("WHIRLS_DATA", str(DATA)))
    ap.add_argument("--map", default=os.environ.get("WHIRLS_SITE_DATA", str(SITE_DATA)))
    args = ap.parse_args(argv)

    data_dir, map_dir = Path(args.data), Path(args.map)
    if args.stage in ("ingest", "all"):
        ingest(data_dir)
    if args.stage in ("derive", "all"):
        derive(data_dir, map_dir, args.tier, refetch_all=args.refetch_all)


if __name__ == "__main__":
    main()
