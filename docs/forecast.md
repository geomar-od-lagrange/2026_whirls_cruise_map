# Drift forecast & hindcast (current advection, ±1 / 3 / 6 h)

Two toggleable **solid lines drawn from each instrument's latest position** — the
drifters and the gliders (XSPAR buoy, seagliders; see [gliders.md](gliders.md))
alike — through the CMEMS surface-current field, with dots at the **1 h, 3 h and
6 h** marks: the **forecast** integrates the field *forward* (violet, +6 h), the
**hindcast** integrates the *same field backward* (magenta, −6 h). Both advect a
passive particle through the current, and the current is sampled **at the
particle's own clock time** — an hourly field window, not a single snapshot — so
the path curls into the near-inertial loop the model already carries (period ~15–24
h at these latitudes) rather than following a straight streamline.

The hindcast is a **current-only back-trajectory** — where the surface current
*would have* carried a particle into the drifter over the past 6 h — **not** the
drifter's observed past track (that is the orange trajectory line; see
[trajectories.md](trajectories.md)). Comparing the two shows how much of the
drifter's recent motion the surface current alone explains.

## What it is — and what it is not

Each is a **path through the time-dependent field**: start at the instrument head,
integrate `dx/dt = u(x, y, t)`, `dy/dt = v(x, y, t)` forward (forecast) or backward
(hindcast) while a clock advances alongside the position, draw the path. It is the
quantitative version of what the particle animation shows qualitatively, but done
against the *true* `uo`/`vo` (m/s, native grid) evolving through the window.

It is **not** a calibrated drifter prediction. The 1/3/6 h dots exist precisely so
a reader can see how far out the estimate is being trusted — the near dots are
solid ground, the 6 h dot is the least certain. Two things bound it, both stated in
the sidebar because positions get read off this line:

- **Surface current only.** `uo`/`vo` are the modelled surface current. A real
  drifter adds windage / Stokes drift (undrogued) or samples a deeper layer
  (drogued); none of that is here. So this is an *indicative passive-tracer* track,
  not the drifter's predicted path. For the **gliders**, which maneuver actively,
  it is further a passive-drift *what-if* — where the current alone would carry the
  platform — meaningful for its drift phases, not a track prediction.
- **Model near-inertial amplitude.** The inertial loop is only as strong as the
  model's. Calibrated against the deployed drifters, the CMEMS field carries
  roughly **0.4–0.65 of the observed near-inertial amplitude** (the range of
  deployment medians; window-to-window spread is larger) with the **phase
  right** (+6° ± 27°) and the rotation sense correct — so the loop's shape and
  timing can be trusted more than its size. **No gain is applied**: no scalar
  or parameterized amplitude correction survived validation (see
  `plans/done/013-inertial-gain-generalization.md`). The calibration covers
  one region and period so far.

## Why it is computed in the build

The build already holds every input — the instrument positions (the `tracks`
DataFrame and the `gliders` list; see [gliders.md](gliders.md)) — and fetches the
hourly current window (`_currents.fetch_field_window()`) for the advection, so
`_forecast` integrates there and emits small `forecast.geojson` and
`hindcast.geojson` artifacts (forward and backward); the client just renders them.
This:

- uses the **true** `uo`/`vo` (m/s, native grid), not the animation's
  magnitude-compressed, coarsened `currents.json` — correct distances, while
  direction still matches the visible trails;
- avoids the coastal **bleed**: `currents.json` fills land with zero velocity so
  the trails smear ashore (a known flow-trail limitation; see `plans/BACKLOG.md`),
  but the raw field keeps land as `NaN`, so the integrator can *stop* at the coast
  instead of being dragged across it;
- keeps the client thin — no field shipped to the browser, no JS interpolation.

