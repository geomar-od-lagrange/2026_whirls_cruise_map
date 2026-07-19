# 050 — Zoom performance: collapse per-segment tracks + stop markers leading
> Implemented. See [docs/trajectories.md](../../docs/trajectories.md) — one multi-part polyline per instrument on a shared canvas renderer.

## Symptom

Zooming (especially to finer resolution) feels laggy: the head markers snap to
their final positions first, then the current shadings and the tracks visibly
"follow" a beat later, with the tracks stuttering.

## Diagnosis

Two separate effects compound:

1. **Marker-leads-raster (inherent Leaflet).** Head markers are DOM/vector layers
   that Leaflet's zoom animation places at their *final* positions immediately.
   Tiles, the WebP current shadings (`L.imageOverlay`), and the track canvas are
   **raster** — during the ~250 ms zoom they are shown as a scaled bitmap of the
   pre-zoom image and only re-rasterize crisply at `zoomend`. So markers appear to
   lead and the raster "catches up".

2. **Track re-raster cost (fixable).** The observed drifter/glider tracks are built
   as **~100k individual 2-point `L.polyline`s** all sharing one canvas renderer
   (`addTrackSegments`). At every `zoomend` Leaflet re-projects and re-strokes all
   ~100k as separate paths — a heavy blocking pass, which is what makes the tracks
   *stutter* rather than just re-sharpen. (The #7 optimization only skipped the extra
   restyle sweep, not this.) The same per-path cost also makes clock-scrub and
   select-all janky.

Ship tracks (SVG, few segments) and the real-drifter forecast lines are not the
dominant cost; the observed-track canvas is.

## Fix

1. **Stop markers leading:** `markerZoomAnimation: false` on the map, so the marker
   pane no longer animates ahead of the raster — the whole scene updates together at
   `zoomend`. One option flag.

2. **Collapse per-segment → per-track:** build each observed track as **one
   (multi-part) `L.polyline` per instrument** instead of one polyline per fix-to-fix
   segment. Cuts the canvas layer count from ~100k to a few dozen, so the `zoomend`
   re-projection/re-stroke (and clock-scrub, and select-all restyle) drop ~1000×.

   Preserved behaviour:
   - **Per-leg hover tooltip** — one sticky tooltip per track whose content is
     resolved on `mousemove` to the vertex nearest the cursor (the same "hover the
     track → that fix" UX; drops ~100k tooltip objects). Nuance: at an exact leg
     boundary the nearest vertex may be the adjacent fix — a one-fix difference on
     dense tracks.
   - **Click-to-select / dim / bring-to-front** — one handler + one restyle part per
     track line.
   - **Blanked >24 h de-spike gaps** — drawn as disjoint parts of the multi-part
     polyline (`splitAtGaps`).
   - **Clock clipping** — `clipTrack` sets the line's latlngs to the vertices up to
     the clock (split at gaps), ending the in-progress part at the interpolated head;
     the head still interpolates over the timed samples exactly as before.

## Verification

No JS test suite. Static: `node --check`, `pixi run check-frontend` (tsc, 0
cross-module reference errors), `esbuild --bundle`. Then in-browser validation
(the MR needs the user's sign-off): zoom smoothness, hover shows the right fix,
click-select + dim + front-raise, clock scrub reveal, "hide outliers" gaps, the
"Show tracks" toggle.
