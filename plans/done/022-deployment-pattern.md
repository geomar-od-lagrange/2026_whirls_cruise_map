> Implemented — see [docs/interactive_forecast.md](../../docs/interactive_forecast.md).
> The jet-fence and Z patterns this describes are superseded by the one polyline
> Deploy tool + batch API ([023](023-simplify-deploy-polyline.md)); the open
> `t0`-inversion problem here is unchanged.

# Interactive deployment-pattern planner (jet frame)

Prototype: extend the interactive click-to-forecast tool
(`plans/021-interactive-forecast.md`, `docs/interactive_forecast.md`) so the
planner lays a whole **drifter deployment fence** across a jet and forecasts
every drifter, instead of forecasting one clicked point.

## The planner's frame

Someone planning this deployment is sampling a **jet** — e.g. the shear line
between the two poles of a dipole eddy. They do not think in latitude/longitude
and compass headings; they think in a frame tied to the feature:

- **across-jet** — the direction the ship steams to *cross* the jet;
- **along-jet** — the jet itself;
- origin at the **jet core**.

So the tool is authored in that frame and projects to geographic coordinates
only at the end. Two clicks establish the frame:

1. **core** — the jet centre (the anchor; visible on the currents/vorticity
   overlay). Clicked first so the array previews outward from it.
2. **start** — the western end of the crossing.

`core − start` is the across-jet (crossing) direction; the jet runs
perpendicular; `|core − start|` is the across-jet half-reach; and the crossing's
eastern **end** is the mirror of start about the core (start and end at opposite
across-jet values, the same along-jet value — on the axis through the core).

Everything else is **absolute distance (km)**, not feature-scaled units — the
planner thinks in metres/miles once the frame is set: `fence_height_km`
(along-jet extent), `node_spacing_km` (across-jet), `row_spacing_km` (along-jet).

## The array

A **fence of jet crossings**: rows spanning the across-jet reach, stacked
along-jet over the fence height, alternate rows staggered by half a node (a
quincunx) for denser pair separations. The ship steams it as a serpentine from
the south-west corner, so consecutive drops are adjacent and `cum_km` is the
real along-track distance — which the API turns into staggered water-entry
times. Pickets and the finer multi-scale micro-clusters of the original QUINCUNX
array (Novelli's `QuincunxPicketsWHIRLS.m` — see [Reference](#reference); the
two-way-verified port lives in git history) are out of scope for this prototype
— this is the macro fence in the frame.

## A second pattern: the Z

A simpler sibling for when a jet frame is the wrong mental model: a **Z** across a
box. The ship steams the top edge → the diagonal → the bottom edge, dropping
drifters at ~equal along-track spacing (a `node_spacing_km` knob; both corners are
drops).

The box is set by **three clicks** — because two can't. Two clicks give the top
edge's two ends (its direction = the box orientation, its length = the width), but a
rectangle needs one more number *and* a side bit: a scalar knob can't say *which*
side of the edge the box falls on. So the **third click** is a side/depth point: its
**signed** projection onto the edge-perpendicular picks the side (the sign) and the
height (the distance); the box stays a true rectangle (the along-edge component is
dropped). This rotates freely and needs no height knob — a live preview flips the box
to whichever side the cursor crosses. (A diagonal-corner pair was the first cut but
can't encode rotation — a diagonal is consistent with infinitely many rotated
rectangles.)

It shares nothing with the jet frame but the km/tangent-plane convention and the
`Waypoint`/`cum_km` contract — so the *same* staggered-entry, synced-`t0` forecast
machinery (below) serves it unchanged. The two patterns coexist as sibling
tools/endpoints; the array geometry is the only difference.

## Build shape

Server-side geometry (single source of truth; the field stays put, only the
answer ships):

- **`_pattern.py`** — `build_pattern()` (fence) returns a `Deployment` of ordered
  `Waypoint`s + a `JetFrame` (core/start/end, bearing, reach, height); the sibling
  `build_z_pattern()` (Z) returns a `ZDeployment` of the same `Waypoint`s + a `ZBox`
  (the four corners + Z-path). `Waypoint` is the shared contract (`index, lon, lat,
  cum_km, kind`; the jet-frame `across_km`/`along_km` are optional, `None` for the
  Z). Tangent plane, geographically correct (cos-lat longitude).
