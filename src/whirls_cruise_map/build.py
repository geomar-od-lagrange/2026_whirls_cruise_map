"""Build the static cruise-map data artifacts from the drifter share.

Reads every snapshot CSV in the Nextcloud share, derives the drifter track DB,
and writes the JSON the Leaflet site consumes into ``site/data/``:

- ``latest.geojson``      one Point per drifter at its most-recent valid fix
- ``tracks.geojson``      one LineString per drifter over its time-sorted fixes
- ``awaiting.json``       D_numbers with no valid fix yet
- ``currents.json``       coarse leaflet-velocity u/v grid for the flow trails
- ``speed.png``           near-native CMEMS surface-speed raster (Mercator-warped)
- ``currents_meta.json``  bounds, vmax, valid-time and colourbar for the client
- ``forecast.geojson``    per-drifter current-advection track to 6 h (1/3/6 h marks)
- ``ftle.geojson``        simplified SPASSO FTLE ridge contour (LCS) line strings
- ``ftle_meta.json``      valid-time, units and level for the FTLE legend
- ``build.json``          UTC timestamp of this build (sidebar data-freshness)

Everything is rebuilt from a fresh full-zip pull each run; no caching.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import _clean, _currents, _fetch, _forecast, _ftle, _geojson

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

    _write_json(SITE_DATA / "latest.geojson", _geojson.latest_geojson(tracks))
    _write_json(SITE_DATA / "tracks.geojson", _geojson.tracks_geojson(tracks))
    _write_json(SITE_DATA / "awaiting.json", awaiting)
    print(
        f"wrote positions for {tracks['D_number'].nunique()} drifters; "
        f"{len(awaiting)} awaiting first fix"
    )

    # Currents + forecast are best-effort: positions/tracks still build if CMEMS
    # is down. One field feeds three artifacts — the coarse vector grid (trails),
    # the near-native speed raster + meta, and the per-drifter advection forecast
    # — each independent, so one failing does not skip the others.
    currents_valid = None
    field = None
    try:
        field = _currents.fetch_field()
    except Exception as exc:
        print(f"WARNING: CMEMS field fetch failed, skipping currents + forecast: {exc}")

    if field is not None:
        try:
            _write_json(SITE_DATA / "currents.json", _currents.to_velocity_json(field))
            png, meta = _currents.to_speed_png(field)
            (SITE_DATA / "speed.png").write_bytes(png)
            _write_json(SITE_DATA / "currents_meta.json", meta)
            currents_valid = meta["valid_time"]
            print(
                f"wrote currents.json + speed.png "
                f"(valid {meta['valid_time']}, vmax {meta['vmax']:.2f} {meta['units']})"
            )
        except Exception as exc:
            print(
                f"WARNING: currents render failed, skipping currents artifacts: {exc}"
            )

        # Per-drifter current-advection forecast (true field, NaN land); its own
        # best-effort step so a currents-render failure doesn't suppress it.
        try:
            forecast = _forecast.forecast_geojson(field, tracks)
            _write_json(SITE_DATA / "forecast.geojson", forecast)
            print(
                f"wrote forecast.geojson "
                f"({len(forecast['features'])} drifter forecasts)"
            )
        except Exception as exc:
            print(f"WARNING: forecast step failed, skipping forecast.geojson: {exc}")

    # FTLE overlay (best-effort, independent): the SPASSO field nearest the speed
    # valid-time, or now if currents are unavailable.
    try:
        target = (
            datetime.strptime(currents_valid, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            if currents_valid
            else datetime.now(timezone.utc)
        )
        result = _ftle.fetch_ftle(target)
        if result is None:
            print("no FTLE within 24h of the target time, skipping ftle.geojson")
        else:
            ftle_field, ftle_valid = result
            geojson, meta = _ftle.to_ftle_geojson(ftle_field, ftle_valid)
            _write_json(SITE_DATA / "ftle.geojson", geojson)
            _write_json(SITE_DATA / "ftle_meta.json", meta)
            n_lines = len(geojson["features"][0]["geometry"]["coordinates"])
            print(
                f"wrote ftle.geojson ({n_lines} ridge lines) + ftle_meta.json "
                f"(valid {meta['valid_time']}, level {meta['levels'][0]['value']:.3f} "
                f"{meta['units']})"
            )
    except Exception as exc:
        print(f"WARNING: FTLE step failed, skipping ftle artifacts: {exc}")


if __name__ == "__main__":
    main()
