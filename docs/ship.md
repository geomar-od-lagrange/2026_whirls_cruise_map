# ship track

A live position and track for **R/V Marion Dufresne**, drawn over the drifter
map. It mirrors the "platform positions real-time" layer on the IPSL WHIRLS
operational map, and uses the same upstream source.

## Data source

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

### Time resolution

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

### Field units

`seatemp`/`airtemp` are °C, `pressure` is hPa, and `truewinddir` is degrees —
unambiguous from the values. The `truewindspeed` **unit is not specified by the
API** and is not asserted here: the popup and sidebar show the bare number. Do
not relabel it as knots or m/s without confirming with the source.

### Course & speed are derived, not reported

The API exposes **no ship speed-over-ground or course-over-ground** — a record is
only `lat`/`lon`/`date` plus the met fields above (`truewinddir`/`truewindspeed`
are *wind*, not vessel motion). So the popup/sidebar "Speed (derived)" and
"Heading (derived)" are computed client-side from a **track segment**: the
great-circle distance between two fixes over their time gap, and the initial
great-circle bearing between them (degrees true, with a 16-point compass label).
The same per-segment derivation labels every fix on the track, not just the
latest — each dot (below) shows its own.

Speed is shown in **both knots and m/s** (`12.3 kn / 6.33 m/s`), so it reads on
the same scale as the drifters' m/s velocities. Below ~0.5 kn — about 150 m over
a 10-min step, comparable to GPS scatter — the bearing is unreliable, so the
heading is suppressed (the near-zero speed is still shown); this is what a vessel
on station or moored looks like. The heading row is **always present**, showing
`NA` when suppressed or when there is no prior fix to derive a bearing from.
Because it is a single-segment difference the value is instantaneous and a little
jumpy; smoothing over several fixes would steady it at the cost of lag, and is
deferred.

## Why client-side and live

Every other layer on this map is a build artifact written into `site/data/` by
the Python build. The ship is the deliberate exception — it is fetched **live in
`app.js`**, not baked at build time.

The reason is freshness under the intended hosting. The site is a static bundle
destined for a scheduled-rebuild GitLab Pages deploy, which has no server at view
time. A baked `ship.geojson` would therefore freeze the vessel between rebuilds,
which defeats the point of a "where is the ship now" layer. Fetching live keeps
the marker current to the API's ~10-minute cadence regardless of build schedule,
and the open CORS policy exists precisely to allow this (the IPSL map does the
same).

The trade-off is that this one layer carries a runtime dependency on a
third-party host. It is contained by the same graceful-fetch pattern the rest of
the client uses: a failed request resolves to an empty list, so an outage simply
stops the marker advancing — it never throws and never blanks the map.

## Behaviour

- **Initial load** fetches the whole cruise window, `cruiseStart … now`.
  `cruiseStart` (`app.js`, the `SHIP` config) is `2026-06-24T00:00:00Z`, matching
  the IPSL WHIRLS window start; it is a one-line constant to adjust.
- **Live refresh** polls every 5 minutes, requesting only the window *since the
  last known fix* and appending the new tail (deduplicated by timestamp), so
  polling cost stays flat as the track grows rather than re-pulling the whole
  history each time.
- **Rendering.** The track is a cased polyline — a white halo under a dark core —
  so it stays legible over any basemap, with a small dot at **every 10-minute
  fix** painted on top of it. Each dot opens the same popup as the current
  position, filled with that fix's own met data and derived motion. The current
  position is a dark disc with a white ring and a boat glyph, set apart from the
  small blue drifter circles. The marker popup and a top-of-sidebar panel show the
  last-fix time, the underway readout (sea/air temperature, pressure, wind), and
  the derived course and speed (below).
- **Stacking.** The track and its dots sit in a `shipTrack` pane *below* the
  drifter markers, while the current-position marker sits in a `ship` pane on
  top. The track runs below the drifters because the cruise departs the drifters'
  staging port, so the early track passes through the pre-deploy cluster; were the
  dots above the markers they would intercept clicks meant for the drifters. The
  dots are plain SVG circle markers (not a canvas, which spans the whole viewport
  and would block clicks map-wide). See [trajectories.md](trajectories.md).
- **Map fit is unchanged.** The opening view still fits the drifter cluster; the
  ship is not folded into the fit because it can be far offshore, which would zoom
  the map out past the drifters the map exists to show. Toggle the layer off, or
  pan/zoom, to follow the vessel.

## Limitation: Marion Dufresne only

This API covers the **French** fleet only (Antea, L'Atalante, Côtes de la Manche,
L'Europe, Marion Dufresne, Pourquoi Pas?, Thalia, Thalassa, Tethys II). The South
African **R/V S.A. Agulhas II** is not in it, so this layer tracks the Marion
Dufresne alone. Adding the Agulhas II would require a separate position source.
