# 041 — Track visual overhaul: deployment dot + moving head + dashed forecast gap

Two coupled rendering issues that redefine how a track reads on the map:
**#33** (a fixed deployment dot + a moving head on every instrument and virtual
track) and **#34** (a dashed bridge closing the reporting-lag gap between an
observed track and its forecast). Both are `site/map/app.js` only.

**#33 and #34 have shipped** (deployment dots, clock-gated; the dashed
bridge/solid-forecast split with a shared clock and a now-ghost handoff
marker). Two things from the original spec converged differently on the way
in, and one is genuinely still open — see the status note in each section
below and the updated verification list at the end.

**#35 (per-class instrument colours) landed independently**, as
`BATCH_STYLES`/`styleForBatch` (drifters), `gliderStyle` (glider/float/xspar/
waveglider) and `deployColor` (virtual deployments) — each already returning
that instrument's identity colour. What did **not** land is the shared
**`identityColor(kind, key)` seam** this plan asked for to front all three:
colour is inlined per call site instead (see "The identity-colour seam"
below). That is the one open item this plan still tracks.

---

## Current state (what we're changing)

- Observed drifter/glider **track lines** are one shared orange
  `TRACK_COLOR = "#e07b39"` (`addTrackSegments`, `736`). Per-instrument identity
  lives on the **head**, not the line: drifter batch fill
  `styleForBatch(batch).fillColor` (`98–114`), glider `gliderStyle(type).color`
  (`2942`).
- **Moving heads** already exist per instrument (one per track, walked by the
  clock via `clipTrack` `569` / `clipForecast` `629`): drifter `L.circleMarker`
  r6 (pane `drifters`), glider divIcon diamond (default markerPane), ship
  `shipIcon` (pane `ship`).
- **Virtual deployments** already have a fixed **drop disc** (`drawDrops` `1495`:
  `L.circleMarker` r4 = `DEPLOY_DROP_RADIUS`, **white** stroke, green
  `DEPLOY_COLOR` fill, pane `deployDrops` z440) **and** a moving **at-time head**
  (`registerAtTimeMarker` `417`, r5 white-ring, pane `atTime` z670).
- **Real instrument tracks have no fixed deployment dot** — only the moving head.
  Their track is already truncated at deployment/detachment (plan 010, "True
  track" = free drift), so the track's **first vertex is the deployment point**.

So the two track families are asymmetric: virtual has {fixed dot + moving head},
real instruments have {moving head only}. #33 makes both symmetric.

---

## #33 — One deployment dot + one moving head on every track (not ships)

### The identity-colour seam — still open
The plan asked for a single accessor, e.g. `identityColor(kind, key)`:
- drifter batch → `styleForBatch(batch).fillColor`
- glider/float/xspar/waveglider type → `gliderStyle(type).color`
- virtual deployment → `DEPLOY_COLOR`

**This was never built.** Colour is inlined at each of the three call sites
instead: `buildTrackGroups` uses `styleForBatch(batch).fillColor` directly
(`app.js`, `src.color`), `buildGliderTrackGroups` uses `gliderStyle(type).color`
directly (`app.js`), and `drawDrops` uses `deployColor(deploymentId)` directly
(`features/deploy.js`). Each already returns the right per-class colour (#35
landed as three independent lookups, not a shared palette), so nothing reads
wrong today — but the seam that was meant to be "the one place #35 swaps in
the palette, and where the observed line will source its colour from too" does
not exist, so there's no single point to extend if the line ever adopts the
identity colour. Remaining choice: extract the seam now (three call sites →
one accessor), or explicitly drop the requirement and accept per-call-site
colour lookups as the shape going forward.

### The deployment dot (identity colour, no outline; radius converged to a fixed constant)
A static `L.circleMarker`:
- **Radius shipped as the existing `DEPLOY_DROP_RADIUS` constant (3.0), not a
  computed "4× line width".** Track weight (`trackWeight`) turned out to be
  zoom-dependent (steps between 1 and 2, plus a selected-state bump) rather
  than the fixed base-2 this plan assumed, so a "4× line width" radius has no
  single fixed target to begin with. Reusing `DEPLOY_DROP_RADIUS` instead gives
  real-instrument dots and virtual-deployment drops the exact same, constant
  size for free (they're now one visual family, per #33's goal) without
  introducing a second, zoom-coupled sizing rule. This was a deliberate
  convergence, not an oversight — the plan's requirement here is superseded by
  the shipped constant.
- `fillColor:` the instrument's identity colour (inlined per call site — see
  "The identity-colour seam" above), `fillOpacity: 1`, **`weight: 0`** (no
  outline).
