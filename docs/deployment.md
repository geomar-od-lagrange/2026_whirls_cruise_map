# Placing and interrogating virtual deployments

The app's primary capability is **placing virtual deployments and reading their
drift**: click (or import) a ship route, and the **Deploy** tool lays a row of
drifter drops at equal spacing along it and advects each drop through the CMEMS
surface-current field — forward or backward, for a chosen duration — as a green
track carrying a marker at its position at the app clock's instant. This is what
distinguishes the app from a pure observation viewer (the IPSL/aeris WHIRLS map):
where that shows where instruments *are*, this asks where a hypothetical release
*would go*, or where water arriving *now came from*. A run is described in
**release / direction / duration** terms; the forecast/hindcast vocabulary names
the *field*'s provenance (below), never a virtual run.

These tracks are computed
**on demand** for a planned deployment by a small live backend — an
integrator over the CMEMS field, seeded by the request. The live backend is
reachable in the local pixi flow (`pixi run serve` + `pixi run serve-api`) and,
under the hosted deployment ([plans/017](../plans/017-whirlsview-openshift.md)),
as a sibling `…/api/` service beside the map. Where no backend is reachable — the
bare static/Pages fallback ([deploy.md](deploy.md)) — the Deploy tool still places
and exports drops; only the drift computation is unavailable, and the tool
gracefully degrades to the last tab rather than leading the dock.

## One polyline tool, not a menu of shapes

A deployment is a ship steaming a path and dropping instruments along it. The tool
takes exactly that: a clicked polyline is the ship's route, and the drops fall at
equal arc-length along it. A free polyline is the **general** case — a jet crossing
is a straight two-click segment, a Z-through-a-box is four clicks — so one tool with
one spacing knob subsumes every fixed pattern without bespoke per-shape geometry or
a matching per-shape endpoint. The trade-off is that feature-tied framing (design an
array in across-/along-jet coordinates, or as a rotated rectangle) is not expressed
directly; here the generality is worth more than the convenience of a named
pattern.

## Why server-side: the field stays put, only the answer ships

The obvious alternative — ship the current field to the browser and advect
client-side — was explored and rejected on transport grounds, not physics. Two
findings decided it:

- **Payload.** A full-resolution hourly window is ~26 MB (int16) to ~52 MB
  (float32) over the cruise bbox; the compact `(mean, A, φ)` decomposition
  ([`_inertial.py`](../src/whirls_cruise_map/_inertial.py)) is ~0.1–2 MB but
  trades away sub-inertial mesoscale time-variation. Either way it is *far* more
  than the map moves today.
- **The deployed cache defeats it.** GitLab Pages here serves **uncompressed**,
  with **deployment-scoped ETags** (one ETag across every file, so a rebuild
  busts them all) and `max-age=600` against a ~10-min rebuild cadence — so a
  reload more than 10 min after the last one **re-downloads the whole field**.
  On the cruise's at-sea VSAT link ([data.md](data.md)) that is untenable.

The server-side API inverts the economics: the ~66 MB window stays in the
backend's memory, and each response is a **small FeatureCollection** — one short
polyline per drop, a few kB each. The forecast cost scales with *particles
requested*, not with the field, and never touches the client's link. The **drops are
not in the response**: the client computed their geometry, so the wire carries only
what needs the field — the advected tracks.

Two transport measures shrink the answer further, both sized against what a
viewer can actually resolve:

- **gzip on the wire.** The app compresses its own responses (Starlette's
  `GZipMiddleware` on the FastAPI app, `minimum_size=1024`) — in-app rather than
  gateway-side, so every deployment shape (the two-port dev flow, the plan-017
  gateway, any future proxy) ships compressed and the dev flow measures what
  production ships. The GeoJSON is highly repetitive, so a 1000-seed response
  drops from ~4.9 MB raw to ~1/3 gzipped; responses below `minimum_size` (the
  `limits` probe, error bodies) skip the codec overhead and stay identity-encoded.
