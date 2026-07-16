# 040 — Defaults & quick wins

Four small frontend issues that are default flips or element removals, batched
because they touch nearby seams and carry little risk. All are `site/map/app.js`
(+ `site/map/style.css`) only — no build-pipeline or API change.

Issues: **#26** (deployment defaults), **#28** (default-show tracks), **#32**
(forward + backward together), **#36** (drop the "Now" button + now-marker pulse).

---

## #36 — Remove the "Now" button; stop animating the now marker

Pure removal. The scrubber's "Now" chip snaps the clock to the wall-clock hour;
the now marker is a small dot on the scrub line with a pulsing ring.

- **JS (`app.js`, `buildTimeSlider`):** delete `nowBtn` construction
  (`1173–1180`), the `syncNowBtn` helper and its calls (`1230`, `1232`, `1237`),
  and the click handler (`1240–1241`). Keep `nowOffset` (`1172`) only if still
  referenced after removal — grep; if the chip was its sole consumer, drop it too.
- **Now marker:** keep the static `.ts-nowdot` (it still marks where "now" sits
  on the span, `1198`) but remove its animation.
- **CSS (`style.css`):** delete the `.ts-nowbtn*` rules (`311–341`). Remove the
  pulse: the `animation: ts-now-pulse …` line (`382`), the `@keyframes
  ts-now-pulse` block (`384–388`), and the whole `prefers-reduced-motion`
  `@media` block that only silenced it (`388–390`, not just the inner rule — else
  an empty `@media` is left). Leave the base `.ts-nowdot` dot styling intact.

Follow-through: grep for `nowBtn`, `ts-nowbtn`, `ts-now-pulse`, `syncNowBtn` and
confirm zero references remain.

---

## #26 — Tweak deployment defaults

Two default-value edits in `buildDeployTool`'s `state` literal.

- **Spacing 2 → 10 km** — `app.js:1528` (`spacing: 2` → `spacing: 10`).
- **Instantaneous release by default** — `app.js:1536` (`timing: "alongtrack"`
  → `timing: "instant"`). This is the interpretation of #26's "Instantaneous
  end": the only "Instantaneous" control is the **Timing** switch
  (`1700–1707`, Instantaneous vs Along-track); defaulting it to `instant` puts
  every drop in the water at the release time (no ship-speed stagger).
  `paintTiming` (`1753–1760`) already greys the ship-speed input under
  `instant`, so the segmented UI reflects the new default with no extra work.

No structural change; the switches/inputs read their initial paint from `state`.

---

## #28 — Default to displaying ship + instrument tracks

Goal: tracks (drifter + glider + ship) show on first load, **without stalling
first paint** on the ~18 MB `tracks.geojson`.

**No refactor needed** (revised after review). An earlier draft assumed
`setInstrumentTracks` stayed a no-op until the Instruments tab was opened — that
is FALSE: `buildControlDock` renders **every** tab body eagerly inside `onAdd`
(`1077–1087`, "each body is built once and shown or hidden by display"), which
fires synchronously at `dock.addTo(map)` (`4072`). So `setInstrumentTracks =
inst.setTracksOn` (`4048`) is wired at startup, before the tracks fetch's `.then`
(`4084`) runs, and the post-fetch reconcile (`4087`) calls the real function.

The only real gap: `tracksMasterOn` starts `false` (`855`) and `sync()` at build
(`960`) therefore leaves the **glider** groups off until the drifter fetch
resolves at `4087` — gliders would needlessly wait on the 18 MB download. Ships
already show immediately (`ship.setTrackShown(tracksOn)`, `3998–3999`).

**The whole change is four edits:**
- `let tracksOn = false;` → `true` (`474`).
- `let tracksMasterOn = false;` → `let tracksMasterOn = tracksOn;` (`855`) —
  `tracksOn` (module var, `474`) is in scope; `sync()` at `960` then lights the
  glider groups at first paint, and `4087` lights the drifters on arrival.
