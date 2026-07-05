# Glider-group instruments: XSPAR buoy + seagliders + floats

The WHIRLS glider-group platforms — the **XSPAR** drifting spar buoy, the
**seagliders**, and the profiling **floats** — shown on the map alongside the
drifters and the ship, each as a latest-position marker with a track and (like
every other instrument) a current-advection forecast/hindcast. They are
**instruments** in the same top-right control as the drifter batches (see
[batches.md](batches.md)).

**Why one doc, not `xspar.md` + `floats.md` + a gliders doc.** Neither the XSPAR
(a surface spar buoy) nor the floats (autonomous profilers) are underwater
gliders, but IPSL's WHIRLS operational centre groups all three under *Gliders* —
the THREDDS tree nests them at `OBSERVATIONS/GLIDERS/{XSPAR,SEAGLIDERS,FLOATS}`.
Following that terminology keeps this map aligned with the source it draws from,
so they are documented together; the code likewise treats each as one `type`
among others converging on the same `Platform` shape, so a shared doc matches the
shared mechanism. Where the distinction matters — the marker colour, and (for
floats) a different source shape — it is called out per type below.

## Source: the WHIRLS THREDDS server

The gliders come from the IPSL WHIRLS THREDDS server
(`https://thredds-x.ipsl.fr/thredds`) — the same data the WHIRLS operational-centre
map draws. Each platform *type* is a folder with a DatasetScan `catalog.xml` and
one `*_track.csv` per platform, served from `fileServer`:

- XSPAR — `…/catalog/WHIRLS/OBSERVATIONS/GLIDERS/XSPAR/catalog.xml`
- Seagliders — `…/catalog/WHIRLS/OBSERVATIONS/GLIDERS/SEAGLIDERS/catalog.xml`

`_gliders.fetch_sources()` **auto-discovers** every CSV: it reads each catalog,
takes every `<dataset>` whose `urlPath` ends `.csv`, and downloads it from
`…/thredds/fileServer/<urlPath>` (`parse_source` then parses each into a track).
A new platform (another seaglider, a second XSPAR) therefore appears with **no
code change** — it is picked up from the catalog on the next build.

### Floats: per-institution files, and identity in a column

The floats sit under the same tree (`…/GLIDERS/FLOATS/`), whose folder holds two
kinds of CSV: one **`mr_float_<institution>_positions.csv` per float**, and a
single **aggregate `floats_track.csv`** that interleaves *every* float's fixes
(its rows are the union of the per-institution siblings). We read the
**per-institution files and skip the aggregate**: they carry the same fixes but
are **fresher** — the aggregate lags them (fewer, older fixes were observed on
it) — and skipping it avoids counting a float twice. `fetch_float_sources()`
discovers them from the FLOATS `catalog.xml` (like the gliders) and drops
`floats_track.csv` by name, so a new institution's float file appears with **no
code change**.

Floats still **break the one-CSV-per-platform identity** the glider parser
assumes: the platform is not the file name but the **`filename` column**
(`65a0_015_01_technical.txt`), so `parse_float_source` groups each file's rows by
that column's leading `_`-token (`65a0`, `6594`) — mirroring the WHIRLS
operational map's own rule — and maps that id to a label: `65a0 → UGOT`
(U. Gothenburg), `6594 → SOTON` (Southampton). An **unmapped id falls back to
itself**, so a third float appears labelled by its raw id. Grouping by the column
(rather than assuming one-float-per-file) also stays correct if a file ever
carries more than one float. Each float becomes one `Platform(type="float")`, so
from here on floats are indistinguishable from gliders to the rest of the
pipeline.

### CSV quirks — parsed by header name and detected time format

The feeds are inconsistent in two ways the parser absorbs.

**Column order varies**, including *which* of latitude/longitude comes first
(both XSPAR and the seagliders currently emit `longitude` before `latitude`), so
the parser maps columns by their **header name** (lower-cased), never by order.
It needs `time`, `latitude`, and `longitude`; a feed missing any is skipped.

**Time encoding varies per value, not per platform type** — the two seagliders
even disagree with each other — so `_parse_time` detects the format of each cell
rather than keying on the type. It handles three encodings:

- Unix epoch seconds, e.g. `1783078052.0` (one seaglider emits this);
- ISO `YYYY-MM-DD HH:MM:SS` with no offset, read as UTC (another seaglider);
- ISO with an explicit offset, e.g. `2026-07-02 00:00:00+00:00` (XSPAR).

A bare number is read as epoch; anything else is handed to `datetime.fromisoformat`
(naive → UTC, offset-aware → normalised to UTC). Line endings (LF or a stray
CR-only feed) are handled by parsing over `splitlines()`.

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
- a **`LineString`** track when it has ≥2 **deployed** fixes (a platform with a
  single deployed fix has only the marker, no line).

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

Coordinates are `[lon, lat]`. Properties carry `id` (the glider CSV filename, or a
float's mapped label) and `type` (`xspar` / `seaglider` / `float`, which keys the
client's colour and label); the
Point adds the latest fix record, the LineString a per-vertex `fixes` list aligned
with `coordinates` (each `{date_UTC, derived_speed_mps, derived_heading_deg}`).
Gliders carry no reported velocity or battery, so — unlike the drifter fix record
([trajectories.md](trajectories.md)) — only the **derived** velocity is emitted
(mean speed and initial bearing of the segment from the previous fix); the tooltip
shows a dash for the fields a glider lacks.

## Client: instruments in the batch control

The gliders join the same top-right control as the drifter batches — renamed
**Instruments** — rather than the Leaflet layer control (see
[batches.md](batches.md)). `app.js` splits `gliders.geojson` into:

- **marker groups** (`buildGliderMarkerGroups`, keyed by `type`) — one instrument
  row per platform class, each a **diamond `divIcon`** so gliders read apart from
  the drifters' circles, coloured per type (XSPAR amber `#f59e0b`, seaglider blue
  `#38bdf8`, float purple `#a855f7` — the operational map's own colours; the two
  floats share the one purple **Floats** row, like the two seagliders share theirs);
  and
- **track groups** (`buildGliderTrackGroups`, keyed by `type`) — a line plus a
  tooltip-bearing dot per fix, drawn in the **shared orange `TRACK_COLOR`** so every
  past track (drifter or glider) reads as the one **True track** layer. Instrument
  identity stays on the coloured marker, not the track.

The marker groups merge into the control's instrument rows; the track groups merge
into its **True track** overlay. So a glider's track shows only when both its
instrument row and the True-track master are checked — exactly the composition the
drifter batches use.

## Forecast / hindcast

The current-advection forecast and hindcast are computed **per instrument**, not
per drifter, so the gliders, the XSPAR, and the floats get advection lines too
(keyed by `type` so they ride their own instrument row and the Forecast/Hindcast
masters). These platforms don't drift purely with the surface current — gliders
maneuver, floats park and profile at depth — so this is a passive-drift what-if
(surface current only), meaningful for their drift phases rather than a track
prediction. See [forecast.md](forecast.md).
