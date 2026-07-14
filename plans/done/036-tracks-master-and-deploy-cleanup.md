# 036 — one "show tracks" master, deploy-tool cleanup, drift-line simplification

*Implemented — see [docs/controls.md](../../docs/controls.md) for the current state.*
Mid-review revisions folded in: direction/timing are **sliding two-state switches**
(labels flanking the knob, no caption — not radios or plain toggles); the scrubber's
type-in **jump box is removed**; the **virtual-deployment drift lines are always shown
in full** (the master governs only observed tracks; the moving at-time head conveys the
clock); the drifter true tracks load **eagerly** so every head follows the clock
(fixing single-fix heads like D-509 for real); the Deploy tab's **"Settings" caption is
dropped**; and the **Current-flow animation defaults off**.

Bug fixes + control cleanup on the deployment-focused frontend (plans 034/035).
Frontend only touches `site/map/{app.js,index.html,style.css}`; the Python build
half of the forecast/hindcast removal is a sibling change (see below).

## Bugs

1. **Single-fix instrument head shows at all times (e.g. D-509).** A drifter with
   one fix has no track `LineString`, so `registerTrackClock` never runs and its
   head marker is never clock-clipped — it sits at its latest position regardless
   of the scrubber, while multi-fix heads hide before their first fix. Fix: register
   a *point clock* for every latest marker keyed on its own `date_UTC`; when the
   tracks master is on, a head with no track-clip entry hides before its (only) fix
   and shows at/after it. Multi-fix heads keep being driven by `clipTrack` (their
   `headKey` is in a `trackedHeadKeys` set, so the point clock skips them).
2. **Scrubber tick labels overflow the box.** The `.ts-tick-label` spans at the
   span ends extend past the control's rounded box. Clip/contain them.

## Remarks (behaviour changes)

- **A. Tracks master governs every track line.** The one master hides/shows drifter
  tracks, glider tracks, **ship** tracks, and the **virtual deployment** drift
  lines together. Heads, drops, and at-time markers are *not* tracks and stay.
- **B. Remove drifter forecast/hindcast** controls, drawing, and build. Frontend:
  drop the Forecast/Hindcast overlays, `buildAdvectionGroups`, `FORECAST_COLOR`/
  `HINDCAST_COLOR`, `renderDriftInfo`, the sidebar drift panel, and the
  `forecast`/`hindcast` fetches + `DATA` entries. Build half: separate agent
  removes `forecast.geojson`/`hindcast.geojson` generation and dead `_forecast`
  builders.
- **C. Rename "True track" → "Show tracks".**
- **D. Move the "Show tracks" checkbox into the scrubber box** (the app-clock
  control), since tracks now clip to that clock. The Instruments tab keeps only the
  marker rows (its overlay-master section goes away).
- **E. Crop the Marion Dufresne track at the start of the data period (28 Jun).**
  Fetch/show MD fixes from `2026-06-28` rather than `2026-06-24`.
- **F. Direction and Timing are radio groups**, not toggle buttons. Direction:
  Forward / Backward. Timing: Along track / Instantaneous.
- **G. CSV import + "Download all CSV" behind a collapsible `⌄` menu** in the Deploy
  tab, so the common path (Settings + Click-to-place + manager) stays uncluttered.
- **H. Remove the "Compute drift" checkbox** — always compute drift. Drop
  `state.computeDrift` and its off-path in `commitDeployment`.
- **I. Drift-line simplification.** One green line style: no analysed/forecast
  solid-vs-dashed split, no faint "ahead of the clock" segment (draw only the trail
  up to the clock), and no dashed grey vessel route between the drops
  (`drawShipTrack` goes). The multi-row "Drift-line legend" collapses away — nothing
  left to disambiguate.

## Frontend design notes

- Module-scope `let tracksOn = false;` read by `clipDeployTrack` (empty line when
  off) and the point-head logic. `setTracksVisible(on)` in `main`: flips `tracksOn`,
  lazily loads + shows/hides drifter+glider track groups (still composed with the
  per-batch rows), toggles each ship's track layer, and re-clips every deploy track.
- Ship: `makeShipLayer` exposes `setTrackShown(on)` (add/remove the `track`
  layerGroup from the vessel group; the marker/head is unaffected). Ships register
  into a `shipLayers` list so the master can reach them; new ships adopt the current
  `tracksOn` at creation.
- Deploy track entry collapses from `{trail[2], ahead[2]}` to a single `line` (+ the
  transparent hit-line for click-to-highlight). `splitTrackAtEdge` and the
  `analysis_edge` plumbing in the drawing path are removed.

## Steps

1. Python build removal (sibling agent).
2. `app.js`: tracks master + point-head fix, deploy-tool radios / CSV menu /
   compute-drift removal, drift-line simplification, forecast/hindcast removal.
3. `index.html`: drop the drift sidebar panel.
4. `style.css`: tick-label clipping, radio rows, collapsible CSV menu, scrubber
   checkbox.
5. `docs/controls.md` + `docs/forecast.md`/`deploy.md` updated to the new state.
6. Review agent, then verify by serving locally.