- **`/api/deployment` + `/api/deployment_z`** (in `_api.py`) — sibling endpoints,
  one per pattern, both delegating to a shared **`_assemble_plan(waypoints, …)`**:
  `drop` Points (kind/index/eta/cum_km, plus the fence's across/along), a
  `ship_track` LineString, `forecast` LineStrings per drifter from its **staggered**
  entry (`run start + cum_km / ship_speed`, reusing `_forecast._advection_feature`),
  and the pattern's own guide (`frame` or `box`) in top-level properties. The
  geometry is unconditional; an out-of-window `start` degrades to `forecasts: 0`
  (not a 422) — a *plan* doesn't depend on the field covering its time.
- **synced-t0 dots** — the drift dots are the first read on the inversion below.
  Rather than dotting each drifter at `+3/6/… h` from its *own* entry (which never
  line up in time), every drop is integrated to a **common wall-clock end** (run
  start + `horizon_h`, now 24 h) and dotted at **absolute** times `run start +
  k·3 h`. So one dot colour is a single instant across the whole array — its shape
  at that `t0`. A drop entering after mark *k* has no dot there; later drops carry
  shorter tracks. `marks[].hours` is run-relative; the client colours by it.
- **client** (`app.js`/`style.css`) — two sibling top-right tools sharing the
  deploy pane: **"Deploy pattern"** (fence: click core, then start; live jet-frame
  preview; km knobs for height + spacings) and **"Deploy Z"** (click the top edge's
  two ends, then a side point; live preview draws the edge, then the box + Z-path
  flipping to the cursor's side; one drop-spacing knob). Both draw drops, the ship
  track, and their guide handles, and share the drift/dot renderer + the legend: the
  per-drop dots are colour-ramped by their synced `t0` (a plasma ramp, run start →
  horizon), so a pattern at one instant is read by eye by picking a colour.

## Deferred forks

- **Fence height** is currently a numeric knob; a drag handle is the natural
  upgrade.
- **Pickets** and the multi-scale micro-clusters (triplets / bridges) re-add as
  kinds once the macro frame is settled.
- **Wind/swell rotation** — an operational tweak *on top of* the jet frame
  (offset the crossing bearing for a safe ride), not the primary orientation.

Prototype only — no persistence, no build artifact, same ad-hoc green family as
the click-forecast. Runs under `pixi run serve` + `pixi run serve-api`.

## Reference

The array geometry follows Gui Novelli's MATLAB package, the source of the
QUINCUNX/pickets pattern this fence is the macro skeleton of:

- Repo: <https://github.com/guillaumenovelli/Lagrangian-Drifter-Array>
- Cite: Novelli, G. (2026). *Lagrangian Drifter Array Simulator & Scale
  Optimizer (WHIRLS)*. Zenodo. <https://doi.org/10.5281/zenodo.20650545>

That package designs the array **forward in space** — layout, ship routing, and
pair-separation / triad-scale analysis for resolving submesoscale
velocity-gradient tensors. It does **not** invert a target pattern: it takes the
drop coordinates as the design and analyses their geometry, without advecting
anything through the flow.

The open problem this planner sets up is the **inverse**: the reference time for
a flow-map / deformation estimate (Haller's `t0`) is when the array is *complete*
in the water, but the ship lays it over hours, so the first drifters have already
drifted by the time the last enters. The clean fence exists in the *deploy*
frame, not at `t0`. Inverting it — pick the ideal `t0` configuration, then
backward-advect each node through the field to the time its drifter is dropped,
so staggered deployment *lands* the array in that configuration — is a
forecast-driven step neither this prototype nor Novelli's package does yet. It is
a fixed point (a node's drop time depends on the ship track, which depends on the
drop positions), so it iterates. Tracked as a follow-on (see
[ROADMAP](ROADMAP.md)).
