# 026 — Controls dock + tidy sidebar

> Implemented. See [docs/controls.md](../../docs/controls.md) for the current state.

## Problem

On a 13" laptop (≈1280×800, worse at 1280×720) the four top-right map controls
— **Instruments**, **Currents**, **Ships**, **Deploy PoC** — stack vertically to
~800 px, more than the ~706 px of map height left after the WIP banner (~30 px)
and header (64 px). The lowest box overflows the map bottom and lands in the
bottom-centre time slider's band (both at z-index 1000). Horizontally, four
opaque boxes crowd the right gutter next to the fixed 260 px sidebar, squeezing
the map from both sides.

The sidebar compounds it: it mixes always-glance data (freshness), occasional
lookups (ship readouts, awaiting), contextual legends (speed, ζ/f — useful only
while that shading is on), and read-once explainers (drift note) — all pinned
open at once.

Chosen direction: **Solution 01 — Consolidated dock + tidy sidebar** (of three
mocked up; see the interface-review artifact). Nearest to the current code, kills
the overflow and the slider collision, keeps the left/right layout the user knows,
minimal new responsive behaviour.

## Approach

### A. One tabbed control dock (replaces four float boxes)

A single `L.control({position:"topright"})` — the **dock** — with a tab strip and
one visible tab body at a time, so the top-right footprint is bounded by the
tallest single tab (~390 px), never the sum. Tabs:

- **Layers** (default) — every layer toggle in one list:
  - instrument overlay master rows (True track / Forecast / Hindcast),
  - instrument batch + glider rows,
  - a divider, then the **Currents** shading radios (None / Current speed /
    Vorticity ζ/f) and the flow / near-inertial overlay checkboxes.
- **Ships** — one checkbox row per vessel; a muted "No vessel fixes yet"
  placeholder until the first fix arrives.
- **Deploy** — the existing PoC tool body (toggle, knob rows, Clear, Download
  CSV, hint, legend, status).

All three bodies are built once and shown/hidden via `display`, so the deploy
tool keeps its state and the ships list can be appended to live.

### Refactor to make this clean (no DOM-moving hacks)

The three current builders each wrap their own `L.control`; split the body-build
out so the dock owns the container:

- `buildBatchControl` → **`buildInstrumentRows(div, map, markerGroups, overlays)`**
  — appends the overlay + batch rows to `div`. Returns nothing; `sync()` runs at
  the end as today.
- `titledLayerControl` (used for Currents *and* Ships, via `L.control.layers`) is
  **removed**. Replace with custom rows built the same way as the instrument rows:
  - **`buildShadingRows(div, map, shadings, overlays, onShadingChange)`** — the
    shadings are mutually-exclusive **radios** (selecting one adds its layer and
    removes the others, including "None"); the overlays are independent
    **checkboxes**. `onShadingChange(name)` fires on every radio change (drives
    contextual legends + the ζ/f prefetch, below).
  - **`buildShipsTab(map)`** → `{ render(div), addVessel(group, name) }`.
    `render` paints the placeholder or the current vessel rows; `addVessel`
    appends a checked row (adding the group to the map) and re-renders.
- `buildDeployTool(deployLayer)` → return `{ state, handleClick, handleDblClick,
  handleMove, handleAbort, renderBody(div, map) }` instead of `{ control, ... }`.
  `renderBody` appends the existing body to `div`; the map handlers in `main()`
  are unchanged.

Then **`buildControlDock(map, tabs)`** where `tabs = [{ id, label, render(div) }]`.

`main()` wiring:
- build `deployTool`, `markerGroups`, overlay list, `currentShading`/`currentOverlays`
  as today;
- `const shipsTab = buildShipsTab(map);`
- build the dock with the three tabs and `addTo(map)`;
- ship/agulhas fixes call `shipsTab.addVessel(group, name)` in place of
  `ensureShipsControl().addOverlay(...)`.

`baselayerchange` is a `L.control.layers` event; since that control is gone, the
ζ/f-frame prefetch moves into the `onShadingChange` callback (first time
"Vorticity ζ/f" is picked). Lift `prefetch` / `vortPrefetched` to `main()` scope.

### B. Tidy sidebar

- Sidebar `<section>`s → native `<details>`/`<summary>` (summary styled as the old
  `h2`, with a disclosure caret). Open by default: Data freshness, both ship
  panels, Surface currents. Collapsed by default: Relative vorticity, Drift
  forecast/hindcast (read-once), Awaiting first fix. Inner element ids
  (`#md-ship-readout`, `#speed-legend`, `#awaiting-list`, …) are preserved so the
  renderers are untouched.
- **Contextual legends**: `#speed-legend` shows only while "Current speed" is the
  active shading; `#vorticity-legend` only while "Vorticity ζ/f" is. Toggled by a
  `.legend-hidden { display:none }` class from the `onShadingChange` callback.
  Speed is the default shading, so its legend shows on load; ζ/f's is hidden until
  picked.

## Out of scope

Icon-rail / slide-over (Solution 02) and the single unified panel (03). No change
to the time slider, map layers, data pipeline, or the `max-width:720px` phone
stacking (the dock keeps working there — one bounded box).

## Acceptance

On 1280×800 and 1280×720: the top-right shows one dock, one tab open, no vertical
overflow, no slider overlap. Every control reachable via a tab. Sidebar sections
collapse; speed legend visible by default, ζ/f legend appears only when selected,
both hide under "None". Deploy tool still places drops and exports CSV. Ships tab
fills on first fix.
