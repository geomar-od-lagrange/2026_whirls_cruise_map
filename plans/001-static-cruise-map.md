# Static cruise map — MVP

Build a static, client-rendered map of the 2026 Whirls Cruise drifters: latest
positions, per-drifter trajectories, and today's CMEMS surface currents. Built
locally for now; GitHub Pages + Actions come later.

Decisions already taken: Leaflet + leaflet-velocity for rendering; the Nextcloud
share is the source of truth (we re-derive everything from it, no separate
archive); pixi for the environment.

## Data

### Source

Public Nextcloud share, downloaded as a single zip (a few dozen MB, fine to
re-fetch each build — no caching tooling needed):

    https://cloud.geomar.de/s/as5DjLdynsMNapt/download

Inside: `2026_whirls_drifters/<YYYYMMDDThhmmss>.csv`, one file per snapshot,
arriving up to every 5 minutes. Columns:

    D_number, date_UTC, Latitude, Longitude, U_speed_mps, U_Dir_deg, batteryState

Each row is a drifter's *latest known fix at snapshot time*. Example
(`20260622T073134.csv`): 124 drifters, most still at `-99999/-99999` (awaiting
first transmission — staged in Cape Town / Table Bay), a handful with real fixes
near `-33.90, 18.43`.

### From snapshots to tracks

The canonical identity of a fix is **(D_number, date_UTC)**, not
(drifter, snapshot): `date_UTC` is the time of the drifter's position, and a
given fix may appear in a single snapshot or repeat across several. De-duplicating
on `(D_number, date_UTC)` recovers the true set of fixes either way, with no
assumption about how often drifters report.

Build the track DB by concatenating every snapshot's rows and de-duplicating on
`(D_number, date_UTC)`. Cleaning rules:

- Drop rows where `Latitude == -99999` or `Longitude == -99999` (no fix). Keep a
  side list of "awaiting first fix" drifter IDs for the UI.
- Parse `date_UTC` (`21-Jun-2026 11:26:09`, UTC) to a real timestamp.
- **Carry but don't yet use `U_speed_mps` / `U_Dir_deg`.** In the pre-deployment
  snapshot these hold implausible values (e.g. `5e19`); they may become
  meaningful once drifters are in the water. Draw no conclusions from the current
  pre-deployment fixes. Map currents come from CMEMS regardless.
- Assign every drifter `batch = "pre_deploy"` until it is deployed and joins a
  real batch (see Deferred). The column is always present, never null.

Result: a tidy long table, one row per `(D_number, date_UTC)` with position and
battery state. This is the "track DB." For MVP it lives only in memory during a
build; an on-disk parquet cache for incremental ingest is a later optimization
(see BACKLOG), not needed while the share stays small.

### Build artifacts (written to `site/data/`)

- `latest.geojson` — one `Point` per drifter at its most-recent valid fix.
  Properties: `D_number`, `date_UTC`, `batteryState`, `batch`.
- `tracks.geojson` — one `LineString` per drifter over its time-sorted fixes.
  (With a single snapshot these are degenerate/one-point; they fill in as
  snapshots accumulate.)
- `awaiting.json` — list of `D_number`s with no valid fix yet (the `-99999`
  rows), for the sidebar.
- `currents.json` — leaflet-velocity grid (see below).

## Currents (CMEMS)

Today's surface currents, analysis/forecast at t=0, as an animated
leaflet-velocity overlay.

- Product: GLOBAL Ocean Physics Analysis & Forecast, surface `uo`/`vo`
  (1/12°). Subset via the `copernicusmarine` toolbox to the cruise bbox, surface
  depth, nearest time to "now".
- Bbox: the canonical cruise study region from the prep repo
  (`2026_whirls_cruise_prep/archetypes/notebooks/001_study_region.py`:
  `0..25 E`, `-45..-25 N`, stable campaign-wide) widened by 10° on every side →
  **lon `-10..35`, lat `-55..-15`** (in −180..180 for the map). Kept as one
  constant in `_currents.py`; revisit if the canonical region moves.
