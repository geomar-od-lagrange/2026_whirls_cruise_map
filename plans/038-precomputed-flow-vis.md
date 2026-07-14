# 038 — Pre-computed flow visualisation (fluent scrubbing)

## Problem

The current-flow overlay ships one `currents_<t>Z.json` u/v grid per frame and renders
it **client-side** with leaflet-velocity's particle animator (Windy.js). That render is
the expensive, stalling path: every frame swap re-seeds the animator, which must run a
pass to lay trails, blocking the main thread and stuttering time-scrubbing. The static
freeze added in plan 037 still had to run (and briefly hide) that pass. Meanwhile the
speed / vorticity shadings are already **pre-rendered at build time** to per-frame WebP
images and swapped as `L.imageOverlay`s on scrub — instant, projection-correct, no
client compute.

## Decision

Make the flow overlay mirror the shading exactly: **pre-render a static streamline
snapshot per frame at build time** as a Mercator-warped WebP, and have the client swap
it as a plain `imageOverlay`. Scrubbing becomes a bare image swap — fluent, no client
particle animation. Validated in `tmp_flowvis/` (matplotlib `streamplot` on the warped
u/v grid renders clean eddies/fronts; land is naturally blank; bounds co-register with
the speed raster).

Trade-off: the flow is now a **still** streamline field, not moving particles. Animation
was already off by default (plan 037) and the goal is fluent scrubbing, so this is the
intended end state. The leaflet-velocity dependency is removed entirely.

## Build side (`src/whirls_cruise_map`)

- `_raster.mercator_streamlines_webp(uo, vo, lats, lons, …)` — new sibling of
  `mercator_rgba_webp`: warp both components to Mercator-even-y (reusing
  `_warp_north_up`), run `streamplot` on the regular warped grid (so the lines are
  already Mercator-registered), knock the alpha down so the shading reads through, and
  encode a lossless WebP. Returns `(webp_bytes, bounds)`; the bounds equal the speed
  raster's, so the client reuses `meta.bounds`. Guards the all-zero/land case.
- `_currents.to_flowvis_frames(window, frame_times)` — sibling of `to_speed_frames`:
  one `flowvis_<t>Z.webp` per frame, `{valid_time, file, image}`.
- New frame kind `flowvis` (WebP): add it to `_FRAME_FILE_RE` / `_STALE_FRAME_RE`
  (keep `currents` in the stale regex so retired `currents_*.json` are pruned on the
  next build). `existing_frame_times` now requires speed + vorticity + **flowvis**.
- `build.py`: write `flowvis_<t>Z.webp` (bytes, like speed) instead of the flow JSON;
  `meta["flow_frames"] = frame_manifest("flowvis", grid, ext="webp")`.
- Remove the now-dead leaflet-velocity JSON machinery: `to_velocity_frames`,
  `to_velocity_json`, `_scale_for_animation`, `_component`, `_component_header`,
  `VELOCITY_GAMMA`, `COARSEN_STRIDE`. Update `_inertial.py`'s docstring cross-references
  (it names these for comparison but never imports them).

## Client side (`site/map`)

- Replace the leaflet-velocity flow block with a `flow` pane (z above `shading`, below
  the markers) holding an `L.imageOverlay(flow_frames[nowIdx].file, meta.bounds)`,
  registered in `currentOverlays["Current flow"]`.
- `scrubFlow(i)` becomes `flowLayer.setUrl(frameUrl(flow_frames[i].file))` — the same
  fluent swap the speed raster uses. Delete `loadFlow`/`flowCache`, `freezeFlow`/
  `resumeFlow`/`showFlowCanvas`/`refreshFlow`, the movestart/moveend re-seed, the
  `layeradd` re-seed, and the flow entry in `overlayAnimators`.
- "Animate overlays" now governs only the near-inertial canvas (the sole remaining
  animated overlay). Keep the toggle.
- Remove the leaflet-velocity vendor include from `index.html` and delete
  `site/map/vendor/leaflet-velocity-1.7.0/`.

## Tests / docs

- `test_shading_frames.py`: drop the `to_velocity_frames` import + JSON-flow test; add a
  `to_flowvis_frames` test (valid WebP bytes, bounds == speed bounds, frame naming).
- `docs/currents.md` + the `app.js` data-manifest header: flow is now a pre-rendered
  streamline WebP per frame, not a leaflet-velocity JSON grid.

## Follow-up (out of scope)

Streamline density/colour and an optional animated mode are tunable later; this lands
the fluent static default.
