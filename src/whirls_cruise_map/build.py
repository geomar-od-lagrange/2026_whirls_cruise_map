"""Build the static cruise-map data artifacts from the drifter share.

Reads every snapshot CSV in the Nextcloud share, derives the drifter track DB,
and writes the JSON the Leaflet site consumes into ``site/data/``:

- ``latest.geojson``      one Point per drifter at its most-recent valid fix
- ``tracks.geojson``      one LineString per drifter over its time-sorted fixes
- ``awaiting.json``       D_numbers with no valid fix yet
- ``currents.json``       coarse leaflet-velocity u/v grid for the flow trails
- ``speed.png``           near-native CMEMS surface-speed raster (Mercator-warped)
- ``currents_meta.json``  bounds, vmax, valid-time and colourbar for the client

Everything is rebuilt from a fresh full-zip pull each run; no caching.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import _clean, _currents, _fetch, _geojson

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_DATA = REPO_ROOT / "site" / "data"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj))


def main() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)

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

    # Currents are best-effort: positions/tracks still build if CMEMS is down.
    # One field -> coarse vector grid (trails) + near-native speed raster + meta.
    try:
        field = _currents.fetch_field()
        _write_json(SITE_DATA / "currents.json", _currents.to_velocity_json(field))
        png, meta = _currents.to_speed_png(field)
        (SITE_DATA / "speed.png").write_bytes(png)
        _write_json(SITE_DATA / "currents_meta.json", meta)
        print(
            f"wrote currents.json + speed.png "
            f"(valid {meta['valid_time']}, vmax {meta['vmax']:.2f} {meta['units']})"
        )
    except Exception as exc:
        print(f"WARNING: currents step failed, skipping currents artifacts: {exc}")


if __name__ == "__main__":
    main()
