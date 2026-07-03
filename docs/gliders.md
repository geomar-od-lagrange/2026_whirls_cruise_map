# Gliders: XSPAR buoy + seagliders

The WHIRLS glider platforms — the **XSPAR** drifting spar buoy and the
**seagliders** — shown on the map alongside the drifters and the ship, each as a
latest-position marker with a track and (like every other instrument) a
current-advection forecast/hindcast. They are **instruments** in the same
top-right control as the drifter batches (see [batches.md](batches.md)).

**Why one doc, not `xspar.md` + a gliders doc.** The XSPAR is a surface spar
buoy, not an underwater glider, but IPSL's WHIRLS operational centre groups both
under *Gliders* — the THREDDS tree nests them at
`OBSERVATIONS/GLIDERS/{XSPAR,SEAGLIDERS}` and the site's platform menu is a single
"Gliders" entry. Following that terminology keeps this map aligned with the source
it draws from, so the XSPAR is documented here with the seagliders rather than on
its own; the code likewise treats it as one glider `type` among others (auto-
discovered from the same catalog structure), so a shared doc matches the shared
mechanism. Where the distinction matters — the CR-only `M/D/YY` CSV, the amber
marker colour, the single-fix (marker-only) case — it is called out per type below.

## Source: the WHIRLS THREDDS server

The gliders come from the IPSL WHIRLS THREDDS server
(`https://thredds-x.ipsl.fr/thredds`) — the same data the WHIRLS operational-centre
map draws. Each platform *type* is a folder with a DatasetScan `catalog.xml` and
one `*_track.csv` per platform, served from `fileServer`:

- XSPAR — `…/catalog/WHIRLS/OBSERVATIONS/GLIDERS/XSPAR/catalog.xml`
- Seagliders — `…/catalog/WHIRLS/OBSERVATIONS/GLIDERS/SEAGLIDERS/catalog.xml`

`_gliders.fetch_gliders()` **auto-discovers** every CSV: it reads each catalog,
takes every `<dataset>` whose `urlPath` ends `.csv`, and downloads it from
`…/thredds/fileServer/<urlPath>`. A new platform (another seaglider, a second
XSPAR) therefore appears with **no code change** — it is picked up from the
catalog on the next build.

### CSV quirks — parsed by header name, not position

The two feeds differ, so the parser maps columns by their **header name**
(lower-cased), never by order:

- XSPAR: `Time,Latitude,Longitude`, **CR-only** line endings, date `M/D/YY H:MM`.
- Seaglider: `time,longitude,latitude` (**longitude before latitude**), date ISO
  `YYYY-MM-DD HH:MM:SS` (UTC).

XSPAR dates get the operational site's own fallback (`parseXsparGliderDate`):
read `M/D/YY H:MM` as UTC, expand a 2-digit year, and — because the upstream year
field is unreliable — if the result predates 2020, use the **current UTC year**
instead. So a stale-looking `7/2/16` reads as this year's fix. Seaglider dates
parse straight as ISO UTC.

## Why build-time, not client-live

Unlike the ship — which the client polls live from a CORS-open API because it
moves continuously (see [ship.md](ship.md)) — the gliders are ingested in the
**build**, like the drifters. Two reasons: THREDDS `fileServer` CORS is not
guaranteed for a browser fetch, and gliders surface only every few hours, so a
rebuilt static artifact is both simpler and reliable. It adds one best-effort step
to the build and nothing to the network path the client depends on.

Best-effort throughout: each catalog and each CSV is fetched independently, so one
dead platform can't suppress the rest, and a total failure yields no
`gliders.geojson` — the map simply omits the gliders, every other layer intact.

## Artifact: `gliders.geojson`

`_geojson.gliders_geojson` writes one `FeatureCollection`. Per platform:

- a **`Point`** at its most-recent (raw) fix; and
- a **`LineString`** track when it has ≥2 **deployed** fixes (a single-fix platform
  — the XSPAR with one report — has only the marker, no line).

### Leading vessel-transit is pruned from the track

A glider's first fixes can be the launch vessel carrying it out to the deployment
site, not the glider drifting. `_drop_leading_transit` removes that leading run:
it walks from the start while each fix's *inbound* speed exceeds
`GLIDER_TRANSIT_MPS` (2.0 m/s) and keeps from the first fix the glider reached at
its own, sub-threshold speed — its deployment. The threshold sits in the wide gap
between the two regimes: a Seaglider's horizontal speed is ~0.25 m/s (0.1–0.4 m/s
through water, up to ~1 m/s over ground with the current), while a ship steams at
several m/s (4–7 m/s seen on the transit legs). So the cut cleanly separates
carried-aboard fixes from free drift, without needing to know *which* vessel
(Marion Dufresne or Agulhas II) launched the glider — unlike the drifter rule,
this is speed-based and vessel-agnostic (contrast [trajectories.md](trajectories.md)'s
ship-proximity `_deploy`).

**Only the leading run is cut.** Once a glider is deployed, every later fix is kept
unchanged, however fast — the map shows raw, unprocessed positions, so a
post-deployment speed spike is treated as noise, not a reason to re-truncate. The
convention matches drifter truncation: the drop point (last transit fix) is
excluded, so the drawn track begins at the first free fix, whose derived velocity
is blank (it derives from nothing). A glider still being carried out — every hop
above threshold — has no free track yet and draws only its marker. The **`Point`
is always the raw latest fix**, unaffected by the prune (the latest fix is well
past deployment).

Coordinates are `[lon, lat]`. Properties carry `id` (from the CSV filename) and
`type` (`xspar` / `seaglider`, which keys the client's colour and label); the
Point adds the latest fix record, the LineString a per-vertex `fixes` list aligned
with `coordinates` (each `{date_UTC, derived_speed_mps, derived_heading_deg}`).
Gliders carry no reported velocity or battery, so — unlike the drifter fix record
([trajectories.md](trajectories.md)) — only the **derived** velocity is emitted
(mean speed and initial bearing of the segment from the previous fix); the popup
shows a dash for the fields a glider lacks.

## Client: instruments in the batch control

The gliders join the same top-right control as the drifter batches — renamed
**Instruments** — rather than the Leaflet layer control (see
[batches.md](batches.md)). `app.js` splits `gliders.geojson` into:

- **marker groups** (`buildGliderMarkerGroups`, keyed by `type`) — one instrument
  row per platform class, each a **diamond `divIcon`** so gliders read apart from
  the drifters' circles, coloured per type (XSPAR amber `#f59e0b`, seaglider blue
  `#38bdf8` — the operational map's own colours); and
- **track groups** (`buildGliderTrackGroups`, keyed by `type`) — a line plus a
  popup-bearing dot per fix, drawn in the **shared orange `TRACK_COLOR`** so every
  past track (drifter or glider) reads as the one **True track** layer. Instrument
  identity stays on the coloured marker, not the track.

The marker groups merge into the control's instrument rows; the track groups merge
into its **True track** overlay. So a glider's track shows only when both its
instrument row and the True-track master are checked — exactly the composition the
drifter batches use.

## Forecast / hindcast

The current-advection forecast and hindcast are computed **per instrument**, not
per drifter, so the gliders and the XSPAR get advection lines too (keyed by `type`
so they ride their own instrument row and the Forecast/Hindcast masters). Gliders
maneuver actively, so this is a passive-drift what-if — the surface current only —
meaningful for their drift phases rather than a track prediction. See
[forecast.md](forecast.md).
