# 043 — Default continent shading (land/sea basemap)

**Status: mostly built.** `to_landmask_webp` (`_currents.py:391-415`), the
`build.py` wiring (`:322-324`), and the gated frontend overlay (`app.js:2336`,
`:2455-2467`) all exist and match this plan's shape. What's still open is a
correctness trade-off around which time slice(s) the mask is baked from — see
"Open question" below. Keep this plan open until that's resolved.

**#29** — a default basemap that shows the continent: no OSM, "just a highly
compressed gray (land) / blue (sea) mask at CMEMS resolution." Build-pipeline
(one static asset) + a frontend layer.

## Why this shape

The map has **no basemap tiles** on purpose (VSAT: a slippy basemap is a heavy
repeated transfer). Today, where the CMEMS current field doesn't cover, the map
is just a flat CSS sea-tone (`#map { background: #dfe7ee }`, `style.css:92–97`),
so the coastline is invisible unless a shading is on. #29 wants a permanent,
tiny, self-hosted land/sea raster underneath everything — land context at zero
per-pan cost.

The land/sea mask is **already implicit** in the CMEMS field: land cells are
`NaN` (`_currents.py:417`, `rgba[np.isnan(warped), 3] = 0`). So we can bake a
one-off mask from the same grid the shadings use — perfect co-registration, no
new data source, time-invariant (one file, not per-frame).

## Build side (Python)

Reuse the existing raster path. The shadings go
`field → _raster.mercator_rgba_webp(field, lats, lons, to_rgba)` (`_raster.py:92`,
via `_warp_north_up` which sets `bounds` to the outer cell edges,
`_raster.py:64–66`), on the grid `BBOX` + `DATASET_ID`
(`_currents.py:47,62` — 1/12°, the Agulhas box).

Shipped as `to_landmask_webp(window)` in `_currents.py:391-415` (colours defined
just above it, `LANDMASK_LAND_RGB`/`LANDMASK_SEA_RGB`, `:387-388`):
- takes the CMEMS window already fetched for the shadings, reads a **single**
  time slice (`window.isel(time=0, drop=True)`, `:400`) and its `uo` NaN
  pattern as the land mask,
- `to_rgba(field)`: `NaN` (land) → opaque `#c9c9c4`; valid (sea) → flat
  `#dfe7ee` (matching the CSS fallback tone); full alpha everywhere,
- runs through `_raster.mercator_rgba_webp` so it co-registers **exactly** with
  the shadings' `meta.bounds` (same grid → same `_warp_north_up` edge bounds),
- returns `(webp_bytes, bounds)`; `build.py:322-324` writes `landmask.webp`
  into `map_dir` via `atomic_write_bytes` and records **just its filename** in
  `currents_meta.json` as `meta["landmask"] = "landmask.webp"` — no separate
  `landmask_meta.json`, since the frontend reuses `meta.bounds`.

It piggybacks the window already fetched by `fetch_shading_window` — no extra
CMEMS egress — and runs in the same slow (CMEMS) tier as the other rasters, per
the plan's "simplest" option above.

### Open question: single time slice vs. NaN-intersection across frames

This plan's original text called for picking "a known-complete frame (or
intersect the NaN pattern across frames) so transient missing-data NaNs aren't
baked as spurious land." An earlier revision did intersect
(`np.all(np.isnan(uo), axis=0)` across the whole window), but that was removed
by plan 045's memory fix, leaving the current single-slice read
(`window.isel(time=0, drop=True)`, `_currents.py:400`). The docstring now
argues the single slice is fine because the CMEMS grid's land geometry is
time-invariant (no tidal-flat/intertidal cells that flip NaN↔finite between
steps), and `tests/test_shading_frames.py::test_landmask_is_single_slice_equivalent`
(`:289-297`) locks in that a full-window intersection and the single first-step
slice produce byte-identical output on the test fixture — i.e. the test proves
equivalence given its own synthetic land pattern, not against the real CMEMS
product.

Whether that time-invariance assumption holds for the live Copernicus product
(vs. an intersection being cheap insurance against a frame with spurious/
transient NaNs at `time=0` — e.g. a partially-failed fetch step) is not settled
here. Two ways to close this:
- accept the single-slice read as-is (cheaper, already shipped, test-locked) —
  in which case strike the "or intersect" alternative from this plan and record
  the time-invariance argument as the rationale, or
- restore a cheap completeness check (e.g. intersect NaN across the fetched
  window, or pick a step known not to be the first/potentially-partial one)
  if a transient-NaN failure mode is judged real.

## Frontend (`app.js`)

Shipped as specced:

1. A `basemap` pane sits below `shading` (`app.js:2336`,
   `map.createPane("basemap").style.zIndex = 300`, vs. `shading` at 350).
2. The overlay is gated, not unconditional (`app.js:2461-2466`): when
   `meta?.landmask && meta?.bounds`, `L.imageOverlay(frameUrl(meta.landmask),
   meta.bounds, { pane: "basemap", className: "crisp-raster" }).addTo(map)` —
   a permanent base, not one of the `currentShading` radio entries
   (`app.js:2856` builds those, and `landmask` is not among them). On a
   static-only / no-CMEMS deploy `meta` is null, so the overlay is skipped.
3. The CSS `#map` background (`style.css:92-97`, `#dfe7ee`) stays as the
   pre-load / 404 / no-field fallback, colour-matched to `LANDMASK_SEA_RGB` so
   there's no flash before the WebP loads.

The shading radios (speed / ζ·f / **None**) live above the basemap pane: pick a
shading and it paints over the land/sea base; pick **None** and the gray-land/
blue-sea base shows through — the #29 default view. Whether the map should
*open* on None (continent-first) rather than speed was flagged as a small,
undecided default choice; it has not been revisited since.

## Verify

Served app: on first load (or with shading = None) the continent renders as a
crisp gray landmass on a blue sea, aligned exactly with the coastline where the
speed shading stops; panning is instant (no tile fetches); selecting a shading
overlays it cleanly; the `landmask.webp` transfer is a few kB. This much is
built and works. What remains open is the single-slice-vs-intersection
question above, and that `docs/currents.md` has no mention of the landmask at
all — it should get one once the open question is settled.