- `interactive: false` — it sits on the track start; non-interactive lets hover
  fall through to the track canvas below (overlayPane z400), so mid-line track
  tooltips/clicks (plan 039) keep working.
- Pane: reuse `deployDrops` (z440) — it's already the "deployment markers" layer,
  above the track canvas and below the heads. ~one dot per instrument track
  (~150 total) as discrete SVG circleMarkers is cheap (nothing like the 100k
  track segments that forced the canvas switch).

**Real instruments (new):** place one deployment dot at the track's **first
coordinate**, coloured by the instrument's identity colour. It never moves.
`addTrackSegments` (`730`) doesn't know batch/type, so add the dot in the
callers `buildTrackGroups` / `buildGliderTrackGroups` (which know the
identity) — this is what shipped, each caller passing its own already-resolved
colour rather than calling through a shared `identityColor` (which, per the
status note above, was never built).

- **Group placement (revised after review):** add the dot to the instrument's
  **marker group** (`markerGroups[batch]`), **not** the track group. The track
  group (`tracksOverlay.groups[batch]`) is gated by `batchOn && tracksMasterOn`
  (`868`) — i.e. also by the "Show tracks" master — so a dot there would vanish
  when tracks are off, whereas the **virtual** drop discs (in the deployment's
  own group) ignore the master. Putting the real dot in the marker group makes it
  gated by the instrument row only, matching the virtual drops — the symmetry #33
  is after. ("Show tracks" then hides the line but keeps the deployment point.)
- Single-fix instruments (e.g. D-509) produce zero segments in `addTrackSegments`
  (`733`, `i < pts.length−1`), so they naturally get no dot — acceptable, their
  latest-position marker already sits at the deployment point.

