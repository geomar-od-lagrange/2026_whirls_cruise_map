# Surface-current shadings & the forecast time slider

The map shades the CMEMS surface field two ways — **current speed** (|velocity|,
cmocean `speed`) and **relative vorticity ζ/f** (see [vorticity.md](vorticity.md))
— as mutually-exclusive base rasters in the **Currents** control. Both are
**time-sliced**: a bottom-centre **time slider** scrubs the shading through the
covered span. The **flow overlay** — a pre-rendered static streamline raster — ships
one WebP per frame and scrubs in lockstep; the **near-inertial animation** follows too,
anchoring its analytic phase to the displayed field time. So every time-dependent
layer shares one clock — scrub to any time and the whole map shows that time. The two
animated overlays can be **frozen to a still snapshot** with one toggle (see *Animate
overlays* below) so time-scrubbing stays cheap.

## Absolute-time frames

Frames are named by their **absolute valid time**, not an offset from a moving *now*:
`speed_2026-07-01T00Z.webp`, `vorticity_2026-07-01T00Z.webp`,
`currents_2026-07-01T00Z.json`. The token is hour-precision UTC with no colons, so it
is a safe filename (`_currents.frame_filename` / `parse_frame_filename` are the
round-trip pair). The manifests carry the full ISO `valid_time`
(`2026-07-01T00:00:00Z`) so a client parses frame and slice times identically.

The frame set is every **`FRAME_STEP_H` (12 h)** step from `FIELD_TMIN` (floored to
00Z — the cruise start) through the **6-hourly product's forecast edge**. 12 h is a
multiple of the product's 6 h grid, so every frame time is a real step. It is a
**growing** set (~50 frames today, ~2/day), anchored at the cruise start rather than
sliding with wall-clock — so an old frame's file, once rendered, never changes name or
content.

## Incremental rendering

Re-fetching and re-rendering the whole span every slow run would grow without bound.
Instead a pure planner (`_currents.plan_render`) decides, from the frame files already
on disk plus wall-clock now, which frames still need work:

- A frame whose file already exists **and** whose `valid_time + FRAME_FINAL_MARGIN_H`
  (12 h) is at or behind now is **final** — immutable, skipped forever (the CMEMS
  "revises nothing behind the analysis edge" working assumption of
  [plans/034](../plans/done/034-deployment-focused-app.md), with a safety margin). Final
  frames are stable files, so the gateway can cache them hard.
- Every other frame in the span — **missing** (a backfill hole, or a prior partial
  run), **recent** (within the margin of now), or **forecast** (beyond now) — is
  (re)rendered this run.

A frame counts as "on disk" only when **all three** artifacts (speed + vorticity WebP,
flow JSON) exist (`_currents.existing_frame_times`), so a frame half-written by a
failed prior step re-plans rather than being mistaken for final.

The 6-hourly CMEMS fetch (`_currents.fetch_shading_window`, gaining explicit
`[t_lo, t_hi]` bounds) covers **only** the span of frames to render: the first run
backfills everything since `FIELD_TMIN` in one subset call (~100 6-hourly steps,
bounded memory); later runs fetch only the recent + forecast tail. The fetch's lower
bound (`first_pending_frame`) is computable before the fetch — it's the earliest frame
still needing render, which never depends on the forecast edge — and its upper bound is
a generous `now + FORECAST_REACH_H` that CMEMS clamps to the product's actual edge, so
the returned window's max time *is* that edge (`window_frame_edge`).

Files matching the frame patterns that are no longer in the current span are **pruned**
each run (`_currents.prune_stale_frames`) — including the **retired offset-named**
`speed_±NNh.webp` / `vorticity_±NNh.webp` / `currents_±NNh.json` from the moving-anchor
design — so stale artifacts never linger. Meta and non-frame files are left untouched.

## One frozen colour scale

A per-frame `vmax` would make a colour mean a different speed at each tick and the
single legend would lie. A per-*build* pooled percentile would fix that within a build
but **drift** as the frame history grows — and force every immutable old frame back
into build memory to stay colour-consistent. So the scale is a **frozen constant**:
`_currents.SPEED_VMAX = 1.2` m/s and the vorticity clip `_vorticity.VORT_CLIP = 0.3`
(|ζ/f|, symmetric ±clip). Both were frozen 2026-07-13 from the then-current pooled
99th / 98th-percentile scale (which rendered vmax 1.18 / clip 0.30). A colour therefore
means the same value at every time and across every build; only the displayed *time*
changes as the slider moves, and the legend renders once.