- **coordinates cropped to 4 dp.** Every emitted coordinate (track vertices and
  marks alike) is rounded to 4 decimal places (~11 m) — sub-pixel at the map's
  `maxZoom: 12` (~30 m/CSS-px at the working latitude), at the drifters' ~5–15 m
  GPS fix scatter, and three orders below the 1/12° CMEMS field driving the
  advection, so nothing visible is lost. The bound is one constant
  (`_forecast._COORD_NDIGITS`), shared with the `_geojson` emitters, so every served
  coordinate obeys it. Combined with gzip, a 1000-seed response lands at ~1 MB.

## Two endpoints, deliberately split

`pixi run serve` (static map, `:8000`) and `pixi run serve-api` (forecast API,
`:8001`) are **separate processes**. The split is intentional: the static half is
byte-for-byte what GitLab Pages serves, so it can fall back to Pages with no
backend, and only the deploy tool's fetch needs the live service. Under the
plan-017 gateway the two become sibling backends under **one origin** — the map at
`…/map/` and its API at `…/api/`, and the gateway may mount each instance under a
subpath (`…/live-test/map/`, `…/live/map/`), so a same-origin fetch works in
production — the only real deployment.

The client resolves the API base rather than hardcoding it (`resolveApi` in
`app.js`), with no client-controlled override: in the two-port dev flow (page on
`:8000`) it auto-targets `:8001`, and otherwise it derives the API **relative to
the map's own served base** — it strips the trailing `map/…` from the page path
and re-roots the API alongside it (`…/live-test/map/` → `…/live-test/api/forecast`,
origin-root `/map/` → `/api/forecast`), so a gateway subpath resolves correctly
without an origin-root assumption. Dropping the former
`?api=`/`window.WHIRLS_FORECAST_API` override means a crafted link can't retarget
the seed `POST` at a hostile host.

## The engine: the build's RK4, seeded by each drop

The API does **not** re-implement advection. `_forecast._integrate` — RK4 with a
5-min sub-step, a polyline vertex every 15 min, the m/s→deg conversion, and the
NaN-land / window-edge stop — already advects an *arbitrary* `(lon, lat)` from an
*arbitrary* start; the build's forecast just happens to feed it instrument heads.
The API feeds it each drop instead. Two small refactors keep this shared cleanly:

