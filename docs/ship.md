# ship tracks

Live positions and tracks for the two cruise vessels — **R/V Marion Dufresne**
and **R/V S.A. Agulhas II** — drawn over the drifter map. Both use the same
on-map rendering (a plain coloured track and a boat marker, both following the
app clock) and the same sidebar readout; they differ only in
**where the data comes from** and, as a
consequence, **whether it is fetched live in the browser or baked at build time**.

That fork is the central design fact here, so it comes first.

## Two sources, two fetch paths

| | Marion Dufresne | S.A. Agulhas II |
|---|---|---|
| Source | Flotte Océanographique Française localisation API (JSON) | IPSL WHIRLS observations-portal `agulhas_positions.csv` |
| CORS | open (`Access-Control-Allow-Origin: *`) | open (`Access-Control-Allow-Origin: *`) |
| Fetched | **live in `app.js`**, polled every 5 min | **baked** into `site/map/data/agulhas.json` by derive (also published cleaned at `/data/ship_agulhas_ii.csv` by ingest, see [data.md](data.md)) |
| Cadence | ~10-min fixes, near-real-time | hourly scrape of myshiptracking.com |
| Motion | **derived** client-side (API reports none) | **reported** (speed/course in the CSV) |
| Extra fields | met (sea/air temp, pressure, wind) | status (moving/stopped), area |

Both sources are CORS-open, so the fetch path is a deliberate choice, not a
constraint. The Marion Dufresne is a **live** layer because it is a
near-real-time feed of a continuously moving vessel — polling keeps it current
between rebuilds, exactly as the IPSL operational map does.

The Agulhas is **baked** instead, for two reasons that outweigh a live fetch.
First, freshness costs nothing: its CSV is an **hourly scrape** of
myshiptracking.com (its `source_url` / `scraped_at_utc` columns), only as current
as its own scraper however we fetch it. Second, baking adds resilience — the map
keeps showing the last-good track when the portal is briefly unreachable, and the
client depends on no third-party host at page load. So it is fetched
**server-side by the Python build**, published cleaned at
`/data/ship_agulhas_ii.csv` by ingest, and read back and written to
`site/map/data/agulhas.json` by derive, which the client loads same-origin like
every other map artifact.

(IPSL also serves this CSV from its THREDDS server, but that host is heavier and
its `fileServer` sends no `Access-Control-Allow-Origin`; the observations portal
is lighter, more reliable, and CORS-open. The bake is kept regardless, for the
freshness/resilience reasons above.)

## Marion Dufresne — the live layer

### Data source

The French Oceanographic Fleet (Flotte Océanographique Française) publishes ship
positions through a public localisation API:

```
GET https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions
      ?startDate=<ISO-8601>&endDate=<ISO-8601>
```

- `MD` is the Marion Dufresne. `/api/v2/vessels` lists the whole fleet;
  `/api/v2/vessels/MD` returns vessel metadata.
- With **no** date params the endpoint returns the single most-recent fix.
- The response is a flat JSON array, ordered in time, on a **fixed 10-minute
  grid**:

  ```json
  [
    {
      "lat": -33.9023,
      "lon": 18.4258,
      "data": {
        "seatemp": 14.70,
        "airtemp": 14.00,
        "pressure": 1011.50,
        "truewinddir": 336.00,
        "truewindspeed": 19.40
      },
      "date": "2026-06-28T00:00:00.000+0000"
    }
  ]
  ```

The API is **open**: `Access-Control-Allow-Origin: *`, no key, no auth — so a
static page can fetch it directly from the browser. A wide window returns the
full track in one request (thousands of points over a couple of months returned
without an obvious cap), and history reaches back well before the cruise.

#### Time resolution