## Discrete colour classes

Both rasters snap the field to **`N_BINS = 12` flat colour classes**: the
`_currents._quantize_unit` step rounds the normalized `[0, 1]` colour-map input to a
bin midpoint *before* the cmocean lookup, so a frame carries 12 constant-colour
regions instead of a ~256-step continuous ramp — same `speed` / `curl` palettes, no
new colours. Two reasons:

- **Bytes.** Lossless WebP encodes large constant-value regions cheaply, so binning
  the field cuts each frame by a further **~60 %** — the single biggest per-frame
  lever here.
- **A quantitative legend.** Discrete classes make the map ↔ legend lookup exact: a
  pixel's colour reads as one labelled class the way an oceanographic contour chart
  does, rather than eyeballing a position on a smooth ramp. The client renders the
  legend bar as the *same* 12 hard-edged classes over the meta's `colorbar`, which
  carries exactly those 12 class colours (derived from the frozen scale), not sampled
  ramp stops.

The diverging ζ/f map keeps an **even** bin count so **zero stays a bin edge**: the
neutral midpoint `0.5` falls on the boundary between the two central classes (6
classes per rotation sense), so no single class straddles no-rotation. The cost is
deliberate banding; `N_BINS` (in `_currents`) is the one constant to raise to go back
toward a continuous ramp.

## Transport: full pixel detail, minimum bytes

The rasters keep **full pixel resolution** (no coarsening — coarser pixels lose the
mesoscale eddies that are the whole point), so the only lever is the encoding and
*when* the bytes move. Three choices keep a growing slider affordable on the cruise's
at-sea VSAT link ([data.md](data.md)):

1. **Lossless WebP, not PNG.** Each frame is a lossless WebP with a native alpha
   plane for land (`_raster.mercator_rgba_webp`) — universally supported and honouring
   `image-rendering: pixelated`, so the crisp native-grid look is unchanged. WebP with
   lossless alpha is roughly *half* an equivalent RGBA PNG's bytes at *identical*
   pixels; combined with the 12 discrete classes above, a cruise-bbox speed frame lands
   at **~27 kB** (a single RGBA PNG would be ~310 kB). Weighed and rejected: indexed
   PNG (bigger, and its fixed 256-colour palette buys nothing once the field is binned
   to 12 classes); a client-side temporal-delta codec (real complexity for less gain
   than WebP + binning already give).

2. **No re-render of final frames.** Only recent + forecast frames are (re)rendered
   each build (see *Incremental rendering*); the immutable back-catalogue is written
   once and served forever.

3. **Lazy transfer on the client.** The page loads the **now** frame only — so the
   critical-path bytes (~27 kB) are far *lighter* than a single 310 kB raster. Because
   the frame set now spans the whole cruise, the prefetch policy is a **band around
   now** (±8 frames), with any other frame fetched on demand when the slider reaches it.
   The ζ/f frames prefetch only once vorticity is first selected, so an untouched layer
   costs zero bytes.

The **flow overlay** is a lossless-WebP raster like the shadings (a static streamline
snapshot per frame — see *Artifacts*), so it rides exactly the same imageOverlay swap
and band-prefetch as the speed / ζ·f frames; a scrub is a bare `setUrl` to the next
frame, with no client-side integration.

## Artifacts

- `speed_<t>Z.webp` and `vorticity_<t>Z.webp` — one lossless WebP each per frame time
  `<t>` (colon-free absolute token, e.g. `2026-07-01T00Z`).
- `flowvis_<t>Z.webp` — one **static streamline** raster per frame time
  (`to_flowvis_frames` → `_raster.mercator_streamlines_webp`): matplotlib `streamplot`
  over the u/v field, Mercator-warped to the **same bounds** as the speed raster, dark
  semi-transparent lines so the shading reads through. The client swaps it as a plain
  imageOverlay, so the flow scrubs fluently with no particle animation.
- `currents_meta.json` / `vorticity_meta.json` — shared `bounds`, `vmax` (+ `vmin` for
  ζ/f), `units`, `colorbar` (the 12 discrete class colours the raster is binned to —
  see *Discrete colour classes*), plus:
  - `frames`: `[{valid_time, file}]` — the slider manifest, one entry per frame in span
    order, each with its own `valid_time` (no offset);
  - top-level `valid_time`: the **now-nearest** frame's time, kept for now-only readers
    (the deploy tool seeds its run start from it — see
    [deployment.md](deployment.md)).
  - `currents_meta.json` additionally carries `flow_frames`: the flow overlay's own
    `[{valid_time, file}]` manifest (same frame times as `frames`).