- Auth: rely on the local `copernicusmarine` login (already cached on this
  machine). In CI later, log in from `CMEMS_USERNAME`/`CMEMS_PASSWORD` secrets.
- Convert the subset (xarray) to leaflet-velocity's two-component JSON: a `u`
  object and a `v` object, each `{header, data}` with `nx/ny/lo1/la1/dx/dy` from
  the grid and a row-major `data` array. Watch the latitude ordering — the
  format expects `la1` at the north edge with data running top-left→right.

## Rendering (`site/`, Leaflet)

Static HTML/JS/CSS that fetches the three JSON artifacts. No build step for the
JS (vendored or CDN-pinned Leaflet + leaflet-velocity).

- Basemap: Esri Ocean Basemap (bathymetry context suits a cruise); OSM fallback.
  Mind attribution.
- Layers / controls:
  - **Latest positions** — circle markers, popups (`D_number`, last fix time,
    battery, lat/lon). On by default.
  - **Trajectories** — `LineString` overlay, toggled off by default (matches
    "toggle for showing trajectories").
  - **Currents** — leaflet-velocity layer, toggleable.
  - **Awaiting first fix** — sidebar list of drifters with no position yet
    (from `awaiting.json`), so staged-but-silent units stay visible. No geometry.
- Keep the control panel as a small module so a **batch filter** can slot in
  later without restructuring (markers already carry `batch`).
- View fits to the bounding box of valid fixes on load.

## Repo layout

```
pixi.toml
src/whirls_cruise_map/
  __init__.py
  _fetch.py        # download + unzip share -> raw CSV paths
  _clean.py        # parse, sentinel-filter, (D_number,date_UTC) dedup -> DataFrame
  _currents.py     # copernicusmarine subset -> leaflet-velocity JSON
  _geojson.py      # DataFrame -> latest.geojson + tracks.geojson
  build.py         # orchestrate: fetch -> clean -> geojson + currents -> site/data/
site/
  index.html  app.js  style.css
  vendor/          # pinned leaflet + leaflet-velocity (or CDN)
  data/            # generated artifacts (gitignored)
```

`build.py` is the only public entry point; `_`-prefixed modules are internal and
free to churn. `docs/` stays prose-only — the built site does not live there.

## Environment (pixi)

conda-forge: `python`, `pandas`, `xarray`, `netcdf4`, `numpy`, `copernicusmarine`.
Tasks:

- `build` — `python -m whirls_cruise_map.build`
- `serve` — static server over `site/` for local viewing
- `dev` — build then serve

## Phasing

In scope for this plan: fetch → track DB → `latest`/`tracks` GeoJSON + CMEMS
`currents.json`, and the Leaflet app rendering all three locally.

## Deferred (see BACKLOG / ROADMAP)

- **Batches** — deployments of dozens of drifters in one coordinated maneuver
  (circle / fence pattern, up to a day); drifters sit in `pre_deploy` until then.
  IDs known per batch as they deploy. We only reserve the `batch` attribute and a
  control seam now; the selection/filter UI comes once a batch source exists. The
  prep repo's `patterns/events.csv` enumerates 11 provisional circle+fence sites —
  a likely reference for batch positions/identities.
- **Automation** — GitHub Actions cron rebuild + Pages deploy; CMEMS via secrets.
- **Track DB cache** — not now. A complete rebuild from the share each run is
  the design; only add an on-disk parquet / incremental ingest if and when
  rebuild time actually becomes a problem.

## Notes

- **Share robustness.** Each build re-derives the full track DB from a single
  atomic full-zip pull, so we never depend on any one CSV being stable — whether
  upstream appends new snapshots or overwrites files is irrelevant, as long as
  the zip carries the accumulated snapshots.