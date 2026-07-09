# `/data` — the download product and the build seam

`/data` is a directory of CSVs: cleaned, unified instrument and ship tracks for
the 2026 Whirls cruise, plus the raw sources they were cleaned from and a
manifest indexing both. It is published at the site's `/data/` path and is also
the input the map itself is built from — one substrate, two consumers.

## One substrate, two consumers

The build (`whirls_cruise_map.build`) runs in two stages across this directory
as a seam:

- **ingest** fetches every live source (the drifter share, the two ships'
  position feeds, the glider-group folders on the IPSL observations portal),
  cleans and unifies them, and writes the tables described below.
- **derive** reads those same tables back — never re-fetching from the live
  upstreams — and builds the map's GeoJSON/PNG artifacts (`site/map/data/`, a
  separate tree; see [deploy.md](deploy.md)).

So a human opening `/data/drifters.csv` in a spreadsheet sees exactly what the
map was built from, not a side-export approximating it. This also means the map
is fully re-derivable from a `/data` snapshot with zero contact to the live
upstreams (the CMEMS-derived overlays aside — see below), which is what lets
`derive --tier fast` run egress-free. See `src/whirls_cruise_map/_data.py` for
the write/read functions and `plans/done/018-ingest-derive-data-seam.md` for the
design intent behind the split.

### Value over the raw share

The drifter snapshot CSVs are already public on a Nextcloud share, and the
glider/ship feeds are already public on IPSL's observations portal. `/data`
doesn't gate access to any of that — it re-publishes the same bytes under `raw/`
(see
below). Its value-add is entirely in what sits next to the raw files: the
**cleaning, unification, and annotation** — de-duplication, sentinel drops,
one shared UTC time convention, batch/deployment metadata — turned into
legible, auditable tables instead of a one-off in-memory step.

## File layout

```
drifters.csv               cleaned drifter fixes
gliders.csv                cleaned glider-group fixes (XSPAR + seagliders + floats)
ship_marion_dufresne.csv   cleaned R/V Marion Dufresne fixes
ship_agulhas_ii.csv        cleaned R/V S.A. Agulhas II fixes (+ SOG/COG/status/area)
platforms.csv              one row per platform (batch, deployed_at, coverage)
manifest.json              file index + per-file provenance + freshness stamp
index.html                 browsable listing of this directory (human landing)
raw/drifters_raw.csv       concatenated snapshot CSVs, pre-clean
raw/gliders/<id>.csv       glider-group source CSV, exactly as fetched (one per
                           glider; one per-float file — mr_float_<inst>_positions
                           .csv or uvp_float_<id>_locations.csv — per float)
raw/marion_dufresne.json   FOF positions API response, exactly as fetched
raw/agulhas_ii.csv         IPSL observations-portal CSV, exactly as fetched
```

Every cleaned per-fix table (`drifters.csv`, `gliders.csv`,
`ship_marion_dufresne.csv`, `ship_agulhas_ii.csv`) shares a core column set,
plus native extras for the sources that report more:

| Column | Meaning |
|---|---|
| `platform_id` | drifter `D-number`, glider filename id, float label (`UGOT` / `SOTON`), or `marion_dufresne` / `agulhas_ii` |
| `platform_type` | `drifter`, `xspar`, `seaglider`, `float`, or `ship` |
| `time_utc` | ISO-8601 UTC, `…Z`, second precision |
| `lat`, `lon` | decimal degrees |

Native extras:

- `drifters.csv` adds `u_speed_mps, u_dir_deg, battery_state` (reported, not
  relied upon before deployment — see below).
- `ship_agulhas_ii.csv` adds `speed_kn, course_deg, status, area` (reported
  SOG/COG plus a moving/stopped flag and free-text area).
- `gliders.csv` and `ship_marion_dufresne.csv` carry only the core columns —
  neither source reports extra motion or state fields.

The two ships stay two separate files rather than one `ships.csv`: the Agulhas
carries SOG/COG/status/area the Marion Dufresne's API does not, so a merged
table would be half the columns blank for one vessel or the other.

### Row order and incremental download