- `_anchor_t0(sampler, t0=None)` — the clock's t = 0 and its `valid_time`, either
  nearest-now (the build) or an explicit epoch (each drop's water-entry time).
- `_advection_feature(sampler, props, lon, lat, t0, valid, direction, *,
  horizon_h, mark_hours)` — the single-line Feature builder, with `_integrate`'s
  cadence knobs (`horizon_h`, `mark_hours`) as parameters. The build keeps its
  defaults (±6 h, 1/3/6 h marks); the API passes each drop the horizon that lands
  it on the run's common end and the run-relative mark hours (below).

So the interactive line carries the *same* physics and the *same* caveats as the
build forecast — surface current only, model near-inertial amplitude — because it is
literally the same integrator over the same field.

**Batched, not looped.** The build advects a handful of instrument heads, so it runs
the scalar `_integrate` per head. A deployment is up to a couple thousand drops, where
that pure-Python per-seed loop dominates walltime (~23 ms/seed → ~46 s at the cap). So
the API advects the whole batch through a **vectorized twin** of the same RK4
(`_forecast._batch_advect`): all seeds advance together in step-index lockstep, each
stage sampling the field for every still-active seed in one numpy gather. It is
**bit-identical** to the scalar path — same corner-sum / time-lerp / RK4 arithmetic
order, same land-`NaN`→stop and window-edge truncation — but ~40× faster (2000 drops in
~1.2 s, from ~46 s), pinned to the scalar reference by a test. `direction` (`+1`
forward, `-1` backward) mirrors the scalar integrator's `dt` negation exactly, so a
backward run walks the same vectorized path in reverse. Pure numpy: no new
dependency.

`_api_parcels.py` is a second, independent engine (see *Validation* below); `_api.py`
(RK4) is the one the client uses.

## The field: an incremental store, streamed per request

The API does **not** fetch CMEMS, and does **not** hold the field in memory between
requests. A slow build run maintains an **incremental per-day store**
(`_field_store.py`: one `uv_YYYY-MM-DD.nc` per UTC day plus a `field_manifest.json`),
covering the whole cruise span rather than a rolling window — see
[plans/034](../plans/done/034-deployment-focused-app.md), workstream A. The API resolves
the store directory the same way the store module does (`WHIRLS_FIELD_CACHE` env,
else a repo-local `cache/field/`), `stat`s the manifest once per request, and rebuilds
a small in-process **field index** — the maximal contiguous on-disk day run
containing "now", its two edges probed for their actual first/last time steps — only
when the manifest's mtime changes. Every request then opens a fresh
`_field_store.StoreField` scoped to just the span its own run needs (the run's anchor
through its common end, clipped to what the store actually has loaded): a bounded LRU
of day arrays, not the whole field, so API memory stays flat regardless of how long
the run or how wide the store grows (the pod's 3 Gi limit).

Because it holds no `cmems-creds` and makes no CMEMS request, the API pod needs **no
credentials and no internet egress** — the field arrives entirely over the shared
store volume, which the API only ever reads.

The consequence for **arbitrary start times**: nothing is a cache key. Any drop start
(or clicked position) inside the store's current reach is servable, at no extra
download, with cost scaling with **the run's own span**, not with request count. A
drop whose start falls outside the store's loaded span, or that has no track left
before its run's common end, is **skipped and counted** (not silently extrapolated),
and the response carries the run's actual window bounds so the client can say so.

This streaming shape *is* the production field-cache
([plans/017](../plans/017-whirlsview-openshift.md), option B): the same locally as
on the cluster, differing only in where the shared store volume lives (a repo dir
under `pixi run serve` vs a PVC mounted into both the build CronJobs and the API pod,
read-only for the latter). What remains deployment-side is the `oc_gateway`
coordination — mount the shared PVC's `cache` subPath into the `*-api` Deployment,
drop its `cmems-creds` `secretRef` and CMEMS egress `NetworkPolicy`, and keep the
gateway nginx from ever serving that subPath — tracked in that repo so the two sides
land together.

## The batch endpoint: seeds + direction in, one track per drop out

`POST /api/forecast` — the whole API. The body is a whole deployment's worth of
seeds plus the run-level direction and horizon:

```json
{ "seeds": [{"lon": …, "lat": …, "start": "ISO-8601"}, …],
  "direction": "forward", "horizon_h": 48.0 }
```

Each seed is a drop the client already placed: a position and its **absolute**
water-entry time (the ship-speed stagger is baked into `start`, so the API needs no
ship speed and no notion of a pattern). `direction` (`"forward"` default, or
`"backward"`) picks the run's anchor: the **earliest** seed start for a forward run,
the **latest** for a backward one. Every seed integrates to the common wall-clock end
`anchor + direction · horizon_h`; a drop entering later (forward) or earlier
(backward) than the anchor carries a correspondingly shorter track, since they all
stop at the same end.

The response is a `FeatureCollection` of one `role: "track"` `LineString` per seed
still carrying a track (`index`, `start` — that seed's own ISO start — `cadence_s`,
`direction`; vertex *i* sits at `start + direction · i · cadence_s`, the substrate a
future at-time-marker client reads off directly rather than a fixed per-run mark
schedule), plus run-level `properties`: `run_start` (the anchor), `direction`,
`horizon_h`, `cadence_s`, `n_seeds`, `tracks`, `skipped`, `window` (this run's actual
loaded span), and `analysis_edge` (now-at-response-time; a track segment beyond it is
forecast-provenance). A seed whose `start` is outside the run's loaded span, or that
has no track left before the common end, is skipped and counted rather than aborting
the batch.

This keeps a clean division of labour: the client owns *where the drops go and when
each enters the water* (pure geometry it can preview without a fetch), and the API is
a **pure batch advector**, directed and seeded by the request.

The request is bounded so one unauthenticated call can't exhaust the pod: at most
**2000 seeds** per POST, `horizon_h` up to 100 days, and — because either knob alone
is a generous ceiling rather than a tight one — a combined **seeds × hours budget**
(`_MAX_SEED_HOURS`, 1,000,000) sized so the worst-case request still finishes with
margin inside the gateway's ~60 s timeout (~12 s worst case). Both the seed cap and
the budget have a **single source of truth** in the API and are advertised by
`GET /api/forecast/limits` (`{"max_seeds", "max_seed_hours", "window", "analysis_edge"}`);
the deploy-tool client fetches the seed cap and rejects an over-cap deployment up
front — "too many drops … increase spacing or shorten the path" — rather than firing
a doomed POST or hardcoding its own copy of the number. A request that slips past
that check is still rejected server-side with a `422`, whose validation message the
client renders verbatim instead of a bare error.

Because a whole deployment advects in ~1–2 s even at the seed cap (vectorized RK4),
the request is a **single POST** — no result cache, single-flight, or client retry.

## Start time, locked to the displayed field; staggered entry

The run start is the **displayed CMEMS field's time**, which the client passes as
drop #1's `start`, so a placed deployment begins at the same instant as the field
shown on the map (and the first segment aligns with the displayed currents — see
below). At load this is the now frame (`currents_meta.json`'s `valid_time`); moving
the **time slider** ([currents.md](currents.md)) re-locks it to the selected
forecast step, so a deployment placed while the slider shows +24 h starts its drift
there. The API validates every seed's start against its loaded window and skips any
it can't cover, so a start out past the window is handled gracefully. Under
along-track timing each later drop enters the water at
`run_start + cum_km / (ship_speed · 1.852)` — the ship-speed knob turned into a
staggered water-entry time, baked into that seed's `start` client-side; under
instantaneous timing every seed's `start` is the release time itself.

## Why the track peels away from the animated currents

A placed forecast and the flow overlay do **not** trace the same curve, and
shouldn't. The "Current flow" overlay is a **frozen 6-hourly snapshot** — the flow frame
nearest the app clock's time (`flowvis_<t>Z.webp`), so it is the **streamlines** of one
instant. The forecast advects through the
**time-dependent hourly window** with the clock running, so it is a **pathline** — and
because the field rings at the inertial
period, the pathline **curls** away from the instantaneous streamline (the same
reason the drifters show inertial loops; [plans/012](../plans/012-near-inertial-forecast.md)).
Locking the run start to the snapshot time aligns them at t = 0; the downstream
divergence is the genuine inertial curl, not a mismatch. (The trails are additionally
√-magnitude compressed for animation, so their *speed* is not the true speed either,
and the separate near-inertial animation excludes the mean current entirely.)

## Client: the Deploy tool

The **Deploy** tab is the dock's primary tab — it leads the strip and opens by default,
chosen at build so the dock never flashes another tab first. A `/limits` probe runs off
the critical path and only downgrades: when the API is unreachable (the static/Pages
fallback), the dock re-selects Instruments; Deploy still places + exports drops without
computing drift. A run is described in **release / direction / duration** terms; the
forecast/hindcast vocabulary is reserved for the *field*'s provenance (below),
never for a virtual run.

**Layout.** The tab reads top to bottom: the **run settings** (release, Direction,
Timing, the duration slider, the drop-spacing / speed line), then the per-deployment
**manager**, then the **Deploy** arm toggle beside a **Clear** button, then the
collapsible **CSV import / export** menu at the very bottom, then the status line.

**Knobs.** **Release** is read-only and follows the app clock — one clock,
so "release at t" means jumping the scrubber to t (see [controls.md](controls.md)),
not typing a time here. **Direction** is a sliding switch (forward/backward, POSTed as
the run-level `direction`). **Timing** is a sliding switch selecting how the drops
enter the water: **along track** (the
default) staggers each drop's water entry by the ship's transit to it — a real vessel
steaming the route — while **instantaneous** puts every drop in the water at the
release time, the idealised simultaneous release (e.g. to read the pure flow-field
deformation of a line, with no transit aliasing mixed in). **Duration** is a **1d / 2d
/ 5d / ∞** segmented slider (default **5d**), writing the run length in hours; the `∞`
stop advects to the end of the loaded field (the server truncates at the field edge, so
any horizon ≥ the field span reaches it). Spacing and speed collapse to one short line —
**"Every \[ \] km at \[ \] kn"** — two inline number inputs with the literal
text between them. Under instantaneous timing the ship speed shapes nothing, so its
input greys out and shows an `∞` glyph (the km spacing stays editable, since
instantaneous drops still have a spacing along the path); the transit estimate also
drops from the preview and status lines. The two number inputs are plain text fields
guarded to admit only digits and a single dot — the decimal separator is a dot
regardless of the browser's locale. This avoids the `type=number` trap where a de-locale
browser blanks a comma-typed value and silently drops the edit; here a comma (or a
pasted `0,5`) is refused as an invalid keystroke rather than blanked or mangled. The
trade is the loss of the native spinner arrows, which the knobs don't need.

**Placing (the Deploy toggle).** Arming the **Deploy** toggle gives the map a
crosshair. While armed:

- **click** adds a vertex to the path; a live preview (rubber-band to the cursor, no
  fetch) redraws the polyline and the equally-spaced drop discs it implies, so the
  spacing and ship-speed knobs read instantly.
- **double-click** finishes. The client resamples the path (`resamplePolyline`, the
  cos-lat tangent-plane / equal-arc-length math) into drops, computes each drop's
  `start` from the release time plus its ship-speed offset (`seedTime`), draws the
  drops into **this deployment's own Leaflet group** (so the manager can toggle
  or delete it wholesale), and POSTs the seeds. The elements sit in stacked panes:
  the drift geometry stays **below every real marker** (`deployTracks` for the drift
  lines at z-index 430, `deployDrops` for the drop discs at 440 — see the pane stack
  in [trajectories.md](trajectories.md)), while `atTime` (the position-at-clock
  markers) rides the top marker pane (670) so a moving head never hides under a line.
- **right-click** (or **Escape**) aborts an in-progress path: the clicked vertices and
  preview are discarded without committing, and the tool stays armed for a fresh start.
  While armed, a right-click's browser context menu is suppressed.

**Pre-validation.** Before any POST the placement is checked against the advertised
`/limits`: the seed cap, the seeds × duration budget (`max_seed_hours`), and
release + duration vs the loaded `window`. A violation is reported on the status line
up front (e.g. *"drops × duration too large … — fewer drops or a shorter duration"*)
and the doomed POST is skipped — the drops are still placed and exportable. When the
limits are unknown (the
probe failed), the check is skipped and the server's own bounded request model rejects
the request via the error path.

**The clock clips the trail.** Each drift is **one green line** that grows up to the
app clock: at clock *t* it draws only the part **already traversed** by that instant
and nothing ahead of it, so a scrub animates the deployment rather than repainting dots
on a static line. Every line also carries **one at-time marker** at the position it
occupies at the clock's instant, interpolated from the feature's
`{start, direction, cadence_s}` timing and walking the line as the scrubber moves
(hidden when the clock is outside the track's span) — it is the drift's only head.
Normalised to ascending absolute time, a backward run needs no special case: it
converges toward the release line as the clock plays forward. The lines carry no
analysed-vs-forecast dash split and draw nothing ahead of the clock, and no vessel
route is drawn between the drops — so nothing needs a legend to decode. The drift lines
are **cropped at the scrubber** like the observed tracks and drifter forecasts (a forward
run grows release→clock, a backward run clock→release), but the deploy tool owns them:
the scrubber's "Show tracks" master governs only observed instrument tracks + the drifter
forecasts, not these. A selected trajectory is raised to the front (the highlighted-track
z-order rule). The tracks are ad-hoc and ephemeral — never persisted,
never a build artifact — and drawn in a distinct **green** so they read apart from the
orange observed tracks.

**Read by click** (three independent axes, each swallowing its own click):

- **click an at-time marker** → that deployment's whole marker set — the array's shape
  at this instant.
- **click a drop disc** → every drop disc of that deployment — the set of water-entry
  points.
- **click a track line** (on bare track) → that one drifter's trajectory.

**Status line.** Reports the geometry (`N drops · X km · ~Y h transit`, or
`… · instant release` under instantaneous timing) then the result
(`tracks/n_seeds drift`, a `· K skipped` tail when the API skipped out-of-window seeds,
a pre-validation message, or the API's message on an error / no field).

## The deployment manager

The Deploy tab carries a **manager** (above the Deploy toggle): one row per placed
deployment — its id, release time, direction arrow, duration, drop count, and an
`instant` tag when it was released instantaneously — with a per-row **visibility
toggle** (add/remove that deployment's group from the map), **CSV export** of that
deployment's waypoints, and **delete**. The **Clear** button beside the Deploy toggle
wipes them all at once, and the all-deployments **Download all CSV** lives in the
Deploy tab's **CSV import / export** menu. Each placement owns a namespaced id and a
Leaflet featureGroup, so hiding, exporting, or deleting one deployment never touches
another's map elements, highlights, or waypoint rows.

## Waypoint CSV export

The per-row **CSV** button exports one deployment; **Download all CSV** exports every
placed one — identical columns either way. The drops *are* the deployment waypoints —
where each drifter enters the water and when (the staggered ship-transit ETA, or the
shared release time for an instantaneous run) — so the export is a straight dump of
geometry the client already owns: no server round-trip and no build artifact. One row per drop, ordered by deployment then drop:

```
deployment,drop,latitude,longitude,water_entry_utc,cum_km
```

`deployment` is the placement's id, `drop` its 1-based index, `latitude`/`longitude`
the seed's 5-decimal position, `water_entry_utc` the seed's absolute ISO `start`, and
`cum_km` the arc length from the path start. The clicked route corners are *not*
exported — the drops lie along that route, so their ordered positions give both the
where and the when without the raw vertices. Drops are captured for every placement,
and delete / Clear all wipe the captured waypoints along with the layers. The download is client-side (a Blob + object URL), so
it works under `pixi run serve` with no backend.

Leaflet fires two `click`s before a `dblclick`, so the finishing double-click's
near-duplicate tail vertex is dropped, and `doubleClickZoom` is disabled while armed
so the finish doesn't also zoom.

## Importing a deployment: paste or upload spatial waypoints

Rather than clicking a route, a user can hand the tool the **vessel's route as a list
of waypoints** they already have — a planned track from the bridge, a spreadsheet
column, a pasted block. The Deploy tab carries a small **paste box** plus **Upload
file** and **Place using these waypoints** buttons.

**Upload *and* mask, one parser — not a choice between them.** "CSV upload vs input
mask" is a false dichotomy: a file and a paste differ only in where the text comes
from. So the textarea *is* the source of truth (the mask), and **Load file…** merely
reads a `.csv` into it; one `parseWaypoints` then serves both. This makes the
pasted-block workflow first-class (paste a plan straight from a doc), lets a loaded
file be eyeballed and edited before placing, and needs no upload-vs-paste branch.

**No parser dependency.** The format is a few lines of `lon,lat` — a ~30-line
tolerant hand-parser beats pulling a CSV library over the CDN the map already leans
on, and stays friendlier to the offline-VSAT / future-CSP direction. PapaParse would
be the pick only if we needed quoting, streaming, or type inference; we don't.

**The rows are the vessel route, not the drops.** An imported waypoint list is the
ship's track — exactly what a clicked path is — so it is treated identically: the tool
**resamples the route at the Drop spacing knob** into equally-spaced drops, and the
**number of drifters follows from the route length and spacing**, not from the number
of waypoints. **Ship speed** staggers each drop's water-entry time along that route,
and the release time is the **time scrubber**'s displayed field instant (pulled live
when **Place using these waypoints** is pressed). So the CSV path is the click path fed
as text: an import literally calls the same `placeDeployment` (resample →
`commitDeployment`: seeds → pre-validation → POST → drift lines + at-time markers + drop
discs + waypoint registry), so an imported deployment computes drift, exports,
highlights, and deletes exactly like a clicked one. **Direction**, **Timing**, and
**Duration** apply the same way.

**Accepted format.** Decimal degrees, negative = S/W. Tolerant: blank lines and `#`
comments are dropped; the delimiter is comma / semicolon / tab / whitespace; a header
row (any non-numeric token) maps columns by name (`lat…`, `lon…`/`lng`), so the
export's `…,latitude,longitude,…` round-trips directly; headerless rows are read as
`lon,lat` (GeoJSON x,y, the seed object's key order). Rows that aren't two finite
in-range numbers are skipped and counted in the status line. DMS (`12° 15.6′ E`) is
not parsed — decimal only (a noted follow-up). `tests/fixtures/deploy_waypoints_wp1-12.csv`
is a 12-waypoint vessel route from the cruise plan for exercising this — resampled at
the default spacing it yields tens of drops along the ~185 km track.

## Validation: cross-checked against OceanParcels v4

The hand-rolled RK4 is validated against **parcels v4** (the OceanParcels rewrite,
built from `main`), advecting the *same* field so the comparison isolates the
integrator. `_api_parcels.py` is a parallel API (`serve-parcels`, `:8002`) that reads
its own `+/-12 h` window off the incremental field store (`_field_store.load_window`
— the same store `_api.py`'s batch endpoint streams from, but its own independent
fetch) and advects a **single point +12 h** — the cadence `_HORIZON_H` (12 h) and
`_MARK_HOURS` (3/6/9/12 h), defined in `_api_parcels.py` itself (the batch endpoint
has no fixed cadence of its own to borrow since v2 takes both from the request).
`tmp_parcels_compare/` is the harness. Findings:

- **Agreement: metres over 12 h** (mean ~8 m, max ~16 m on a ~20 km path). The
  residual is one constant — parcels' spherical mesh uses the nautical-mile
  `deg2m = 1852 × 60 = 111120`, ours uses `R·π/180 = 111194.9` (R = 6.371e6), a
  **0.067 %** offset that quantitatively predicts the observed separation. Otherwise
  the two schemes match by construction (bilinear space, linear time, RK4). This
  makes parcels a citable correctness reference for the time-stepping.
- **Performance: RK4 ~5.5 ms vs parcels ~525 ms per single-particle forecast**
  (~100×), parcels' cost being its per-step field interpolation. Parcels *vectorizes*
  (per-particle cost falls sharply with batch size), so the tiers are complementary.

**So RK4 is the interactive engine and parcels is the oracle / batch tool.** For a
per-drop service the 100× speed decides it; parcels is reserved for large offline
ensembles (where vectorization amortizes) and as the independent check on the RK4.
Parcels caveats, if it is used for batches: it is alpha (unstable API; `__version__`
misreports); its native land convention is zero-velocity, not NaN (we keep NaN so it
raises→truncates like the RK4); and one out-of-bounds particle aborts a whole
vectorized batch unless land is filled and boundary kernels added. Installing it pins
the project's Python to 3.12 and pulls its dependency tree (uxarray/xgcm/…) from
conda-forge.