**Virtual deployments (restyle):** change `drawDrops` (`1499–1501`) and the
**unselected** branch of `restyleDropDisc` (`2207–2211`) to the new spec — drop
the white stroke (`color:"#fff", weight:1` → `weight:0`), keep the green fill and
radius. **Preserve `restyleDropDisc`'s selected branch** (`color:"#111827",
weight:2`) — that outline is the selection affordance. The disc already sits at
the deployment position and never moves, so only its unselected style changes.

**Ships:** unchanged — no deployment dot ("not the ships!"). They keep their
moving `shipIcon` head only.

### The moving head (spec: every track has one)
Real instruments and ships already have moving heads; virtual deployments have
the at-time head. So after #33, **both families have {fixed dot + moving head}**
— the "marrying" is structural. #33 respecs only the *deployment dot*, not the
head, so leave the head objects as they are (drifter circle, glider diamond,
ship disc, virtual at-time dot). Just confirm every track kind registers a
clock-driven head (single-fix instruments already do via `updatePointHeads`
`519`).

---

## #34 — Dashed bridge from observed-end to now

**Cause.** Reporting lag: a drifter's last transmitted fix predates "now", so its
observed track ends before the forecast, which is **seeded at the last fix**
(`kickDrifterForecasts` `2492`, `start = date_UTC`) and advected forward.
`drawDrifterForecastLines` currently **drops every advected vertex with
`t < nowMs`** (`2444`) — discarding exactly the last-fix→now segment. #34 renders
that discarded segment as a dashed line instead of throwing it away.

**Approach — split the forecast into a dashed bridge + a solid forecast, both
clock-clipped.** In `drawDrifterForecastLines`, instead of keeping only
`t ≥ now`, partition the advected vertices at `nowMs`:
- **Bridge**: vertices with `t ≤ now` (last-fix → now), rendered **dashed**
  (`dashArray: "6 4"`), pane `driftForecast`, non-interactive. Include the
  first `t ≥ now` vertex so it visually meets the forecast start.
- **Forecast**: vertices with `t ≥ now`, solid, as today.
- **Colour: shipped as the drifter's identity colour (`styleForBatch(seed.batch)
  .fillColor`) for both segments, not a separate `VIOLET_FORECAST_COLOR`.** The
  planned distinct "modeled recent past" colour was dropped in favour of one
  colour source for the whole trail — bridge and solid forecast differ only by
  `dashArray`, matching how the dot and head are also identity-coloured. This
  is a better fit for the plan's own stated end-state ("dot = line colour =
  head colour"): the forecast already sources from identity colour today, so
  there is nothing left for #35 to converge here. The `driftForecast` pane and
  code comments still say "violet" in a few places — that naming is now
  vestigial (the rendered colour is per-batch, never actually violet) and can
  be reworded whenever those lines are next touched.

Both are driven by the drifter's existing head and the **same clock entry**, so
the bridge obeys the clock-clip invariant (plan 035: nothing shows ahead of the
scrubber). Extend `clipForecast` (`629`) so the forecast entry now owns two
polylines and clips each against the clock: show the dashed bridge up to
`min(clock, now)` and the solid forecast from `now` up to `clock`; the head walks
the combined path. This is a contained change: the **only** consumers of
`forecastClockEntries` / `entry.line` are `clipForecast` and `updateClock`
(`394`) — there is no restyle / `bringToFront` / selection machinery on these
lines — so a two-line entry ripples nowhere else.

Two things to get right (flagged by review):
- **`entry.shown`** is a single membership bool (`634/659/660/662`). With two
  lines, either add/remove both together under one flag or give each its own —
  don't let bridge and forecast group-membership desync.
- **Head-handoff is a behavioural change, not just a render one.** Keeping the
  pre-now vertices moves `times[0]` (the entry's gate at `633`) from ≈now back to
  the **last-fix** time. Because `clipForecast` runs last and drives the head
  whenever `ms > start`, the drifter head will now **walk the modeled bridge**
  between last-fix and now instead of parking at the last real fix (today's
  `clipTrack.latest()` behaviour). At the default clock (≈now) the marker sits at
  the modeled now-position, not the last transmitted fix. This is what #34
  intends ("keep the marker walking"), but state it. Consider a tooltip that
  reads "recent (modeled)" on the bridge vs. "forecast" (`2460`) ahead of now.

Gating is unchanged: only while "Show tracks" is on and the batch is visible
(`forecastBatchVisible`). Scope is the real deployed drifters (the only
instruments with a forecast today); gliders/floats/xspar/waveglider have none, so
no bridge for them.

---

## Sequencing & verification

#33 first (the dot/head system), then #34 (the bridge). One plan, likely one or
two commits.

**Status: both #33 and #34 have shipped and are verifiable in the served app
today** (pixi frontend + API):
- **#33:** every drifter/glider/virtual track shows a small filled dot (no
  outline, fixed `DEPLOY_DROP_RADIUS`) at its deployment point in that
  instrument's identity colour, clock-gated to appear once actually deployed;
  ships have none; mid-track hover tooltips and click-highlight still work (the
  dot is non-interactive); the virtual drop discs lost their white ring.
- **#34:** a deployed drifter shows a **dashed** segment (identity colour) from
  its last real fix to "now", meeting the **solid** forecast in the same
  colour; scrubbing the clock walks the head along dashed-then-solid and never
  draws ahead of the clock; a small "now-ghost" dot marks the observed↔forecast
  handoff point.

**Still open — not yet done, don't check this off:** the shared
`identityColor(kind, key)` accessor was never built; colour is inlined at each
of the three call sites (`buildTrackGroups`, `buildGliderTrackGroups`,
`drawDrops`). Either extract the seam or explicitly retire the requirement —
see "The identity-colour seam" above. (`VIOLET_FORECAST_COLOR` and the
"4× line width" dot-radius spec are *not* open items — both converged to a
better shipped design, described in place above.)
