# 042 — Zoom levels: finer max + intermediate stops

> Implemented. Zoom configuration lives in `site/map/config.js` (`FALLBACK_ZOOM`, `MAX_ZOOM`).

**#27** — the finest zoom isn't fine enough, and there's no zoom *between* two
existing levels. `site/map/app.js` only.

## Current state

- `L.map("map", { center, zoom: FALLBACK_ZOOM, maxZoom: MAX_ZOOM })`
  (`3502–3506`). `MAX_ZOOM = 12` (`39`), `FALLBACK_ZOOM = 12` (`35`).
- `minZoom`, `zoomSnap`, `zoomDelta` are all unset → Leaflet defaults
  (`minZoom 0`, `zoomSnap 1`, `zoomDelta 1`): **integer-only** zoom.
- **No tile basemap** (deliberate — the CMEMS shading covers the ocean; a slippy
  basemap is a heavy repeated VSAT transfer, `3497–3499`). So there is **no
  `maxNativeZoom`** to raise.
- The real resolution ceiling is the **CMEMS 1/12° (≈9 km) shading raster**, drawn
  one-pixel-per-cell with `image-rendering: pixelated` (`crisp-raster`,
  `style.css:242`). `MAX_ZOOM = 12` is capped so you don't zoom into empty space
  past the field's resolution (`3500–3501`).

## Change

Two edits to the `L.map` options (`3502–3506`):

1. **Intermediate stops** — add `zoomSnap: 0.5` and `zoomDelta: 0.5` (wheel and
   +/- buttons step half-levels; the map can settle between old integer levels).
   0.25 is an option if 0.5 still feels coarse; start at 0.5.
2. **Finer max** — raise `MAX_ZOOM` (`39`), e.g. 12 → 14. Caveat to keep honest:
   there is no more *detail* past the 1/12° raster — a higher max just enlarges
   the pixelated cells (`crisp-raster` upscale). 14 gives 4× linear zoom-in over
   the field pixels, useful for reading dense drops/tracks; going much beyond
   turns the shading blocky. Pick 14, confirm visually, adjust.

## Coupled spots (verify, likely no change)

- `trackWeight` (`280`) keys line weight off `MAX_ZOOM - 2` / `MAX_ZOOM - 5`
  thresholds; these compare fine against fractional `getZoom()` and against the
  new `MAX_ZOOM`, so weights still ramp sensibly — just confirm lines don't get
  too thick/thin at the new max.
- `fitBounds(..., { maxZoom: 9 })` (`3647`) caps only the *initial* fit; leave it
  (we don't want to open zoomed to the max).
- If the deployment-dot radius in [041] is defined relative to track weight,
  re-check it at the new max zoom.

## Verify

Served app: mouse-wheel and +/- give half-level steps; max zoom reaches ~14 with
the shading pixelated-but-crisp (not bilinear-smeared) and tracks/drops still
legible; initial load still fits the cruise box at ≤ 9.
