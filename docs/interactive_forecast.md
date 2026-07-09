# Interactive click-to-deploy drift forecast (dev PoC)

Click a multi-segment path on the map and double-click to finish: the **Deploy**
tool lays a row of drifter drops at equal spacing along the path and draws each
drop's **current-advection drift to 48 h** — a solid green line with a colour-ramped
dot at each mark. Unlike the per-instrument forecast ([forecast.md](forecast.md)),
which is a **build artifact** computed for the drifter/glider heads, these are
computed **on demand** for a planned deployment by a small live backend.

**This is a development PoC, not part of the deployed product.** The public map
ships as static files on GitLab Pages ([deploy.md](deploy.md)), which has no
backend, so the deploy has no deploy tool. It runs locally (`pixi run serve` +
`pixi run serve-api`) and is the prototype for the future hosted analysis service
([plans/017](../plans/017-whirlsview-openshift.md)'s `/analysis` path).

## One polyline tool, not a menu of shapes

A deployment is a ship steaming a path and dropping instruments along it. The tool
takes exactly that: a clicked polyline is the ship's route, and the drops fall at
equal arc-length along it. A free polyline is the **general** case — a jet crossing
is a straight two-click segment, a Z-through-a-box is four clicks — so one tool with
one spacing knob subsumes every fixed pattern without bespoke per-shape geometry or
a matching per-shape endpoint. The trade-off is that feature-tied framing (design an
array in across-/along-jet coordinates, or as a rotated rectangle) is not expressed
directly; for the PoC the generality is worth more than the convenience of a named
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
polyline per drop, a few kB for a whole deployment, less than the existing
`forecast.geojson`. The forecast cost scales with *particles requested*, not with
the field, and never touches the client's link. The **drops and the ship track are
not in the response**: the client computed the geometry, so the wire carries only
what needs the field — the advected tracks.

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

`_api_parcels.py` is a second, independent engine (see *Validation* below); `_api.py`
(RK4) is the one the client uses.

## The field: one window, cron-written, reloaded on change

The API does **not** fetch CMEMS. The slow build cron already fetches one hourly
window to build `forecast.geojson`/`hindcast.geojson`; it now **persists that same
window** to an unserved `site/map/data/_cache/forecast_window.nc` (`*.tmp` +
`os.replace`, atomic). The API loads that file into a `_forecast._Field` and
**rebuilds it whenever the file's mtime changes** — one `stat` per request, a
rebuild only on a fresh cron write. Every forecast is then pure CPU on the
in-memory array. Because the sampler tracks the file rather than being built once,
a long-lived single-replica pod **picks up each new window within one request, no
restart** — the failure the process-lifetime cache had, where a pod's forward
window edge was consumed by uptime until "now"-seeded runs truncated and then died.

The cron sizes the window forward to `FORECAST_HORIZON_H + SLOW_CADENCE_H`
(48 + 12 = 60 h), so a window up to one slow-cron cadence old still spans a full
48 h run from "now" rather than truncating every track at the window edge; the back
span (12 h) covers a displayed field that lags wall-clock by the build cadence. The
horizon is one shared constant (`_currents.FORECAST_HORIZON_H`, read by both the
cron's window sizing and the API's request default), so a horizon bump can't
silently outrun the persisted window. The window is fetched **once** and reused for
forecast/hindcast, the persisted API cache, and — sliced back to its narrow ~24 h
span, which the near-inertial fit needs — the inertial decomposition.

Because it holds no `cmems-creds` and makes no CMEMS request, the API pod needs
**no credentials and no internet egress** — the field arrives entirely over the
shared volume. The path is overridable via `WHIRLS_FORECAST_WINDOW` (the shared PVC
path in the plan-017 deployment).

The consequence for **arbitrary start times**: t0 is *not* a cache key. One window
serves every drop start (and every clicked position) that falls inside it, at no
extra download. Cost scales with the window's **span** and **refresh cadence**,
never with request count. A drop whose start falls outside the loaded window is
**skipped and counted** (not silently extrapolated), and the response carries the
window bounds so the client can say so. Widening the window to cover a longer
horizon or a longer deployment run is a **server-memory** cost (~4 MB/h in float64),
*not* a wire cost — which is the whole point of keeping the field server-side.

This load-latest-reload-on-mtime shape *is* the production field-cache
([plans/017](../plans/017-whirlsview-openshift.md), option B): the same locally as
on the cluster, differing only in where the shared volume lives (a repo dir under
`pixi run serve` vs a PVC mounted into both the cron and the API pod). What remains
deployment-side is the `oc_gateway` coordination — mount the shared PVC into the
`*-api` Deployment, drop its `cmems-creds` `secretRef` and CMEMS egress
`NetworkPolicy`, and keep the gateway nginx from serving `data/_cache/` — tracked in
that repo so the two sides land together.

## The batch endpoint: seeds in, one track per drop out

`POST /api/forecast` — the whole API. The body is a whole deployment's worth of
seeds plus two run-level cadence knobs:

```json
{ "seeds": [{"lon": …, "lat": …, "start": "ISO-8601"}, …],
  "horizon_h": 48.0, "mark_step_h": 3.0 }
```

Each seed is a drop the client already placed: a position and its **absolute**
water-entry time (the ship-speed stagger is baked into `start`, so the API needs no
ship speed and no notion of a pattern). The response is a `FeatureCollection` of one
`forecast` `LineString` per **in-window** seed (`role`, `index`, `valid_time`,
`marks`), plus run-level `properties`: `run_start`, `horizon_h`, `mark_step_h`,
`n_seeds`, `forecasts`, `skipped`, and `window` (the field span). The **run start is
the earliest seed time** — drop #1's entry.

This keeps a clean division of labour: the client owns *where the drops go and when
each enters the water* (pure geometry it can preview without a fetch), and the API is
a **pure batch advector** of the seeds it is handed. A seed whose `start` is out of
the field window, or at/after the common run end (no track left to draw), is skipped
and counted rather than aborting the batch — a *plan* still stands even when the
field doesn't cover its full duration.

The request is bounded so one unauthenticated call can't exhaust the pod: at most
**2000 seeds** per POST (the RK4 advection is GIL-bound and serialises on the single
sync worker), plus `horizon_h`/`mark_step_h` ranges that cap the per-seed dot
schedule. The seed cap has a **single source of truth** in the API and is advertised
by `GET /api/forecast/limits` (`{"max_seeds": …}`); the deploy-tool client fetches it
and rejects an over-cap deployment up front — "too many drops … increase spacing or
shorten the path" — rather than firing a doomed POST or hardcoding its own copy of the
number. A request that slips past that check (limits probe unavailable) is still
rejected server-side with a `422`, whose validation message the client renders
verbatim instead of a bare error.

## Synced-t0 dots: the whole array at one instant

The dots are the scientific point of a batch, not decoration. Dotting each drop at
`+3/6/… h` from its *own* entry would put no two dots at the same wall-clock time,
so the array's shape at a chosen instant could not be read off the map. Instead every
seed is integrated to a **common wall-clock end** (`run_start + horizon_h`, 48 h) and
dotted at **absolute** run-relative marks (`run_start + k · mark_step_h`, default
3 h), computed by `_seed_marks`. So mark *k* is the same instant for every
drop, and **one dot colour is the whole array at one t0** — the reference time a
deformation / flow-map estimate is anchored to (Haller). A drop that enters after
mark *k* simply carries no dot at *k*, and later drops carry shorter tracks (they all
stop at the same end). The integrator tags each mark with elapsed-from-entry hours;
`_batch_forecast` shifts each back to its absolute run-relative hour by adding the
drop's entry offset — a relabel by *value*, not by list position, so a mark the
integrator dropped (too near entry, or past a coast truncation) leaves no dot rather
than shifting the colour of every later one. That value is what the client colours each
dot by.

This is the first read on the **open `t0`-inversion problem** (see the roadmap): the
clean array exists in the *deploy* frame, but by the time the last drop is in the
water the first has already drifted, so the synced dots show the array *deforming*,
not the ideal configuration at t0. Landing an ideal t0 configuration by
backward-advecting each node to its drop time is the inverse step, not yet built.

## Start time, locked to the displayed field; staggered entry

The run start is the **displayed CMEMS field's time**, which the client passes as
drop #1's `start`, so a placed deployment begins at the same instant as the field
shown on the map (and the first segment aligns with the displayed currents — see
below). At load this is the now frame (`currents_meta.json`'s `valid_time`); moving
the **time slider** ([currents.md](currents.md)) re-locks it to the selected
forecast step, so a deployment placed while the slider shows +24 h starts its drift
there. The API validates every seed's start against its loaded window and skips any
it can't cover, so a start out past the window is handled gracefully. Each later drop enters the
water at `run_start + cum_km / (ship_speed · 1.852)` — the ship-speed knob turned
into a staggered water-entry time, baked into that seed's `start` client-side. The
API validates each start against the loaded window and skips any it can't cover.

## Why the track peels away from the animated currents

A placed forecast and the animated flow overlay do **not** trace the same curve, and
shouldn't. The "Current flow" trails are built from a **frozen 6-hourly snapshot** —
the displayed slider frame (`currents_±NNh.json`; see [forecast.md](forecast.md)), so
they are **streamlines** of one instant. The forecast advects through the
**time-dependent hourly window** with the clock running, so it is a **pathline** — and
because the field rings at the inertial
period, the pathline **curls** away from the instantaneous streamline (the same
reason the drifters show inertial loops; [plans/012](../plans/012-near-inertial-forecast.md)).
Locking the run start to the snapshot time aligns them at t = 0; the downstream
divergence is the genuine inertial curl, not a mismatch. (The trails are additionally
√-magnitude compressed for animation, so their *speed* is not the true speed either,
and the separate near-inertial animation excludes the mean current entirely.)

## Client: the Deploy tool

A top-right **Deploy** control arms click-to-place mode (the map takes a crosshair
cursor). While armed:

- **click** adds a vertex to the path; a live preview (rubber-band to the cursor, no
  fetch) redraws the polyline and the equally-spaced drop discs it implies, so the
  spacing and ship-speed knobs read instantly.
- **double-click** finishes. The client resamples the path (`resamplePolyline`, the
  cos-lat tangent-plane / equal-arc-length math) into drops, computes each drop's
  `start` from the run start plus its ship-speed offset (`seedTime`), draws the ship
  track + drops, and POSTs the seeds; the returned forecast lines and synced dots are
  drawn over them in three stacked panes above the instruments — `deployTracks`
  (ship route + drift lines) lowest, `deployDrops` (drop discs) above, and
  `deployDots` (the `+Δt` mark dots) on top — so a drop disc never hides a delayed
  dot and the dots never slip under a line, regardless of draw order.
- **right-click** (or **Escape**) aborts an in-progress path: the clicked vertices and
  preview are discarded without committing, and the tool stays armed for a fresh start.
  While armed, a right-click's browser context menu is suppressed.

Knobs: **Drop spacing (km)**, **Ship speed (kn)**, **Forecast (h)** (default 48), and
a **Forecast drift** checkbox (draw the drops + ship track only, no fetch, when off).
The status line reports the geometry (`N drops · X km · ~Y h transit`) and then the
result (`forecasts/n_seeds drift`, an over-cap notice when a placement exceeds the
seed cap, or the API's message on an error / no field). The
forecasts are ad-hoc and ephemeral — never persisted, never a build artifact — and
drawn in a distinct **green** so they don't read as the instrument forecast (violet).
**Clear** wipes them.

## Waypoint CSV export

**Download CSV** (beside **Clear**) exports the placed drops as a flat waypoint
table for the ship. The drops *are* the deployment waypoints — where each drifter
enters the water and its staggered ship-transit ETA — so the export is a straight
dump of geometry the client already owns: no server round-trip and no build
artifact. One row per drop across **every** currently-placed deployment (a Deploy
session can place several), ordered by deployment then drop:

```
deployment,drop,latitude,longitude,water_entry_utc,cum_km
```

`deployment` is the placement's id, `drop` its 1-based index, `latitude`/`longitude`
the seed's 5-decimal position (what was forecast), `water_entry_utc` the seed's
absolute ISO `start`, and `cum_km` the arc length from the path start. The clicked
route corners are *not* exported — the drops lie along that route, so their ordered
positions give both the where and the when without the raw vertices. Drops are
captured whether or not **Forecast drift** is on (both draw drops), and **Clear**
wipes the captured waypoints along with the layers. The download is client-side (a
Blob + object URL), so it works under `pixi run serve` with no backend.

Leaflet fires two `click`s before a `dblclick`, so the finishing double-click's
near-duplicate tail vertex is dropped, and `doubleClickZoom` is disabled while armed
so the finish doesn't also zoom. The drop dots are coloured by their synced `t0` from
matplotlib's **tab20c** palette — a categorical map of five hue families (blue,
orange, green, purple, grey), each in four dark→light shades — indexed by mark ordinal
(`k = hours / mark_step`). Consecutive marks step through it, so every fourth mark
opens a new hue family and the three between are lightness steps of that hue: adjacent
marks stay easy to tell apart and the family boundary gives a coarse "which quarter of
the run" read. The palette is used discrete, not interpolated (blending a qualitative
map muddies it). A matching legend shows the swatch bands over the run's horizon, so a
pattern at one instant is read by eye by picking a colour. Each dot's tooltip pairs the
run-relative hours with the mark's absolute ISO time (`+6 h · 2026-…Z`).

Three independent read-by-click axes lift a slice of the array (enlarged / thickened,
dark-outlined; toggle off by re-clicking, or clear all with a background click):

- **click a `+Δt` dot** → every dot at that same mark hour of that deployment — the
  array's *shape at one instant* (a column).
- **click a drop disc** → every drop disc of that deployment — the whole set of
  water-entry points.
- **click a forecast line** (on bare track, between the markers) → that one drifter's
  trajectory (a row).

## Validation: cross-checked against OceanParcels v4

The hand-rolled RK4 is validated against **parcels v4** (the OceanParcels rewrite,
built from `main`), advecting the *same* cached window so the comparison isolates the
integrator. `_api_parcels.py` is a parallel API (`serve-parcels`, `:8002`) that
reuses `_api._load_window` verbatim and advects a **single point +12 h** — the
cadence `_api._HORIZON_H` (12 h) and `_api._MARK_HOURS` (3/6/9/12 h) it imports, kept
in `_api.py` for exactly this oracle even though the batch endpoint uses its own 48 h
default. `tmp_parcels_compare/` is the harness. Findings:

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
