# Track z-order + zoom-scaled tracks, and a data-browser link

> **Implemented.** Selection z-order and zoom-scaled line/dot sizing are
> documented in [docs/trajectories.md](../../docs/trajectories.md) (*Selection*
> and *Rendering and stacking order*). The data-browser header link is a
> self-contained `site/map/` change.


Covers issues #11 (highlighted-track z-order + zoom-dependent line/dot sizing)
and #13 (link to the `/data` browser from the map). Both are frontend-only
edits under `site/map/` — the map is a static Leaflet app, not a build artifact.

## #11 — highlighted track in front of other tracks

All drifter/glider track lines and per-fix dots live in Leaflet's default
`overlayPane` (one shared SVG renderer), while the latest-position heads sit in
higher panes (`drifters` 650, `ship` 660). So a selected track already renders
*below the marker heads* — correct — but *among* the other tracks, where a
neighbouring track can paint over it. Currently selection only recolours
(`lineStyle`/`dotStyle`), leaving draw order untouched.

Fix: on selection, bring the selected line + its dots to the front of their
shared renderer with `bringToFront()` — exactly the pattern the deploy-highlight
already uses (`renderDeploySelection`). This lifts the selected track above every
other track but leaves it below the head/ship panes, matching the issue: *in
front of all other tracks, not in front of the markers*.

Route both the drifter and glider restylers through shared `restyleLine` /
`restyleDot` helpers so the front-raising is defined once and both element kinds
inherit it. Re-applied on every `applySelection` pass (select + zoom).

## #11 (follow-up comment) — thinner lines, zoom-gated dots

At coarse zoom the individual per-fix dots and heavy lines blur the tracks
together; at the finest zooms we want the dots visible. Make line weight and dot
radius a function of the current zoom (maxZoom = 12):

- **Line weight**: 1 at coarse zoom, stepping up to 2 zoomed in; the selected
  track stays a fixed increment thicker so it still reads as picked.
- **Dot radius**: hidden (radius 0) below the finest three zoom levels
  (zoom < 10), then shown at 10–12.

`lineStyle`/`dotStyle` read a module-level `trackZoom`, updated on `zoomend`,
which then re-runs `applySelection` to restyle every registered part. Heads and
ship track are unaffected (heads carry no zoom rule; the ship isn't registered).

## #13 — data-browser link on the map

`site/data/index.html` is the generated dataset browser; the map is one level
down at `site/map/`. Add a right-aligned header link (`../data/`) beside the
imprint link. Group both into a `.header-links` nav so the header stays a clean
flex row and the right cluster is extensible.