The renderers (`to_speed_frames` / `to_vorticity_frames` / `to_flowvis_frames`) take
the fetched window and the list of frame times to render, and return just those frames
plus the shared scale (`bounds`/`vmax`/`units`/`colorbar`); `build.py` assembles the
full-span `frames` / `flow_frames` manifests and the top-level `valid_time` and writes
the metas, so the manifest always lists the whole span even though only the tail was
rendered this run.

## Client: the app clock

The client reads each meta's `frames` manifest and computes the **now-nearest** index
itself from the entries' `valid_time`s (there is no `now_offset_h` key — the anchor is
no longer baked into the build). It builds the speed and ζ/f overlays at that frame,
then a bottom-centre **datetime scrubber** becomes the app's single **clock**.

The clock runs at **1 h granularity** over the frames' full span `[first valid_time,
last valid_time]` and opens on the now-nearest frame. Its value is the displayed field
time (`displayedFieldTime`) **exactly** — a slider drag moves it in 1 h steps. The
raster and flow layers **snap to the nearest 12 h frame** (they only re-point when that
snapped frame actually changes), while the **near-inertial animation** (which reads
`displayedFieldTime` live to anchor its phase) and the
**deploy tool's run start** consume the exact clock instant. So a scrub moves the clock
hour-by-hour while the shown field frame changes every 12 h, and every registered
shading overlay stays in lockstep (speed and ζ/f both re-point even while one is
hidden). The tracks follow the same clock: observed tracks clip to the fixes at or
before the clock with their head markers riding the clipped end (see
[trajectories.md](trajectories.md)), and the virtual drift trails re-split into a
strong traversed part and a faint remainder ([deployment.md](deployment.md)) — a
scrub moves the whole picture, not just the shading. The scrubber carries day tick
marks with sparse `Jul 14`-style UTC date labels, a live clock readout, and a
wall-clock **now** affordance: a small blue dot sits on the scrub line itself (out of
the tick lane so it can't collide with the date labels) with a slow pulsing ring so
the present reads at a glance, and — because the dot is deliberately non-interactive so
it never blocks grabbing a thumb parked near it — a small **"now" chip** beside the
clock readout carries the click. Pressing it snaps the scrubber back to the now hour
through the same input→onChange path a drag uses, so every clock-aware layer re-syncs
identically; the chip dims to a quiet outline once the thumb already sits on now. Both
appear only when now falls inside the covered span.

The prefetch policy is a **band around now**: the whole (growing) frame set is never
bulk-prefetched. On the clock's first move the client warms only the shading + flow
frames within ±8 indices of the now frame; the ζ/f frames warm the same ±8 band on
first selection of that shading. Any frame outside a warmed band loads on demand — every
layer (speed, ζ/f, and the flow overlay) is an imageOverlay whose `setUrl` fetches the
frame naturally, so a clock position whose nearest frame 404s never wedges the UI (the
overlay keeps the last loaded frame).

The scrubber is built only when the meta carries more than one frame; with CMEMS down
(no meta) there is no clock and no shading.

## Animate overlays (static-snapshot toggle)

The **near-inertial particle canvas** runs its own continuous `requestAnimationFrame`
loop that repaints every frame regardless of the clock. During a time-scrub that loop
competes with the raster/track work on the main thread and scrubbing stutters. The
**"Animate overlays"** checkbox in the Currents tab governs it. Turned **off**, the
overlay freezes to a **still snapshot** of the current frame, redrawn only on discrete
state changes (a clock scrub, a pan/zoom) — never free-running — so a scrub costs the
raster/track work alone. The default is **off**.

The near-inertial animation loop parks (schedules no `requestAnimationFrame` while
static). The still is drawn by integrating each particle forward a fixed number of steps
at the *displayed* field time — an instantaneous streamline snapshot — without mutating
the particle pool, so toggling animation back on resumes from the live positions. It
re-renders (coalesced to one raster per frame) on clock scrub and on `moveend`/`zoomend`.

The **flow overlay** is *not* part of this toggle: it is a pre-rendered static streamline
raster swapped per frame (see *Artifacts*), so it is always fluent and never animates.
