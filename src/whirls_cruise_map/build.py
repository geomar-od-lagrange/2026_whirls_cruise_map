"""Build the static cruise-map data artifacts from the drifter share.

Reads every snapshot CSV in the Nextcloud share, derives the drifter track DB,
and writes the JSON the Leaflet site consumes into ``site/data/``:

- ``latest.geojson``  one Point per drifter at its most-recent valid fix
- ``tracks.geojson``  one LineString per drifter over its time-sorted fixes
- ``awaiting.json``   D_numbers with no valid fix yet
- ``currents.json``   leaflet-velocity grid of today's CMEMS surface currents

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
    try:
        currents = _currents.fetch_currents()
        _write_json(SITE_DATA / "currents.json", currents)
        print("wrote currents.json")
    except Exception as exc:
        print(f"WARNING: currents step failed, skipping currents.json: {exc}")


if __name__ == "__main__":
    main()
