# Wave gliders

*Implemented. See [docs/gliders.md](../../docs/gliders.md) (wave gliders fold into
the glider-group doc) and [docs/data.md](../../docs/data.md) for the current
state.*

Add the **wave gliders** the WHIRLS operational map (observations.ipsl.fr)
recently gained, rendered here alongside the drifters, gliders, floats, and
ships as a new `waveglider` instrument type. This is the long-deferred
`WAVEGLIDERS/` follow-up flagged in
[020](020-observations-portal-csv-source.md) ("Add the `waveglider` type
once `WAVEGLIDERS/` carries data") — the folder now carries data.

## Which tracks the operational map shows

The operational map draws **two** wave gliders, and its bundle configures them
explicitly (they are not auto-discovered upstream):

- **`melktert`** — `…/GLIDERS/WAVEGLIDERS/melktert_track.csv`, a plain
  `time,longitude,latitude` CSV (epoch-second times, `longitude` before
  `latitude`).
- **`wg1169`** — `…/GLIDERS/WAVEGLIDERS/wg1169_WHIRLS_Cruise_L1.nc`, a **NetCDF**
  (an L1 product with rich telemetry). The operational map reads it via THREDDS
  OPeNDAP, pulling only `latitude`/`longitude` (it sets `date:null` — it does not
  bother with time). The `.nc` also carries a CF `time` coordinate
  (`minutes since 2026-07-01 06:50:00`, 10-min grid, 1457 steps).

Both are near the study region (lon ~11–12.5°E, lat ~−38…−36°). We include both.

## How each is ingested here

**`melktert` — free.** It is a CSV in the same shape the glider parser already
handles, so adding `("waveglider", …/GLIDERS/WAVEGLIDERS/)` to `_gliders._GROUPS`
picks it up through the existing autoindex discovery (`fetch_sources` →
`parse_source`) with **no other change** — epoch time, name-mapped `longitude`
before `latitude`, and the raw-CSV publish to `data/raw/gliders/melktert.csv` all
fall out of the shared path.

**`wg1169` — a NetCDF path.** The CSV autoindex scan only matches `.csv`, so
`wg1169` needs its own fetch/parse, added beside the floats' bespoke pair:

- `fetch_waveglider_nc_sources()` scans the `WAVEGLIDERS/` autoindex for `.nc`
  links (a regex that matches `.nc` but **not** the sibling `.ncml` NcML pointer)
  and downloads each as **bytes**, so a second wave-glider `.nc` appears with no
  code change.
- `parse_waveglider_nc(name, data)` opens the bytes with **xarray** (a hard
  project dep already; imported lazily so the CSV path stays stdlib-only), reads
  `time`/`latitude`/`longitude`, drops non-finite fixes, and returns the same
  `Platform(type="waveglider")` shape. Identity is the leading `_`-token of the
  file name (`wg1169_WHIRLS_Cruise_L1.nc` → `wg1169`), matching the operational
  map's own id.

We read the `.nc` **as a static file from the observations portal**, not via
THREDDS OPeNDAP like the operational map does — [020](020-observations-portal-csv-source.md)
moved this project off the heavy, intermittently failing THREDDS server onto the
portal, and the portal serves the `.nc` directly (`Content-Type:
application/x-netcdf`, ~1.7 MB). So this keeps the whole ingest on one host and
one reliability story, and — because the `.nc` carries `time` — our `wg1169`
track is actually *richer* than the operational map's (which omits time).

The leading vessel-transit prune (`_drop_leading_transit`, 2.0 m/s) applies as-is
and does the right thing for both: `melktert` starts near-stationary (max ~1.7
m/s, nothing cut — full track), while `wg1169` starts at ~5 m/s (ship carried it
out — the leading transit is correctly pruned to the free-drift remainder).

## Downstream: all generic

Everything past ingest keys off the platform `type`, so wave gliders ride the
existing machinery unchanged:

- `write_gliders` / `read_gliders` fold `waveglider` rows into `gliders.csv`; the
  raw `.nc` is published under `data/raw/gliders/` (a new `write_raw_bytes`, since
  the existing raw publish is text-only).
- `_geojson.gliders_geojson` emits the marker + track features (type in the
  properties); the per-instrument forecast/hindcast (`_forecast._glider_heads`,
  `batch = p.type`) covers them automatically.
- The client needs **one** line: a `GLIDER_STYLES.waveglider` entry —
  `#ec4899` (the operational map's own wave-glider track colour) + label "Wave
  gliders". `gliderStyle`, `buildGliderMarkerGroups`, `buildGliderTrackGroups`,
  `buildAdvectionGroups`, `batchLabel`, and `instrumentOrder` are already
  type-generic. The instrument row slots alphabetically (seaglider · waveglider ·
  xspar · Floats-pinned-last); no ordering change.

## Touch list

- `src/whirls_cruise_map/_gliders.py` — `_GROUPS` entry; `_nc_datasets`,
  `fetch_waveglider_nc_sources`, `parse_waveglider_nc`; docstring/comments.
- `src/whirls_cruise_map/_data.py` — `write_raw_bytes`; docstring note.
- `src/whirls_cruise_map/build.py` — best-effort NetCDF ingest block.
- `site/map/app.js` — `GLIDER_STYLES.waveglider` + comment.
- `docs/gliders.md` — wave gliders in the source list + a NetCDF note; update the
  "WAVEGLIDERS empty" line.
- `tests/test_gliders_discovery.py` — `_nc_datasets` (matches `.nc`, skips
  `.ncml`) and `parse_waveglider_nc` (round-trips a tiny in-memory NetCDF).
