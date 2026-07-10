# Surface-current shadings & the forecast time slider

The map shades the CMEMS surface field two ways — **current speed** (|velocity|,
cmocean `speed`) and **relative vorticity ζ/f** (see [vorticity.md](vorticity.md))
— as mutually-exclusive base rasters in the **Currents** control. Both are
**time-sliced**: a bottom-centre **time slider** scrubs the shading through the
CMEMS forecast at **12 h steps, −12 h … now … +72 h** (8 frames). The animated
**flow trails** ship the same 8 frames and scrub in lockstep; the **near-inertial
animation** follows too, anchoring its analytic phase to the displayed field time
(see [forecast.md](forecast.md)). So every time-dependent layer shares one clock —
scrub to +48 h and the whole map shows +48 h.

## One window, one clock

All eight frames — speed, ζ/f **and** the flow-trail grids — come from a **single**
CMEMS fetch: the 6-hourly `PT6H-i` product over `[now−12 h, now+72 h]`
(`_currents.fetch_shading_window`). 12 h is a multiple of the 6 h grid, so every
slider target lands on a real step; the **now** frame (offset 0) is identical to
the single-time speed raster the map shipped before. The flow trails slice the same
window per frame (`to_velocity_frames`), so they share the shadings' clock without a
second download.

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

## Discrete colour classes

Both rasters snap the field to **`N_BINS = 12` flat colour classes**: the
`_currents._quantize_unit` step rounds the normalized `[0, 1]` colour-map input to a
bin midpoint *before* the cmocean lookup, so a frame carries 12 constant-colour
regions instead of a ~256-step continuous ramp — same `speed` / `curl` palettes, no
new colours. Two reasons:

- **Bytes.** Lossless WebP encodes large constant-value regions cheaply, so binning
  the field cuts each frame by a further **~60 %** — the single biggest per-frame
  lever here (see the Transport totals below).
- **A quantitative legend.** Discrete classes make the map ↔ legend lookup exact: a
  pixel's colour reads as one labelled class the way an oceanographic contour chart
  does, rather than eyeballing a position on a smooth ramp. The client renders the
  legend bar as the *same* 12 hard-edged classes (`renderCurrentsInfo` /
  `renderVorticityInfo` over the meta's `colorbar`, which now carries the 12 class
  colours, not sampled ramp stops).

The diverging ζ/f map keeps an **even** bin count so **zero stays a bin edge**: the
neutral midpoint `0.5` falls on the boundary between the two central classes (6
classes per rotation sense), so no single class straddles no-rotation. The cost is
deliberate banding; `N_BINS` (in `_currents`) is the one constant to raise to go back
toward a continuous ramp.

## Transport: full pixel detail, minimum bytes

The rasters keep **full pixel resolution** (no coarsening — coarser pixels lose the
mesoscale eddies that are the whole point), so the only lever is the encoding and
*when* the bytes move. Three choices keep an 8-frame slider affordable on the
cruise's at-sea VSAT link ([data.md](data.md)):

1. **Lossless WebP, not PNG.** Each frame is a lossless WebP with a native alpha
   plane for land (`_raster.mercator_rgba_webp`) — universally supported and honouring
   `image-rendering: pixelated`, so the crisp native-grid look is unchanged. WebP with
   lossless alpha is roughly *half* an equivalent RGBA PNG's bytes at *identical*
   pixels; combined with the 12 discrete classes above, a cruise-bbox speed frame lands
   at **~27 kB** (the single RGBA PNG the map shipped before was ~310 kB). Weighed and
   rejected: indexed PNG (bigger, and its fixed 256-colour palette buys nothing once
   the field is binned to 12 classes); a client-side temporal-delta codec (a canvas
   reconstruction pipeline — real complexity for less gain than WebP + binning already
   give).

2. **No extra download in the build.** All frames slice the one window already
   fetched (above), so the slider costs the build one wider fetch, not eight.

3. **Lazy transfer on the client.** The page loads the **now** frame only — so the
   critical-path bytes (~27 kB) are far *lighter* than the old single 310 kB
   raster. The other seven speed frames prefetch in the background on the slider's
   **first move**, so a viewer who never scrubs pays only that one frame; the ζ/f
   frames prefetch only once vorticity is first selected, so an untouched layer costs
   zero bytes. Sliding to an un-prefetched frame just fetches it on demand.

Net: the naïve 8× (~6 MB for both raster fields) becomes ~0.22 MB of speed frames +
~0.30 MB of ζ/f frames (WebP + the 12 discrete classes), and only ~27 kB on the
initial load.

The **flow-trail** grids are JSON, not rasters, so the same lazy-transfer discipline
applies with a different codec: values round to **4 dp** (the raw solve emits
17-significant-digit floats — ~3× the bytes for sub-mm/s precision the decorative,
gamma-scaled trails never resolve), which roughly halves each frame to ~0.45 MB. The
now frame loads first (actually *lighter* than the single ~1 MB grid the map shipped
before), the other seven prefetch on the slider's first move, and scrubbing to an
un-prefetched frame fetches it on demand — so the 8-frame flow set is ~3.6 MB total
(~0.94 MB gzipped, comparable to one shading field) but only ~0.45 MB on load.

## Artifacts

- `speed_-12h.webp … speed_+72h.webp` and `vorticity_-12h.webp …` — 8 lossless
  WebP frames each (filename label `f"{offset:+03d}h"`).
- `currents_-12h.json … currents_+72h.json` — 8 flow-trail leaflet-velocity grids,
  one per slider offset (`to_velocity_frames`), values rounded to 4 dp.
- `currents_meta.json` / `vorticity_meta.json` — shared `bounds`, `vmax`
  (+ `vmin` for ζ/f), `units`, `colorbar` (the 12 discrete class colours the raster
  is binned to — see *Discrete colour classes*), plus:
  - `frames`: `[{offset_h, valid_time, file}]` — the slider manifest, one entry per
    frame with its own `valid_time`;
  - `now_offset_h`: `0` — which frame the slider opens on;
  - top-level `valid_time`: the now frame's time, kept for now-only readers (the
    deploy tool seeds its run start from it — see [interactive_forecast.md](interactive_forecast.md)).
  - `currents_meta.json` additionally carries `flow_frames`: the flow trails' own
    `[{offset_h, valid_time, file}]` manifest (same offsets/times as `frames`).

## Client

`app.js` builds the speed and ζ/f overlays at their `now` frame, then a
`buildTimeSlider` control (a positioned element, not an `L.control`, so it can
centre and span the map width; Leaflet mouse propagation disabled so dragging the
handle never pans the map). Moving the slider `setUrl`s **every** registered
shading overlay to that offset's frame (so speed and ζ/f stay in lockstep even
while one is hidden), swaps the flow trails to that frame's grid
(`flowLayer.setData`, loaded lazily and cached; a request token drops a stale
late-arriving fetch), updates the sidebar displayed-time line
(`renderCurrentsInfo(meta, frame)`), and re-locks the deploy tool's start to the
displayed field. It also mutates `displayedFieldTime`, which the near-inertial
animation reads live to anchor its phase (see [forecast.md](forecast.md)), so that
overlay follows without any per-frame data. The slider is built only when the meta
carries more than one frame; with CMEMS down (no meta) there is no slider and no
shading, as before.