The 10-minute grid is the **maximum resolution this API offers** — it is the
native cadence, not a default we can override. Probing confirmed it: gaps are
exactly 600 s with no jitter over a full day, a one-hour window returns only the
:00/:10/:20… grid points, and resolution-style query parameters
(`interval`, `step`, `resolution`, `sampling`, `raw`, …) are all silently
ignored and return the identical grid. There is no published OpenAPI/Swagger to
suggest a finer endpoint. The ship logs position and underway data onboard at a
much higher rate (typically 1 Hz), but that is **not** served by this real-time
localisation service; it is archived post-cruise through SISMER/Coriolis (the
vessel's `sismerId` is `MARION2`) or available directly from the cruise data
manager. For a track line at these zoom levels 10-minute fixes are ample — at
~12 kn the ship advances under 4 km between them.

#### Field units

`seatemp`/`airtemp` are °C, `pressure` is hPa, and `truewinddir` is degrees —
unambiguous from the values. The `truewindspeed` **unit is not specified by the
API** and is not asserted here: the tooltip and sidebar show the bare number. Do
not relabel it as knots or m/s without confirming with the source.

### Course & speed are derived, not reported

The API exposes **no ship speed-over-ground or course-over-ground** — a record is
only `lat`/`lon`/`date` plus the met fields above (`truewinddir`/`truewindspeed`
are *wind*, not vessel motion). So the tooltip/sidebar "Speed (derived)" and
"Heading (derived)" are computed client-side from a **track segment**: the
great-circle distance between two fixes over their time gap, and the initial
great-circle bearing between them (degrees true, with a 16-point compass label).
The same per-segment derivation labels every fix on the track, not just the
latest — each segment's hover tooltip shows its own.

Speed is shown in **both knots and m/s** (`12.3 kn / 6.33 m/s`), so it reads on
the same scale as the drifters' m/s velocities. Below ~0.5 kn — about 150 m over
a 10-min step, comparable to GPS scatter — the bearing is unreliable, so the
heading is suppressed (the near-zero speed is still shown); this is what a vessel
on station or moored looks like. The heading row is **always present**, showing
`NA` when suppressed or when there is no prior fix to derive a bearing from.
Because it is a single-segment difference the value is instantaneous and a little
jumpy; smoothing over several fixes would steady it at the cost of lag, and is
deferred.

### Why client-side and live

Every other layer on this map is a build artifact written into `site/map/data/`
by derive. The Marion Dufresne is the deliberate exception — it is fetched
**live in `app.js`**, not baked at build time.

The reason is freshness under the intended hosting. The site is a static bundle
destined for a scheduled-rebuild GitLab Pages deploy, which has no server at view
time. A baked position would therefore freeze the vessel between rebuilds, which
defeats the point of a "where is the ship now" layer. Fetching live keeps the
marker current to the API's ~10-minute cadence regardless of build schedule, and
the open CORS policy exists precisely to allow this (the IPSL map does the same).
The Agulhas cannot take this path — its source is not CORS-open (above) — so it
is baked; the trade-off is accepted there because its source is only hourly
anyway.

The trade-off for the live layer is a runtime dependency on a third-party host.
It is contained by the same graceful-fetch pattern the rest of the client uses: a
failed request resolves to an empty list, so an outage simply stops the marker
advancing — it never throws and never blanks the map.

- **Live refresh** polls every 5 minutes, requesting only the window *since the
  last known fix* and appending the new tail (deduplicated by timestamp), so
  polling cost stays flat as the track grows rather than re-pulling the whole
  history each time.
- **Initial load** fetches the whole cruise window, `cruiseStart … now`.
  `cruiseStart` (`app.js`, the `SHIP` config) is `2026-06-28T00:00:00Z`, where the
  MD ship track crops; it is a one-line constant to adjust.

### Also fetched at build time, for deployment detection

Separately from the live client layer, the **build**'s ingest stage pulls a
one-shot snapshot of the Marion Dufresne track (`_ship.fetch_raw` + `_ship.parse`;
the raw JSON is also archived to `data/raw/marion_dufresne.json`) to detect where
each drifter detached from the vessel and truncate its trajectory there (see
[trajectories.md](trajectories.md)). This is a historical computation — the
detachment already happened — so a build-time snapshot is right, and it is
best-effort: a failed fetch just skips truncation (full tracks). Drifters detach
from the Marion Dufresne, not the Agulhas, so this is Marion-Dufresne-only.

## Agulhas II — the baked layer

### Data source

IPSL publishes the Agulhas track as a single CSV on the WHIRLS observations
portal (the same host the gliders come from, see [gliders.md](gliders.md)):

```
https://observations.ipsl.fr/aeris/whirls/data/observations/SHIPS/agulhas_positions.csv
```

```
scraped_at_utc,reported_at,lat,lon,speed_kn,course_deg,status,area,source_url
2026-07-03T09:29:34Z,2026-07-03 08:47,-35.59410,15.37993,6.9,268,START Moving,SW OF CAPE TOWN,https://www.myshiptracking.com/vessels/sa-agulhas-ii-...
2026-07-03T10:30:01Z,2026-07-03 10:00,-35.50905,15.55999,,284,STOP Moving,SW OF CAPE TOWN,https://www.myshiptracking.com/vessels/sa-agulhas-ii-...
```

- `reported_at` carries **no timezone** (`YYYY-MM-DD HH:MM`); it is treated as
  **UTC** — the file's own `scraped_at_utc` is UTC and the whole app is UTC.
- `speed_kn` / `course_deg` are **reported** SOG/COG, so — unlike the Marion
  Dufresne — nothing is derived; `speed_kn` is blank when the vessel is stopped
  (rendered as a dash).
- `status` (`START Moving` / `STOP Moving`) and `area` are free text shown as-is.
- There is **no met data** — the underway readout the Marion Dufresne panel shows
  has no Agulhas equivalent.

`_agulhas.fetch_raw()` / `_agulhas.parse()` fetch and parse the CSV server-side
in ingest (best-effort, like the gliders: any failure yields no fixes and the
map simply omits the vessel), which writes the cleaned result to
`/data/ship_agulhas_ii.csv` (see [data.md](data.md)); derive then reads that
table back and writes `site/map/data/agulhas.json`. That artifact is a **plain
JSON array of fix objects** deliberately shaped like the
live FOF API's array (`{date, lat, lon, …}`), so the client's ship renderer
consumes both with no conversion. An empty result still writes `[]`, keeping the
client's optional fetch uniform.

### Behaviour

The client loads `agulhas.json` same-origin and re-fetches it on the Marion
Dufresne's 5-minute cadence, so a scheduled rebuild's new fixes appear without a
page reload. It feeds the whole file to the same `append` path the Marion
Dufresne uses — seeding the track on the first load, then adding only fixes newer
than the last one — so redraw cost stays flat as the track grows over the cruise
rather than redrawing the whole track each poll. The layer follows the same "no fix ⇒
no dead toggle" contract: the overlay and marker appear only once at least one
fix has loaded.

## Shared rendering

Both vessels feed one renderer (`makeShipLayer(vessel)` in `app.js`) driven by a
per-vessel **spec** in `VESSELS`. The spec carries the vessel name, its source
attribution, its track/marker colours, its sidebar panel element ids, and the one
thing that genuinely differs — a `rows(fix, prevFix)` function turning a fix into
the `[label, value]` pairs the tooltip and sidebar both render (so a vessel's two
readouts can never drift). The Marion Dufresne's rows derive motion and add met;
the Agulhas's use the reported speed/course plus status/area.

- **Rendering.** The track is a **plain coloured line at the drifter-track
  width** — one polyline per fix-to-fix segment, no per-fix dots and no cased
  halo (plan 034, decision 8). Each segment carries, on hover, the same tooltip
  as the current position filled with that fix's own data, so the along-track
  times stay readable without dots; the segments are interactive for the hover
  but swallow their click (a ship has no highlight axis). The two vessels are
  told apart by colour: the Marion Dufresne is dark blue (`#1e40af`), the
  Agulhas deep crimson (`#9b1c31`) — both distinct from the drifters' blue/teal
  and the gliders' amber/sky (the MD blue is deeper than the drifters'
  deployment-1 blue and the seaglider sky-blue, so it reads as a distinct line). The vessel marker is a coloured disc with a
  white ring and a boat glyph, set apart from the small blue drifter circles —
  and it **rides the app clock**: the track clips to the fixes at or before the
  clock and the disc sits at the vessel's interpolated position at that instant
  with the bracketing fix's tooltip, parking at the latest fix when the clock is
  at or past it (the same clock-following every observed track gets — see
  [trajectories.md](trajectories.md)). A top-of-sidebar panel per vessel shows
  the last-fix time, source, and that vessel's readout rows (always the latest
  fix, whatever the clock shows).
- **Stacking.** The track segments sit in a `shipTrack` pane *below every marker
  pane* (z-index 410) and the vessel marker in a `ship` pane on top (660). Every
  line/track pane sits below every marker pane, so no marker is ever occluded by a
  track — this is what keeps the early ship track (the cruise departs the drifters'
  staging port, so it runs through the pre-deploy cluster) from painting over, or
  intercepting a click meant for, any drifter or glider marker. Leaflet's
  `tooltipPane` is lifted above both the `drifters` and `ship` panes (its default
  z-index would tie/sit below them), so a segment's hover tooltip floats over
  every marker instead of being occluded. See [trajectories.md](trajectories.md).
- **Map fit is unchanged.** The opening view still fits the drifter cluster; the
  ships are not folded into the fit because they can be far offshore, which would
  zoom the map out past the drifters the map exists to show. Toggle a layer off,
  or pan/zoom, to follow a vessel.
