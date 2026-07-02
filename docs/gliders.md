# Gliders: XSPAR buoy + seagliders

The WHIRLS glider platforms — the **XSPAR** drifting spar buoy and the
**seagliders** — shown on the map alongside the drifters and the ship, each as a
latest-position marker with a track and (like every other instrument) a
current-advection forecast/hindcast. They are **instruments** in the same
top-right control as the drifter batches (see [batches.md](batches.md)).

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

- a **`Point`** at its most-recent fix; and
- a **`LineString`** track when it has ≥2 fixes (a single-fix platform — the XSPAR
  with one report — has only the marker, no line).

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