- `tracks: { initial: false, … }` → `initial: true` (`3834`).
- `buildTracksChip(map, { initial: false, … })` → `initial: true` (`3871`, the
  no-field chip fallback).

**Non-blocking is already true** and must stay so: `tracks.geojson` is fetched
fire-and-forget (`4084`, not awaited), so first paint never waits on it; ships
and gliders show at once, drifters attach on arrival. Verify no `await` creeps
onto that fetch.

---

## #32 — Deployment: forward + backward together (OR, not XOR)

Today `state.direction` is a single string (`"forward" | "backward"`, `1531`),
rendered as a two-state switch (`1689–1693`) — structurally exclusive. #32 wants
both runnable at once.

**Model change.** Replace the single `direction` with two independent booleans,
e.g. `state.runForward` / `state.runBackward` (default forward on, backward off),
and swap the direction `switchRow` for two small toggles/checkboxes ("Forward",
"Backward"). Guard against both-off (fall back to forward, or disable Deploy with
a hint).

**Run path.** `commitDeployment` (`2019`) currently derives one `direction`
(`2021`), validates once (`2072`), POSTs once (`2085–2091`), and draws one line
set (`2103`). Generalise to **loop over the selected directions**: for each of
`["forward","backward"]` that is on, run `validatePlacement` (`1994`) → POST
`{ seeds, horizon_h, direction }` → `drawDeployForecastLines(features, group,
runStart, deploymentId)`. Both direction line sets go into the **same**
deployment `group` and share the one set of `drawDrops` discs. `clipDeployTrack`
(`2238`) already keys growth direction off each feature's `props.direction`
(`2375`), so forward (release→clock) and backward (clock→release) tracks clip
correctly side by side. Both are the same green (`DEPLOY_COLOR`); they emanate
from the shared drops in opposite time senses — no colour split needed.

**BLOCKING fix — the track key must include direction.**
`drawDeployForecastLines` registers each track in `deployTracks` keyed by
`` `${deploymentId}#${props.index}` `` (built `2373`, written `2402`). Running
forward and backward under one `deploymentId` yields identical keys (both index
0..N-1), so the second direction **overwrites** the first — the orphaned lines
stay drawn but drop out of `updateClock`'s clip loop (`390–391`) and selection
(`2272–2273`). Change the key to include direction, e.g.
`` `${deploymentId}#${props.direction}#${props.index}` ``. `forgetDeployment`'s
`key.startsWith(`${id}#`)` (`2146`) still matches, so wholesale cleanup is
unaffected. (At-time markers keyed `deploy:${id}` `2409–2416` coexist fine —
two heads overlap at each drop at t=release; acceptable.)

**Bookkeeping.** The manager row (`deployments[deploymentId]`, `2048–2057`)
holds a single `direction`; store the set instead (or the two booleans) and show
a "⇄ both" arrow when both ran (`1786` builds the arrow glyph). The seed/limit
pre-flight runs per direction; if one direction violates limits, still place
drops + run the other. CSV export is geometry-only (drops), so it is unaffected.

This is the heaviest item in 040 (the run path becomes a small loop); if it
grows, it can split into its own plan, but the change is contained to
`commitDeployment` + the two-toggle UI.

---

## Sequencing & verification

Order within the batch: #36 → #26 → #28 → #32 (cheap-to-meaty). One commit is
fine, or split #32 out.

Verify in the served app (pixi build + serve, frontend + API):
- **#36:** no "Now" chip; the now dot is present but not pulsing.
- **#26:** Deploy tab opens with spacing `10` and Timing on **Instantaneous**
  (ship-speed input greyed).
- **#28:** on first load, ship + glider tracks are visible immediately and
  drifter tracks appear a moment later (when the fetch lands) — the map never
  blocks waiting on them; toggling "Show tracks" off/on still works.
- **#32:** check both Forward and Backward, place a deployment, and confirm two
  green tracks (up- and down-time) grow from the same drops as the scrubber
  moves; the manager row shows both directions.
