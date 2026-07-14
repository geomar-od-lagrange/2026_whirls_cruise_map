# 037 — UX review batch: z-order, colors, control redesign, responsiveness

A batch of eight review issues against the deployment-focused frontend (plans
034–036). Everything lands in `site/map/{app.js,style.css,index.html}` — the map
is a static Leaflet app, no build step. The work splits into three clusters that
run **sequentially on the same working tree**, in this order, because B rebuilds
the very control bodies A recolors and C re-times:

- **A. Render & colors** — #20 pane z-order, #21 Marion Dufresne dark blue,
  #22 violet forecast tracks for the real deployed drifters.
- **B. Controls redesign** — #23 deploy-tool layout, #24 merge instruments +
  ships (seaglider→glider rename, select/deselect-all).
- **C. Responsiveness** — #17 static-overlay snapshot toggle, #18 show-tracks
  eventual consistency, #19 now-point affordance.

Two of these are architectural, not cosmetic — flagged inline: **#17** (decouple
per-frame animation cost from scrubbing: snapshot vs live) and **#18** (decouple
control state from the async render it triggers). Everything else is localized.

Update the matching `docs/*.md` in the **same pass** as each cluster (docs
describe what *is*, no changelog narration): `ship.md` (A/#21), `trajectories.md`
+ `features.md` (A/#20, #22), `deploy.md` (B/#23), `controls.md` + `batches.md` +
`gliders.md` (B/#24), `currents.md` (C/#17), `controls.md` (C/#18, #19).

---

## Cluster A — Render & colors

### #20 — all markers above all tracks

**Root cause.** The single z-order authority is the `createPane` block at
`app.js:3098-3118`. Track panes are interleaved *among* the marker panes:
`shipTrack` (640) sits above Leaflet's default `markerPane` (600) where the
**glider** markers live (`buildGliderMarkerGroups`, ~2565, `L.marker` with **no
`pane`** → default 600), and `deployTracks`/`deployDrops` (663/664) sit above the
`drifters` (650) and `ship` (660) marker panes. So the MD ship track paints over
sg284 — the reported bug — and deploy drift lines paint over drifter heads.

**Fix (decided).** Lower **every track/line pane below Leaflet's default
`markerPane` (600)**. This alone lifts the glider markers (which live in 600)
above all tracks — no new glider pane needed. New ordering, bottom→top:

- `shading` 350, `inertial` 360 (unchanged)
- observed drifter/glider track lines: default `overlayPane` 400 (already below
  markers — unchanged)
- `shipTrack` 640 → **410**
- `driftForecast` (**new**, the violet #22 lines) → **420**
- `deployTracks` (deploy drift lines) 663 → **430**
- `deployDrops` 664 → **440** — the drop discs are placement points of the PoC
  deploy tool, not instrument markers; keep them just above the deploy lines but
  still below all real markers (the issue is about *markers*, and drops read as
  part of the drift geometry). *(Judgment call — if drops should read as markers
  instead, raise to ~645; leave a comment either way.)*
- markers: `drifters` 650, glider markers 600 (default), `ship` 660,
  `atTime` 670, `tooltipPane` 680, `popupPane` 700 (unchanged)

Rewrite the narrating comments at `3092-3118` (they describe the *old* order and
the drops-below-drifters click rationale — which now flips: with tracks under 600
the click-interception concern that motivated `shipTrack` < `drifters` is moot).

**Risk.** The comment at 3092-3097 explains `shipTrack` was deliberately below
`drifters` so early ship-track dots don't intercept clicks in the pre-deploy
cluster. Lowering it further keeps that property (still below markers), so no
regression. Verify glider markers still receive clicks (they should — 600 is now
strictly above every line pane). The observed track *hover tooltips* live in
`tooltipPane` 680 and are unaffected.

### #21 — Marion Dufresne in dark blue

`VESSELS.md.trackColor` and `.markerColor` are both `#1a1a1a` (near-black) at
`app.js:74-76`; `haloColor` `#ffffff`. Change both to a **dark navy** — proposed
`#0b2350`. `trackColor` drives the track polyline (`segFor`, ~2934);
`markerColor` drives `shipIcon` (~2892). Both flow from this one config, so it is
a two-value edit plus:

- update the color-inventory comment at ~`app.js:83`,
- update `docs/ship.md`.

**Rationale / risk.** The navy must read apart from the existing blues:
`deployment_1` drifter blue `#1f5fa8` / fill `#3a8ddb` (l.102) and seaglider
sky-blue `#38bdf8` (l.2515). `#0b2350` is much darker than all three and keeps a
white halo, so on the map it reads as a distinct dark line rather than merging
with the drifter cluster. If it still reads too close under the shading overlay,
darken toward `#08182f`.

### #22 — violet forecast tracks for the REAL deployed drifters

**Approach (DECIDED): use the API.** Reuse the exact machinery the deploy tool
already drives — `FORECAST_API = resolveApi("/api/forecast")` (l.1173),
`getDeployLimits` for the availability gate, and the `POST` shape from
`commitDeployment` (l.1912–1930): `{ seeds:[{lon,lat,start}], horizon_h,
direction }` → response `features` with `role:"track"`. Do **not** block map init
on it — fire once, asynchronously, after the map is up.

**Seed collection.** The real deployed drifters are the drifter heads whose
`batch` is a `deployment_*` (i.e. **not** `pre_deploy`) — the other instruments
(gliders/floats/xspar/waveglider) are explicitly excluded by #22. Their last real
fix is exactly what `latest.geojson` carries per drifter (`buildBatchGroups`,
l.695: `properties.batch`, `D_number`, `date_UTC`, coords `[lng,lat]`). Collect
seeds directly from that source geojson (it is already loaded for the heads):

```
seeds = latest.features
  .filter(f => f.geometry?.type === "Point"
            && String(f.properties?.batch).startsWith("deployment_"))
  .map(f => ({ lon: f.geometry.coordinates[0],
               lat: f.geometry.coordinates[1],
               start: f.properties.date_UTC }));
```

(Equivalently, the **last vertex** of each `tracks.geojson` LineString — same
point/time, slightly more work. `latest.geojson` is the simpler seed source.)

**Request shape.** Mirror `commitDeployment`: one POST, `direction:"forward"`,
and a **single** `horizon_h` large enough that *every* seed reaches the end of
the data period. Each drifter's last fix has a different time, so rather than
per-seed horizons, send `horizon_h = spanHours` (the full field span, `(clockTN −
clockT0)/3600000`, computed at `3236-3241`). The server advects each seed from
its own `start` and truncates at the field's last frame, so every track ends at
end-of-data and none overshoots meaningfully. Seeds whose `start` predates the
window (`clockT0`) are silently skipped by the API (`p.skipped`) — acceptable;
those drifters simply get no forecast. Plumb `spanHours`/`clockTN` into the
kickoff (they live in `main`'s clock block).

**Rendering.** Write a **slim sibling** of `drawDeployForecastLines` (l.2152) —
call it `drawDrifterForecastLines` — that keeps only the full-line draw and drops
the deploy-tool-specific machinery (no `registerAtTimeMarker`, no
`selectDeployTrack` hit-line, no `deployTracks[trackKey]` registry). One
`L.polyline` per `role:"track"` feature:

```
L.polyline(latlngs, { pane: "driftForecast", color: VIOLET_FORECAST_COLOR,
                      weight: 2, opacity: 0.85, interactive: false })
```

Add `const VIOLET_FORECAST_COLOR = "#7c3aed";` beside `DEPLOY_COLOR` (l.1208) —
the codebase comments at 1206/2248 already reserve the word "violet" for forecast
lines. The lines go into the **new `driftForecast` pane (420)** from #20, so they
sit below every marker but above the ship track. Collect them in one
`L.featureGroup` added to the map when the response arrives.

**Kickoff & gating.** After the dock is assembled in `main`, call an async
`kickDrifterForecasts()` that: (1) `await getDeployLimits()` (the same gate the
Deploy tab uses to decide reachability) — if the dynamic `/api/forecast` server
is absent, **return silently**, no violet layer (a static-only deploy just won't
show forecasts); (2) build seeds; (3) POST; (4) draw. Not awaited by `main` —
loading is allowed to lag.

**Visibility.** Draw them **always shown, full line**, matching the virtual
deployment drift lines' treatment (plan 036: the master governs only *observed*
tracks; forecast/virtual lines stay). *(Alternative if it clutters: give them one
"Drifter forecast" checkbox — but default to always-on to avoid growing the panel
this cluster.)*

**Risks.** (a) **API availability is the feasibility gate** — this branch is
`deployment-focused-app`; `FORECAST_API` is not a static artifact. The silent
gate keeps a static deploy from erroring, but the violet tracks only appear where
`serve-api` runs. (b) A drifter fix older than `clockT0` is skipped — expected.
(c) `spanHours` as horizon is an upper bound; confirm the server truncates rather
than errors on over-long horizons (the deploy tool already sends finite horizons;
a very large one should just clamp at the field edge — verify against `/limits`
`max_horizon_h` and clamp to it if present).

---

## Cluster B — Controls redesign

### #23 — deploy controls

All edits are inside `buildDeployTool.renderBody` (`app.js:1458-1748`) plus the
state head (`1381-1393`) and the deploy CSS (`style.css:615-902`). Reorder the
`create()` blocks; the backing state values (`horizonH`, `shipKn`, `spacing`,
`timing`) stay.

**New top-to-bottom order** (currently Settings → click-to-place → CSV →
Deployments → Clear-all → status):

1. **Settings** (no "Settings" caption — already dropped in 036): Release row,
   Direction switch, Timing switch, then the redesigned duration + speed/spacing
   rows below.
2. **Deployments** list *with its visibility checkboxes* — moved **up**, above
   click-to-place (`renderManager`, l.1693-1734).
3. **Deploy** toggle + **Clear** button side by side (was "Click-to-place" +
   separate "Clear all").
4. **CSV import / export** `<details>` — moved to the **very bottom**
   (l.1620-1682).
5. status line.

**Duration slider.** Replace `numRow(settings,'Duration (h)','horizonH')` (l.1574)
with a **4-stop segmented slider — 1d / 2d / 5d / inf — defaulting to 5d**, no
headline. Map stops to `state.horizonH` in hours: `24 / 48 / 120 / <inf>`. For
"inf" reuse the #22 trick: set `horizonH = spanHours` (advect to end of field;
server truncates at the field edge). Plumb `spanHours` into `buildDeployTool`
(new param) so the "inf" stop has a concrete hour value. Default `state.horizonH`
becomes `120` (5d). Check every `horizonH` consumer (`computeSeeds`,
`placeDeployment`, `commitDeployment` → `horizon_h`) tolerates the large "inf"
value — clamp to `/limits.max_horizon_h` if the API reports one. Build it as a
segmented control (four buttons / a stepped `<input type=range min=0 max=3>`);
style beside `.pt-switch` in the CSS block.

**Speed + spacing condensed.** Collapse `numRow shipKn` (l.1575) and
`numRow spacing` (l.1576) into **one row** reading: `Drop every [ ] km at speed
[ ] kn` — two inline number inputs bound to `state.spacing` and `state.shipKn`,
literal text between them, no per-field labels. When Timing = **Instantaneous**,
grey the **speed** input and show an **infinity glyph (∞)** in place of the kn
value. `paintTiming` (l.1577-1582) already greys the ship row when instant —
retarget it to grey only the speed input and swap its display to ∞ (leave the km
spacing editable, since drops still have spacing when instantaneous).

**Toggle rename + Clear.** Rename the `.ft-toggle` text `Click-to-place: ON/OFF`
→ **`Deploy: OFF / ON`** (l.1589); drop the "Click to place" `section()` caption.
Move the **Clear** button (today "Clear all" in `.pt-manage-actions`, l.1736-1745
→ `clearAllDeployments`) to sit **next to the Deploy toggle** in a shared flex
row. The toggle still sets the deploy cursor + `doubleClickZoom` as before.

**Risk.** The "inf" sentinel is the one non-mechanical part — verify it against
the API's horizon cap so a 5-day-span "inf" doesn't get every seed skipped.
Moving the Deployments manager above click-to-place means `deployManagerRefresh`
still targets the same element — keep the registration (l.1733) pointed at the
relocated node.

### #24 — merge instruments and ships into one Drifters panel

Target layout (one panel, three groups separated by `---`):

```
## Drifters
[x] batch 1 (20)   [x] batch 2 (3)
[x] batch 3 (3)    [x] batch 4 (2)
[x] batch 5 (85)   [x] batch X (73)
---
[x] Glider (2)     [x] Float (4)
[x] XSPAR (1)      [x] Waveglider (2)
---
[x] M. Dufresne    [x] Agulhas II
   + select all / deselect all  (small text, bottom)
```

**Labels (pure data edits).** Every row's text is
`${batchLabel(batch)} (${group.getLayers().length})` at `app.js:811`; counts are
live layer counts (nothing to change). Relabel:

- `BATCH_LABELS` (l.120-127): `Drifter batch N` → **`batch N`**; `pre_deploy`'s
  `Drifter pre` → **`batch X`** (matches the "batch X (73)" row — the pre-deploy
  group).
- `GLIDER_STYLES.seaglider.label` (l.2515): `Seagliders` → **`Glider`**. Only the
  **visible row label** changes; the underlying `type` key `seaglider` stays
  baked in `data/gliders.geojson` + build pipeline + docs — renaming the key is
  out of scope for a label change (note this in `docs/gliders.md`).

**Two-column grid + dividers.** `buildInstrumentRows` (l.742-822) currently emits
a vertical flex list with a single `hr.batch-divider` (l.790) between drifters and
gliders. Change to a **2-column grid** (CSS at `style.css:575-613`) and insert a
**second divider** before the ships group. `instrumentOrder` (l.137-142) already
sorts drifters before gliders (float pinned last), so the drifter/glider split
falls where the first divider goes; the ship rows are appended after the second.

**Merge the ships in.** Ship visibility is today a **separate dock tab**
(`buildShipsTab`, l.882-920; wired at `main` l.3550 + l.3576). Fold it in:

- Render **both vessel rows eagerly** from `VESSELS` config (l.70-91) inside
  `buildInstrumentRows` — the two ships (`md`, `agulhas`) are known at config
  time, so no lazy-append is needed. Each row's checkbox toggles that vessel's
  track group; wire it through a per-vessel `setVisible(on)` that **queues/no-ops
  until the vessel's layer exists** (vessels are created lazily on first fix via
  `makeShipLayer`/`shipLayers`, `main` ~3626/3657). This replaces `buildShipsTab`'s
  lazy `addVessel` row-building with static rows whose handlers attach to the
  layer when it registers.
- **Delete** `buildShipsTab` and remove the `'ships'` tab entry (`main` l.3550 +
  l.3576) and its `.dock-tab` (CSS 425-572 — one fewer tab).

**Select all / deselect all.** Add a small-text control at the **bottom** of the
panel that checks/unchecks every row (batches + gliders + ships) and **fires each
row's change handler** so markers and tracks reconcile — not just a visual toggle.
Two links ("select all" / "deselect all") or one toggle.

**Coupling to preserve.** Unchecking an instrument row hides its markers **and**
its track (via `sync()`, l.768-773, gated by the scrubber's "Show tracks"
master). Keep this when restructuring the rows; `setTracksOn`/`setInstrumentTracks`
wiring (returned by `buildInstrumentRows`, driven from `main` l.3330-3335) must
still reach every row after the grid rebuild. The "Show tracks" master itself
stays in the scrubber (l.1050-1056) — untouched by #24.

**Risk.** The main structural change is ships-as-static-rows with deferred
handlers. Get the queue-until-layer-exists right so toggling a ship before its
first fix doesn't throw and correctly applies once the layer lands. Grid layout
must stay readable at the dock's narrow width — let rows wrap to one column on
overflow.

---

## Cluster C — Responsiveness

### #17 — static overlay snapshot toggle *(architectural)*

**Problem.** Both animated overlays run their **own continuous `requestAnimationFrame`
loop** and repaint every frame regardless of the clock: the near-inertial canvas
(`startInertialClock`, l.2383-2499, its own `tick` rAF) and the leaflet-velocity
flow trails (its internal Windy animator, built l.3398-3468). During time
scrubbing these compete for the main thread, so scrubbing stutters.

**Mechanism (decouple per-frame animation from the displayed state).** Add a
single **"Animate overlays"** toggle (default *on*) in the Currents tab governing
both overlays. When switched **off → static snapshot**:

- **Near-inertial** (our own canvas — full control): add a module flag read by
  `tick`. When static, render **one still frame** at `displayedFieldTime`
  (particles at their current positions, streaks drawn once, **no advection
  step**) and then **stop scheduling `requestAnimationFrame`**. Re-render the
  still on clock change and on `moveend/zoomend` (view change), but never
  free-run. When toggled back on, resume the rAF loop from the current instant.
- **Flow (leaflet-velocity)** — no native static mode; it animates via an
  internal Windy instance. Snapshot = let it lay down one pass of trails, then
  **stop its animator** so the last painted frame stays as a still (the layer
  exposes its canvas at `flowLayer._canvasLayer._canvas` and the animator via the
  Windy instance — `stop()` it, restart on toggle-on). On scrub while static, run
  one pass at the new frame then stop again (reuse the existing `scrubFlow`
  frame-swap, l.3439-3449, followed by an immediate stop). If reaching into
  leaflet-velocity internals proves brittle, the fallback is to render a **static
  streamline/arrow snapshot** of the current frame onto a canvas in the `inertial`
  pane and hide the animated layer while static — larger, but fully under our
  control.

The key property either way: **while static, no rAF fires during a scrub** — the
overlay is a still image re-rendered only on discrete state changes, so scrubbing
cost drops to the raster/track work alone.

**Risk.** leaflet-velocity's animator is a private API; guard the `stop()`/restart
against version drift (feature-detect, no-op if absent). Document the toggle in
`docs/currents.md`.

### #18 — show-tracks checkbox eventual consistency *(architectural)*

**Problem.** The "Show tracks" checkbox's `change` handler runs
`setTracksVisible` (l.3332-3337) **synchronously**: it flips `tracksOn`, toggles
every ship track, calls `setInstrumentTracks(on)` (adds/removes many polylines
across all batches + gliders to the map), and re-runs `updateClock` to re-clip
every track. All of that happens **inside the input event**, so the checkbox's
visual state doesn't repaint until the heavy work returns — the box "stalls till
tracks are loaded."

**Mechanism (decouple control state from render).** Flip the state and repaint
the checkbox **immediately and synchronously**, then run the reconcile
**off the event**:

- In the toggle handler, set `tracksOn = cb.checked` and return at once — the
  checkbox is now instantly consistent.
- Schedule the actual line add/remove + re-clip on the **next tick**
  (`queueMicrotask` / `requestAnimationFrame`), keyed to the latest desired
  state (a small `pendingTracksOn` so a rapid re-toggle collapses to the final
  value — eventual consistency, last-write-wins). Optionally chunk the per-batch
  add across a couple of frames if a single add still janks.
- Optional affordance: a brief "…" / disabled-looking pending style on the row
  while the deferred render runs, cleared when it completes — but the checkbox
  **check state itself never waits**.

This is the eventual-consistency model the issue asks for: control state leads,
render catches up asynchronously and reconciles to whatever the control last
said.

**Risk.** Guard against a toggle landing mid-render: the deferred worker must read
`pendingTracksOn` at run time (not close over a stale `on`), and `updateClock`
must be safe to call against a half-added set (it already iterates registered
entries). The same deferral should cover the standalone `buildTracksChip` path
(l.1123) when there is no scrubber.

### #19 — now-point affordance on the scrubber

**Problem.** The wall-clock "now" marker is a small, **non-interactive** blue dot
(`.ts-nowdot`, built l.1076; CSS `pointer-events:none` so it never blocks the
thumb). There is no easy way to jump the clock back to now.

**Mechanism.** Add a **clickable "now" affordance** — the cleaner option given the
dot deliberately ignores the pointer:

- Add a small **"⦿ now" button/chip** in the `.ts-head` row (l.1049-1058), beside
  the clock readout. Clicking it sets the range to the now offset
  (`Math.round((nowMs − t0Ms)/HOUR_MS)`, clamped to `[0, spanHours]`),
  dispatches the `input` event path (`setTime` + `onChange`) so every clock-aware
  layer follows, and can disable/hide itself when the clock is already at now.
- Additionally **make the dot attractive**: enlarge it slightly and give it a
  subtle pulse/ring in CSS so the "now" position on the line reads at a glance.
  Keep `pointer-events:none` on the dot itself (the button carries the click), so
  it still never blocks grabbing a thumb parked near now.

**Risk.** Minimal. Only surfaces when `nowMs` is inside `[t0Ms, lastMs]` (same
guard as the dot, l.1075). Ensure the dispatched jump goes through the exact
`onChange` used by dragging so overlays/tracks re-sync identically.

---

## Sequencing & shared risks

1. **A before B**: A only recolors/reorders panes and adds a forecast layer; B
   rebuilds `buildInstrumentRows` and `renderBody` wholesale. Doing A first keeps
   the pane/color edits out of B's larger diff.
2. **B before C**: C/#18's deferral wraps `setTracksVisible`/`setInstrumentTracks`,
   whose wiring B rearranges (rows + ship merge). Land the row structure first.
3. **#20 ↔ #22 dependency**: the violet `driftForecast` pane is defined in A/#20's
   restack; #22 draws into it. Do them in one A pass.
4. **API gate shared by #22 and #23-inf**: both lean on `/api/forecast` and its
   horizon cap — confirm `getDeployLimits`/`/limits` once and clamp both the
   drifter-forecast horizon and the deploy "inf" stop to `max_horizon_h`.
5. **Verify by serving locally** (`pixi run serve` + `serve-api` for the forecast
   paths) after each cluster; there is no JS linter or test harness.

## Docs to update (same pass, per cluster)

- A: `docs/ship.md` (#21 navy), `docs/trajectories.md` + `docs/features.md`
  (#20 stacking order; #22 violet forecast lines).
- B: `docs/deploy.md` (#23 layout), `docs/controls.md` + `docs/batches.md`
  (#24 merged panel, `batch N`/`batch X` labels), `docs/gliders.md`
  (row label `Glider`; note the `seaglider` data key is unchanged).
- C: `docs/currents.md` (#17 animate/snapshot toggle), `docs/controls.md`
  (#18 eventual-consistency master, #19 now affordance).