The forecast is then as fresh as the build, anchored to the window time nearest now
(its `valid_time`, the integration's t = 0) and to the same instrument fixes the
markers use, all refreshed together each run.

### Alternatives weighed

- **Single frozen field.** The previous approach advected through one CMEMS
  snapshot held fixed in time. Simpler and cheaper, but it *discards the
  oscillation the model carries*: with the field constant, the particle sees a
  constant velocity and traces a straight streamline, so the drifters' visible
  inertial loops never appear. Replaced by the hourly window — the same fetch shape
  costs only ~+0.8 s and one dataset swap (see `plans/012-near-inertial-forecast.md`).
- **Analytic slab near-inertial model.** Add a Pollard–Millard slab NI velocity on
  top of the current. Tested and dropped: matching the drifters' observed
  amplitude would require an implausibly shallow mixed layer (~23–38 m where
  the model's is ~41–204 m), and the slab response is in phase with CMEMS's
  own near-inertial signal — the model field already contains the wind-forced
  response, so adding a slab on top would double-count it. See
  `plans/done/inertial_slab_model.md`.
- **Coefficient decomposition.** Fit per-cell `(mean, near-inertial amplitude,
  phase)` and reconstruct the field analytically, instead of shipping the hourly
  window. The decomposition exists as library code with no build artifact
  (`_inertial.py`: a per-cell `(mean, A, φ)` least-squares fit of the hourly
  window, `GAIN = 1.0` as the calibration seam); it is the seam a future
  validated amplitude gain or a compressed advection artifact would plug into
  (`plans/012-near-inertial-forecast.md`, Phase 3). The advection reads the
  raw hourly window.
- **Client-side integration over `currents.json`.** Rejected: that grid is
  magnitude-compressed (wrong speeds), coarsened, and land-bled.

## The integration

`_forecast.py` (kept separate from `_currents` so each stays focused) integrates
each instrument independently:

- **Inputs.** The hourly current window (`uo`/`vo`, lat/lon, **time**, `NaN` land)
  and each instrument's latest position with an identity + a toggle key: a drifter
  head is `(lon, lat, D_number, batch)`, a glider head `(lon, lat, id, type)` —
  `batch` (drifters) and `type` (gliders) are the same keys the marker and track
  toggle under, so the advection line rides the same instrument row. The heads are
  gathered by `_drifter_heads` + `_glider_heads`. Every instrument with a valid
  latest fix gets one, single-fix ones included (advection needs only a position,
  not a past track).
- **Stepper.** RK4 to ±6 h with a fixed 5-min sub-step — a signed step, forward for
  the forecast and backward for the hindcast (the shared
  `_advection_geojson(field, tracks, gliders, direction)`, wrapped by
  `forecast_geojson` and `hindcast_geojson`) — sampling the field **bilinearly in
  space and linearly in time** at the particle each stage, with the clock advancing
  by the step (`_Field.velocity(lon, lat, t)`, `t` in epoch seconds). t = 0 is
  anchored to the window time nearest now. Velocity m/s → deg: `dlat = v / R · 180/π`,
  `dlon = u / (R cos lat) · 180/π`, `R = 6.371e6`. The scheme is not delicate — at
  ~0.5 m/s a particle moves ~11 km in 6 h, about one grid cell — so accuracy is
  dominated by the field, not the step (a uniform-flow check lands the 6 h mark to
  sub-metre).
- **Stop conditions.** `_Field.velocity` returns `None` once the particle leaves
  the grid, leaves the fetched time window, **or** enters a cell with any `NaN`
  corner (coast) at either bracketing time. RK4 aborts the step, so the path
  **truncates at the last fully-ocean vertex** — one cell short of land, never
  across it. Only the horizon marks actually reached are emitted.

## Artifacts: `forecast.geojson` and `hindcast.geojson`

Identical shape (one is the forward integration, the other the backward). One
`LineString` per instrument from its head, a vertex every 15 min for a smooth
curve, coordinates `[lon, lat]` rounded to 5 dp (~1 m, far below the ~10 km
displacement). Properties:

- the head identity — `D_number` for a drifter, `id` for a glider — plus `batch`
  (the instrument key its marker/track toggle under: the drifter batch, or the
  glider `type`) and `valid_time` (the integration's t = 0);
- `marks` — a list `[{hours, lon, lat}]` for each of 1/3/6 h the integration
  reached, with `hours` **signed by direction** (positive in the forecast,
  negative in the hindcast); parallels the per-vertex `fixes` pattern in
  [`tracks_geojson`](trajectories.md), so the client places the dots without
  re-deriving timing.

An instrument whose head is already on land or off-grid yields no usable line
(`<2` vertices) and is skipped; it still shows its latest-position marker. Each
artifact is an independent best-effort build step, so one can be present without
the other.

## Client rendering and toggles

`app.js` fetches `forecast.geojson` and `hindcast.geojson` (optional, like
`tracks`) and groups each by `batch` via the shared `buildAdvectionGroups(geojson,
color)` — the glider features, keyed by their `type`, group under `xspar` /
`seaglider` right alongside the drifter batches. Per instrument it draws a **solid
line** from the head — **violet** for the forecast, **magenta** for the hindcast,
both distinct from the orange observed track and the coloured head marker — plus a
small dot at each `marks` entry (1/3/6 h). The lines and dots are
**non-interactive** and carry **no popup** — they are plain position marks — so
they never swallow a click meant for a marker beneath them.

The layers are governed by the **Instruments** control (top-right), not the Leaflet
layer control — the same control that filters drifter batches and glider platforms
(see [batches.md](batches.md)) and toggles trajectories (see
[trajectories.md](trajectories.md)). The control takes a **list of overlays**
`[{label, groups, on}]`; True track, `Forecast (1/3/6 h)` and `Hindcast (1/3/6 h)`
are entries, each a master row above the instrument rows. They compose identically:
**an instrument's forecast/hindcast shows only when both its own row and that
master row are checked**, so unchecking an instrument hides its markers, its track,
its forecast *and* its hindcast together. Default off. (Gliders carry a forecast
and hindcast; the drifters carry all three overlays.)

The sidebar **Drift forecast & hindcast** panel — the two were merged, being the
same field with the same caveats — states the `valid_time` (the integration's
t = 0) via `renderDriftInfo(forecast, hindcast)`, with a static note that the lines
advect a **surface point particle by the currents only** (no wind, waves, or the
instrument's own motion) and that the hindcast is a current back-track, not the
observed track. `valid_time` is read off the first available feature — one window,
one anchor time for every line.
