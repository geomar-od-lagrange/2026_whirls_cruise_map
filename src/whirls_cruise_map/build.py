"""Build the static cruise-map data artifacts from the drifter share.

Reads every snapshot CSV in the Nextcloud share, derives the drifter track DB,
and writes the JSON the Leaflet site consumes into ``site/data/``:

- ``latest.geojson``      one Point per drifter at its most-recent valid fix
- ``tracks.geojson``      one LineString per drifter over its time-sorted fixes
- ``awaiting.json``       D_numbers with no valid fix yet
- ``currents.json``       coarse leaflet-velocity u/v grid for the flow trails
- ``speed.png``           near-native CMEMS surface-speed raster (Mercator-warped)
- ``currents_meta.json``  bounds, vmax, valid-time and colourbar for the client
- ``forecast.geojson``    per-drifter current-advection track to +6 h (1/3/6 h marks)
- ``hindcast.geojson``    per-drifter current-advection back-track to -6 h (1/3/6 h marks)
- ``build.json``          UTC timestamp of this build (sidebar data-freshness)

Everything is rebuilt from a fresh full-zip pull each run; no caching.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import _clean, _currents, _deploy, _fetch, _forecast, _geojson, _gliders, _ship

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_DATA = REPO_ROOT / "site" / "data"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj))


def main() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)

    # Stamp the build up front so the sidebar can show data age even if a later
    # best-effort layer fails; the client reads it from build.json.
    _write_json(
        SITE_DATA / "build.json",
        {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    )

    with tempfile.TemporaryDirectory() as tmp:
        csv_paths = _fetch.fetch_snapshots(Path(tmp))
        raw = _clean.load_raw(csv_paths)

    tracks = _clean.tracks(raw)
    awaiting = _clean.awaiting(raw)

    # Deployment detection (best-effort): fetch the vessel track and find where
    # each drifter detached from it, so the trajectory shows only the free drift.
    # A failed fetch yields no starts -> full tracks, exactly as before.
    ship_track = _ship.fetch_track()
    deploy_starts = _deploy.deployment_starts(tracks, ship_track)
    print(
        f"vessel track: {len(ship_track)} fixes; "
        f"deployment detected for {sum(1 for _ in deploy_starts)} drifters"
    )

    _write_json(SITE_DATA / "latest.geojson", _geojson.latest_geojson(tracks))
    _write_json(
        SITE_DATA / "tracks.geojson",
        _geojson.tracks_geojson(tracks, deploy_starts),
    )
    _write_json(SITE_DATA / "awaiting.json", awaiting)
    print(
        f"wrote positions for {tracks['D_number'].nunique()} drifters; "
        f"{len(awaiting)} awaiting first fix"
    )

    # Glider platforms (XSPAR buoy + seagliders), from the WHIRLS THREDDS server.
    # Best-effort and independent of the drifter share: a dead THREDDS host yields
    # no gliders.geojson and the map simply omits them.
    # Fetched here and reused by the forecast/hindcast steps below, so gliders and
    # drifters share one advection pass. An empty list on failure leaves both the
    # gliders.geojson and the advection instrument-set exactly as drifters-only.
    gliders = []
    try:
        gliders = _gliders.fetch_gliders()
        _write_json(SITE_DATA / "gliders.geojson", _geojson.gliders_geojson(gliders))
        print(
            f"wrote gliders.geojson ({len(gliders)} platforms: "
            f"{', '.join(f'{p.id}[{len(p.fixes)}]' for p in gliders) or 'none'})"
        )
    except Exception as exc:
        print(f"WARNING: glider fetch failed, skipping gliders.geojson: {exc}")

    # Currents overlays are best-effort: positions/tracks still build if CMEMS is
    # down. One single-time field feeds the two overlay artifacts — the coarse
    # vector grid (trails) and the near-native speed raster + meta — each
    # independent, so one failing does not skip the others.
    field = None
    try:
        field = _currents.fetch_field()
    except Exception as exc:
        print(f"WARNING: CMEMS field fetch failed, skipping currents overlays: {exc}")

    if field is not None:
        try:
            _write_json(SITE_DATA / "currents.json", _currents.to_velocity_json(field))
            png, meta = _currents.to_speed_png(field)
            (SITE_DATA / "speed.png").write_bytes(png)
            _write_json(SITE_DATA / "currents_meta.json", meta)
            print(
                f"wrote currents.json + speed.png "
                f"(valid {meta['valid_time']}, vmax {meta['vmax']:.2f} {meta['units']})"
            )
        except Exception as exc:
            print(
                f"WARNING: currents render failed, skipping currents artifacts: {exc}"
            )

    # Time-dependent advection field: a separate hourly CMEMS window (independent of
    # the single-time overlay field above), so the forecast/hindcast particle is
    # pushed by the current at its own clock time and traces the inertial loop the
    # model already carries — not the straight streamline of a frozen snapshot.
    # Forecast and hindcast are independent best-effort steps.
    window = None
    try:
        window = _currents.fetch_field_window()
    except Exception as exc:
        print(f"WARNING: CMEMS window fetch failed, skipping forecast/hindcast: {exc}")

    if window is not None:
        try:
            forecast = _forecast.forecast_geojson(window, tracks, gliders)
            _write_json(SITE_DATA / "forecast.geojson", forecast)
            print(
                f"wrote forecast.geojson "
                f"({len(forecast['features'])} instrument forecasts)"
            )
        except Exception as exc:
            print(f"WARNING: forecast step failed, skipping forecast.geojson: {exc}")

        try:
            hindcast = _forecast.hindcast_geojson(window, tracks, gliders)
            _write_json(SITE_DATA / "hindcast.geojson", hindcast)
            print(
                f"wrote hindcast.geojson "
                f"({len(hindcast['features'])} instrument hindcasts)"
            )
        except Exception as exc:
            print(f"WARNING: hindcast step failed, skipping hindcast.geojson: {exc}")


if __name__ == "__main__":
    main()
