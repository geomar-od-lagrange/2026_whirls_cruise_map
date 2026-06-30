> **Implemented** → [docs/forecast.md](../../docs/forecast.md). Built as planned;
> one addition beyond the intent: a sidebar **Drift forecast** panel carrying the
> caveat (current-advection estimate, field frozen at `valid_time`; surface
> current only) and the dashed-line legend, since positions get read off the line.

# Current-advection forecast per drifter head (1 / 3 / 6 h)

A toggleable dashed line drawn forward from each drifter's latest position,
obtained by advecting a passive particle through the CMEMS surface-current field.
The line runs to **6 h**, with dots marking the **1 h, 3 h and 6 h** positions.
Same field that drives the animated flow trails, so the line follows the visible
flow — but computed from the *true* velocities, so its reach is physically
meaningful.

## What it is — and what it is not

It is a **streamline advection through the present field, frozen in time**: start
at the drifter head, step `dx/dt = u(x,y)`, `dy/dt = v(x,y)` forward, draw the
path. It is the quantitative version of what the particle animation shows
qualitatively.

It is **not** a true time-evolving forecast and not a calibrated drifter
prediction. The 1/3/6 h dots exist precisely so a reader can see how far out the
estimate is being trusted — the near dots are solid ground, the 6 h dot is the
least certain. Be explicit in the legend, because positions get read off this:

- **Frozen field.** We hold one CMEMS snapshot (`fetch_field` pulls the time
  nearest now; the dataset is 6-hourly, `PT6H-i`). The 6 h horizon therefore
  spans ~one full field step — the real field would have advanced by then. 1 h and
  3 h sit comfortably inside; 6 h is the edge of what one frozen field supports,
  hence a marked horizon rather than the default read.
- **Surface current only.** `uo`/`vo` are the modelled surface current. A real
  drifter adds windage / Stokes drift (undrogued) or samples a deeper layer
  (drogued); none of that is here. So this is an *indicative passive-tracer*
  track, not the drifter's predicted path.

## Why compute it in the build (recommended)

The build already holds both inputs: the true `field` from
`_currents.fetch_field()` and every drifter's latest position (the `tracks`
DataFrame). Integrate there and emit a small `forecast.geojson`; the client just
renders it. This:

- uses the **true** `uo`/`vo` (m/s, native grid), not the animation's
  compressed/coarsened `currents.json` — correct distances, while direction still
  matches the visible trails (the γ-compression preserves direction);
- avoids the coastal **bleed**: `currents.json` fills land with zero velocity
  (`_currents._component`), but the raw field keeps land as `NaN`, so the
  integrator can *stop* at the coast instead of being dragged across it;
- keeps the client thin — no field shipped to the browser, no JS interpolation.

The forecast is then as fresh as the build, which is right: it is anchored to the
field's `valid_time` and to the same drifter fixes the markers use, all refreshed
together each run.

### Alternatives weighed

- **Client-side integration over `currents.json`.** Rejected: that grid is
  magnitude-compressed (wrong speeds) and coarsened, and has the land-bleed. Doing
  it right client-side would mean shipping the true field too — more data and JS
  for no freshness gain.
- **Time-varying multi-step forecast.** Advect through several CMEMS forecast
  timesteps rather than a frozen field. More faithful, especially at 6 h, but much
  more data/complexity. Out of scope; the natural next step if the frozen 6 h
  proves too coarse.

## Build: the integration

A new `_forecast.py` (keeps `_currents` focused), called from `build.py` after the
field and `tracks` are in hand; best-effort like the other currents artifacts (a
CMEMS miss just skips it).

- **Inputs:** the true `field` (`uo`/`vo`, lat/lon, NaN land) and each drifter's
  latest `(lon, lat, D_number, batch)` — `batch` from the *latest* fix, the same
  key the marker and trajectory use, so the forecast couples to them. Every
  drifter with a valid latest fix gets one, single-fix drifters included (a
  forecast needs only a position, not a past track).
- **Stepper:** RK4 to 6 h with a fixed step (≈5–10 min); bilinear interpolation of
  `uo`/`vo` at the particle each sub-step. Velocity m/s → deg:
  `dlat = v / R · 180/π`, `dlon = u / (R·cos lat) · 180/π`, `R = 6.371e6`. Step
  count/scheme is a tuning detail — at ~0.5 m/s a particle moves ~11 km in 6 h
  (~1 grid cell), so accuracy is not delicate.
- **Stop conditions:** leave the bbox, or enter a cell with any `NaN` corner
  (coast). Truncate the line there; emit only the horizon marks actually reached.
- **Output `forecast.geojson`:** one `LineString` per drifter from its head,
  vertices every ~15 min for a smooth dashed curve. Properties:
  `D_number`, `batch`, `valid_time`, and `marks` — a list `[{hours, lon, lat}]`
  for each of 1/3/6 h the integration reached (parallels the `fixes` pattern in
  `tracks_geojson`, so the client places the dots without re-deriving timing).

## Client: rendering and toggle

Mirror the trajectories work (`docs/trajectories.md`):

- Fetch `forecast.geojson` (optional, like `tracks`).
- Group by `batch`. Per drifter draw a **dashed** line from the head (Leaflet
  `dashArray`), styled clearly apart from the solid past track, plus a small
  **dot at each `marks` entry** (1/3/6 h). The dots are plain markers — **no
  tooltip/popup for now**. Colour: a forecast hue distinct from the orange track
  and blue head (TBD at build time).
- **Toggle:** a master **"Forecast (1/3/6 h)"** checkbox in the Drifters control,
  composed with the batch rows exactly as Trajectories is — a batch's forecast
  shows only when its batch row and the forecast row are both checked. Default
  off.

### Control refactor (do this first)

`buildBatchControl` currently hard-codes one master-toggled per-batch overlay
(`trackGroups`). There are now two (trajectories + forecast), so generalize it:
pass a **list** of overlays `[{label, groups, on}]`, render one master row per
overlay whose `groups` is non-empty, and have `sync()` toggle each
`overlay.groups[batch]` by `batchOn[batch] && overlay.on`. Markers stay the
always-batch-governed base layer. This drops the trajectory-specific branch in
favour of the general shape and makes the next such layer a one-line addition.

## Caveats to put in the UI / docs

- Label it a **current-advection estimate (field frozen at `valid_time`)**, not a
  forecast of the drifter; the 6 h mark is near the field's own 6-hourly step.
- Surface-current proxy; no windage / drogue depth.
- Lines truncate at the coast and at the field edge (fewer marks shown).

## Decisions (resolved)

1. **Horizons 1 / 3 / 6 h** — one line to 6 h with dots at the three marks.
2. **Per-batch**, coupled exactly like trajectories (master toggle × batch row).
3. **Dashed** line, **dots at 1/3/6 h**, **no tooltip** on the dots for now.
4. **Just the line** — no endpoint-displacement popup for now.

## Out of scope (deferred)

- Time-evolving multi-timestep advection (the faithful fix for the 6 h horizon).
- Tooltips / endpoint displacement on the marks.
- Ensemble / uncertainty cone; drogue-depth or wind-corrected drift physics.