`drifters.csv` is ordered by `(time_utc, platform_id)` — chronological, then by
id to break ties — **not** grouped by platform. This is a download-transport
choice, not a semantic one. Because the newest fixes always sit at the end of
the file, a rebuild only *appends* rows there, so a colleague on a
bandwidth-limited link (the cruise's at-sea VSAT case) can fetch just the new
tail with an HTTP range request — `curl -C - -o drifters.csv <url>` resumes from
the local byte count — instead of re-downloading the whole growing file. The map
build is indifferent to the order: `read_drifters` re-sorts by
`(platform_id, time_utc)` on the way in, so this is purely the download
product's concern.

The guarantee is best-effort, not absolute. A fix that arrives late with an old
timestamp inserts mid-file and shifts the byte prefix; likewise a from-scratch
rebuild that restated an old value would. A range client must therefore detect a
changed prefix — the simplest checks being *did the file shrink* (`HEAD` →
`Content-Length` below the local size) or *did the `ETag` lineage break* — and
fall back to a full download when it sees one. The alternative that removes even
that caveat (a persistent, strictly append-only event log, dedup-on-read) is a
larger producer change; the time-sort is the low-effort step that makes the
common case — new fixes only — cheap to sync today.

`platforms.csv` is one row per platform, not per fix — the place per-platform
metadata lives once instead of being repeated down every row of a per-fix
table:

| Column | Meaning |
|---|---|
| `platform_id`, `platform_type` | as above |
| `batch` | deployment batch (drifters only; empty for gliders/ships) |
| `deployed_at` | first free-drift fix time (drifters only; empty if not yet detected) |
| `first_fix`, `last_fix` | coverage window |
| `n_fixes` | fix count; an awaiting drifter (no valid fix yet) has `n_fixes` 0 and empty `first_fix`/`last_fix` |

## Cleaning rules

Each rule below is auditable against `raw/`: the raw file and the cleaned file
sit side by side, so a de-dup or a drop can be checked by re-running the same
logic over what's published. The rules live in code, not just here — this
section points at the modules rather than duplicating their docstrings.

- **Drifters** (`_clean.py`). The canonical identity of a fix is
  `(D_number, date_UTC)`; the same fix recurs across snapshots, so
  `_clean.clean` de-duplicates on that pair after parsing `date_UTC`. Rows with
  `Latitude`/`Longitude` equal to the sentinel `-99999` ("no fix yet") are kept
  in the cleaned table (so `awaiting` can still see the platform) but dropped
  from `tracks` before `drifters.csv` is written. `D_number` is forced to
  string so it matches the deployment roster's JSON (hence string) keys, and
  `batch` is joined from `deployments.json` via `_clean.load_deployments`
  (`pre_deploy` for anything not yet rostered — see
  [batches.md](batches.md)).
- **Gliders** (`_gliders.py`). Column order (and even which of
  latitude/longitude comes first) varies by feed, so `_parse_csv` maps by
  header name. Time encoding varies **per value**, not per platform type — one
  seaglider emits Unix epoch seconds, another emits naive ISO read as UTC, XSPAR
  emits offset-aware ISO, and the SeaExplorer emits day-first `DD/MM/YYYY` — so
  `_parse_time` detects the format of each cell rather than keying on platform
  type; all converge on the same UTC `time_utc` convention as every other source.
- **Floats** (`_gliders.py`, `fetch_float_sources` / `parse_float_source`). The
  floats live under the same `GLIDERS` tree on the observations portal. We ingest
  the per-float position files and **skip** the folder's aggregate
  `floats_track.csv` (the same fixes interleaved, but it lags the per-float
  files). Two float CSV schemas are read: `mr_float_<institution>_positions.csv`
  (time column `time`, identity in a `filename` column) and
  `uvp_float_<id>_locations.csv` (time column `utc_time`, no `filename` column —
  identity is the `<id>` in the file name). Identity maps to a label
  (`65a0 → UGOT`, `6594 → SOTON`; unmapped ids such as the UVP `6596` / `6597`
  keep their raw id), landing in `gliders.csv` as `platform_type` `float`. See
  [gliders.md](gliders.md).
- **Agulhas II** (`_agulhas.py`). `reported_at` carries no timezone; it is
  assumed UTC because the file's own `scraped_at_utc` column is UTC and the
  whole app is UTC. `speed_kn` is blank (empty, not zero) when the vessel is
  reported stopped.
- **Deployment detection** (`_deploy.py`). `deployment_starts` compares each
  drifter's fixes against the Marion Dufresne track by distance
  (`NEAR_SHIP_KM`, conservative: the cut sits after the *last* fix within range,
  so nothing vessel-attached leaks into the free track) and surfaces the first
  free-drift fix time as `deployed_at` in `platforms.csv`. A drifter never seen
  near the vessel is left untruncated (full track, no `deployed_at`); this
  detection runs in ingest because it needs the MD track anyway, which ingest
  already fetches.

## The boundary: what's in `/data`, what's map-only

`/data` holds the **full, annotated tracks** — every valid fix, plus `batch`
and `deployed_at` as annotations, never a physical truncation. A download user
gets everything and decides what to do with it.

The map's own views over the same data — `tracks.geojson`'s truncation at
`deployed_at` (the "True track" free-drift segment), `latest.geojson`'s
last-fix-only view, the current-speed/vorticity rasters, the forecast/hindcast
overlays — are **rendering** decisions, computed by `derive` into
`site/map/data/`, not written back into `/data`. Truncating in `/data` itself
would throw away information a download user might want (e.g. the transit
leg); computing it in derive keeps that choice at the layer that actually
needs a specific view.

**`/data` is observations only.** No CMEMS model field — surface currents
(u/v), the ζ/f vorticity raster, forecast/hindcast advection — ever lands
here, regardless of how useful it might be to a downstream user. Those are
model-derived map overlays, a different kind of artifact from an observed
track, and `derive`'s CMEMS-fetching (`--tier slow`) already has a separate,
best-effort path that doesn't share this directory. Keeping this boundary firm
is also what keeps `derive --tier fast` egress-free: it reads only `/data` and
touches no network.

## manifest.json

One JSON object per build:

```json
{
  "built_at": "2026-07-04T12:33:25Z",
  "files": [
    {
      "name": "drifters.csv",
      "kind": "cleaned",
      "source": "https://cloud.geomar.de/s/as5DjLdynsMNapt/download",
      "rows": 11497,
      "columns": ["platform_id", "platform_type", "time_utc", "lat", "lon",
                   "u_speed_mps", "u_dir_deg", "battery_state"]
    }
  ]
}
```

- `built_at` is the ingest run's timestamp (ISO-8601 UTC), the same freshness
  signal `build.json` gives the map.
- Each entry's `kind` is `raw`, `cleaned`, or `metadata` (`platforms.csv`).
- `source` is the **provenance URL** — the exact upstream the file was
  fetched from — so a reader can go check the cleaning against the live
  source, not just the raw sibling checked into `/data` itself.
- `rows`/`columns` are present for CSV tables; a `raw/` entry that publishes a
  source verbatim as text (a glider CSV, the MD JSON, the Agulhas CSV) carries
  only `name`/`kind`/`source` — it isn't parsed into rows by ingest, so there's
  no row count to report.
