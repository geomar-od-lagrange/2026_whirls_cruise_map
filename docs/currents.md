# Surface-current shadings & the forecast time slider

The map shades the CMEMS surface field two ways — **current speed** (|velocity|,
cmocean `speed`) and **relative vorticity ζ/f** (see [vorticity.md](vorticity.md))
— as mutually-exclusive base rasters in the **Currents** control. Both are
**time-sliced**: a bottom-centre **time slider** scrubs the shading through the
CMEMS forecast at **12 h steps, −12 h … now … +72 h** (8 frames). The animated
flow trails and the near-inertial animation are *not* scrubbed — they are textures
of one instant (see [forecast.md](forecast.md)); only the two scalar rasters gain
frames.

## One window, one clock

All eight frames — and the flow-trail grid — come from a **single** CMEMS fetch:
the 6-hourly `PT6H-i` product over `[now−12 h, now+72 h]`
(`_currents.fetch_shading_window`). 12 h is a multiple of the 6 h grid, so every
slider target lands on a real step; the **now** frame (offset 0) is identical to
the single-time speed raster the map shipped before. The flow trails are the
window's **now** slice. This replaces the previous per-overlay single-time fetch,
so the whole overlay set shares one clock and there is no second download for the
slider.

The near-inertial *advection* field is a **separate** finer hourly window
(`fetch_field_window`, `PT1H-m`, ±12 h) that feeds forecast/hindcast and the
inertial decomposition — unrelated to these overlays and unchanged.

## One shared colour scale

A per-frame `vmax` would make a colour mean a different speed at each tick and the
single legend would lie. Instead **one** `vmax` backs the whole slider — the
`SPEED_CLIP_PERCENTILE` (99th) of speed pooled over *every* frame for speed, the
98th percentile of |ζ/f| (symmetric ±vmax) for vorticity. So a colour is the same
value at every time, cross-time comparison is valid, and the legend is rendered
once; only the displayed *time* changes as the slider moves. (Pooling clips the
busiest instant slightly harder than a per-frame scale would, which is the point —
the scale is stable across the run rather than breathing frame to frame.)

## Transport: full pixel detail, minimum bytes

The rasters keep **full pixel resolution** (no coarsening — coarser pixels lose the
mesoscale eddies that are the whole point), so the only lever is the encoding and
*when* the bytes move. Three choices keep an 8-frame slider affordable on the
cruise's at-sea VSAT link ([data.md](data.md)):

1. **Lossless WebP, not PNG.** Each frame is a lossless WebP with a native alpha
   plane for land (`_raster.mercator_rgba_webp`). On the cruise-bbox speed field a
   frame is **~85 kB** — versus ~150 kB for an indexed PNG and ~310 kB for the RGBA
   PNG the map shipped before — at *identical* pixels and full colour (no palette
   quantisation). WebP is universally supported and honours
   `image-rendering: pixelated`, so the crisp native-grid look is unchanged.
   Weighed and rejected: indexed PNG (bigger, and quantises the ramp); a client-side
   temporal-delta codec (a canvas reconstruction pipeline — real complexity for less
   gain than WebP already gives).

2. **No extra download in the build.** All frames slice the one window already
   fetched (above), so the slider costs the build one wider fetch, not eight.

3. **Lazy transfer on the client.** The page loads the **now** frame only — so the
   critical-path bytes (~85 kB) are actually *lighter* than the old single 310 kB
   raster. The other seven speed frames prefetch in the background once the map is
   idle; the ζ/f frames prefetch only once vorticity is first selected, so an
   untouched layer costs zero bytes. Sliding to an un-prefetched frame just fetches
   it on demand.

Net: the naïve 8× (~6 MB for both fields) becomes ~0.7 MB of speed frames + ~0.9 MB
of ζ/f frames, and only ~85 kB on the initial load.

## Artifacts

- `speed_-12h.webp … speed_+72h.webp` and `vorticity_-12h.webp …` — 8 lossless
  WebP frames each (filename label `f"{offset:+03d}h"`).
- `currents_meta.json` / `vorticity_meta.json` — shared `bounds`, `vmax`
  (+ `vmin` for ζ/f), `units`, `colorbar`, plus:
  - `frames`: `[{offset_h, valid_time, file}]` — the slider manifest, one entry per
    frame with its own `valid_time`;
  - `now_offset_h`: `0` — which frame the slider opens on;
  - top-level `valid_time`: the now frame's time, kept for now-only readers (the
    deploy tool seeds its run start from it — see [interactive_forecast.md](interactive_forecast.md)).
- `currents.json` — the flow-trail leaflet-velocity grid, from the now slice
  (unchanged shape).

## Client

`app.js` builds the speed and ζ/f overlays at their `now` frame, then a
`buildTimeSlider` control (a positioned element, not an `L.control`, so it can
centre and span the map width; Leaflet mouse propagation disabled so dragging the
handle never pans the map). Moving the slider `setUrl`s **every** registered
shading overlay to that offset's frame (so speed and ζ/f stay in lockstep even
while one is hidden), updates the sidebar displayed-time line
(`renderCurrentsInfo(meta, frame)`), and re-locks the deploy tool's start to the
displayed field. The slider is built only when the meta carries more than one
frame; with CMEMS down (no meta) there is no slider and no shading, as before.
