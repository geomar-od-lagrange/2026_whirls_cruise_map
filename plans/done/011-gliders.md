# Gliders: XSPAR buoy + seagliders

> **Done.** Implemented — see [docs/gliders.md](../../docs/gliders.md) (and the
> per-instrument forecast/hindcast in [docs/deploy_tool.md](../../docs/deploy_tool.md),
> the Instruments control in [docs/batches.md](../../docs/batches.md)).

Add the WHIRLS glider platforms — the **XSPAR** drifting spar buoy and the
**seagliders** — to the cruise map, alongside the drifters and the ship. Source:
the IPSL WHIRLS THREDDS server, the same data the operational centre map
(`observations.ipsl.fr/aeris/whirls`) draws.

## Source

Each platform type is a folder on THREDDS with a DatasetScan `catalog.xml` and
one `*_track.csv` per platform, served from `fileServer`:

- XSPAR — `.../catalog/WHIRLS/OBSERVATIONS/GLIDERS/XSPAR/catalog.xml`
  (currently `xspar_xeos_track.csv`)
- Seagliders — `.../catalog/WHIRLS/OBSERVATIONS/GLIDERS/SEAGLIDERS/catalog.xml`
  (currently `sg284_track.csv`)

THREDDS host: `https://thredds-x.ipsl.fr/thredds`. A dataset's `urlPath`
(e.g. `WHIRLS/OBSERVATIONS/GLIDERS/XSPAR/xspar_xeos_track.csv`) appended to
`.../thredds/fileServer/` is the CSV URL.

**Auto-discovery** (user choice): parse each `catalog.xml` and ingest *every*
`*_track.csv` it lists, so new platforms appear with no code change.

### CSV quirks (both handled by header-driven parsing)

The two formats differ, so parse by *header name*, not column position:

- XSPAR: `Time,Latitude,Longitude`, **CR-only** line endings, date
  `M/D/YY H:MM` (e.g. `7/2/16 0:02`).
- Seaglider: `time,longitude,latitude` (**lon before lat**), date ISO
  `YYYY-MM-DD HH:MM:SS` (UTC).

XSPAR date parsing mirrors the operational site's `parseXsparGliderDate`: read
`M/D/YY H:MM` as UTC; expand a 2-digit year (`16`→`2016`); and if the result is
`< 2020`, use the **current UTC year** instead. So `7/2/16 0:02` → today
`2026-07-02T00:02Z`. (The upstream year field is unreliable; the fallback is
what makes the operational map show the fix as current.)

## Why build-time, not client-live

The ship polls live client-side because its API is CORS-open and it moves
continuously. Gliders instead go through the **build** (like the drifters):
THREDDS `fileServer` CORS is not guaranteed for browser fetch, and gliders
surface only every few hours, so a rebuilt static artifact is both simpler and
reliable. One more best-effort step in `build.py`; nothing new on the network
path the client depends on.

## Shape

- `_gliders.py` — `fetch_gliders() -> list[Platform]`, best-effort (any failure
  → skip that platform / return `[]`, exactly like `_ship.fetch_track`). A
  `Platform` is `{id, type, fixes: [(time, lat, lon)]}` (time-sorted, tz-aware
  UTC). `type` is `"xspar"` or `"seaglider"`.
- `_geojson.gliders_geojson(platforms)` — one FeatureCollection: per platform a
  latest `Point` and, if ≥2 fixes, a `LineString` track. Reuses
  `_segment_motion` for per-fix derived speed/heading, so glider popups match
  the drifter/ship idiom (time + derived velocity; gliders carry no reported
  velocity or battery). Properties: `id`, `type`, and the fix record(s).
- `build.py` — a best-effort glider step writing `site/data/gliders.geojson`,
  between the tracks and the currents steps.
- `site/app.js` — `buildGliderGroups(geojson)` → `{type: featureGroup}`; each
  type is one **layer-control** overlay (not the batch control — gliders aren't
  deployment batches). XSPAR amber `#f59e0b`, seaglider blue `#38bdf8` (the
  operational map's own colours). Diamond `divIcon` marker so gliders read apart
  from the drifters' circles; track line + per-fix dots + popup, mirroring
  `buildTrackGroups`. On by default.
- `site/style.css` — a `.glider-marker` class (diamond), analogous to
  `.ship-marker`.
- `docs/gliders.md` — the standalone doc; move this plan to `plans/done/`.

## Out of scope

- Depth / dive profiles, CTD or other sensor data — positions and tracks only.
- Live client-side polling (see "Why build-time").
