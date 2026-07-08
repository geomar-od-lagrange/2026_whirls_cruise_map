> **Implemented** — see [docs/currents.md](../../docs/currents.md).

# Forecast time slider (±12 h steps of the CMEMS field)

Give the map a **time slider** that scrubs the surface-current **speed** and
**vorticity (ζ/f)** shadings through the CMEMS forecast at 12 h steps:
**−12 h, now, +12 h, +24 h, +36 h, +48 h, +60 h, +72 h** (8 frames). The flow
trails and near-inertial animation stay the "now" snapshot (they are textures of
one instant, not a forecast the slider scrubs); only the two scalar rasters gain
frames.

## Transport budget — the hard part

The rasters must keep **full pixel detail** (no coarsening), so the only lever is
per-byte encoding + when bytes move. Today one field ships as an RGBA PNG:
`speed.png` 322 kB, `vorticity.png` 451 kB. Naïve 8× would add ~6 MB — untenable
on the at-sea VSAT link. Three decisions cut it:

1. **Indexed (paletted) PNGs, not RGBA.** The colour map is a 1-D ramp, so each
   pixel is one palette index, not four channels. Land is a single fully
   transparent palette entry (PNG `tRNS`), replacing the RGBA alpha plane. This
   is a ~3–4× shrink per frame at *identical* pixel resolution — the palette is
   256 entries (255 colour levels + 1 transparent), so the colour quantisation
   matches the 256-level colour map sampling; nothing visible is lost. The client
   still just points an `L.imageOverlay` at the file — the palette is baked in, no
   client-side colour-mapping or canvas pipeline. (WebP-lossless was weighed: ~20 %
   smaller again but full-colour only, so it can't carry a transparent-index; the
   indexed-PNG + `image-rendering:pixelated` path stays simplest and is already
   the repo's shape.)

2. **No new download in the build.** The slow build already fetches the hourly
   window (`fetch_field_window`, `PT1H-m`) for forecast/hindcast/inertial/API. All
   8 shading frames are **slices of that same window** — the window becomes *the*
   field. The single-time `fetch_field` (`PT6H-i`) is dropped: currents.json
   trails and every shading frame now come from the one hourly window, so the
   whole overlay set shares one clock. The window's forward reach grows 60 h → 72 h
   to cover the +72 h frame (`FORECAST_WINDOW_FWD_H = max(FORECAST_HORIZON_H +
   SLOW_CADENCE_H, SHADING_FWD_H)`); back stays 12 h (already covers −12 h).

3. **Lazy transfer on the client.** The page loads the **now** frame only — the
   critical-path bytes are unchanged from today. The other 7 speed frames prefetch
   in the background after first render; the vorticity frames prefetch only once
   vorticity shading is first selected. Sliding to an un-prefetched frame just
   fetches it on demand.

## Shared scale across frames

A per-frame `vmax` would make the colours (and the legend) mean a different speed
at each tick. One **shared `vmax`** is computed over all 8 frames (99th pct of
speed; 98th pct of |ζ/f|, symmetric) so a colour is the same value at every time
and cross-time comparison is valid. The legend is rendered once; only the
displayed *time* changes as the slider moves.

## Artifacts

- `speed_-12h.png … speed_+72h.png` (8 indexed PNGs), `vorticity_-12h.png …`
  (8 indexed PNGs). Filename label is `f"{offset:+03d}h"`.
- `currents_meta.json` / `vorticity_meta.json` grow a `frames` list
  `[{offset_h, valid_time, file}]` + `now_offset_h: 0`, keep shared
  `bounds/vmax/units/colorbar` (+ `vmin` for vorticity), and keep a top-level
  `valid_time` = the now-frame time (so the deploy tool's start-time seed and any
  now-only reader are unchanged).

## Code

- `_raster.py`: extract the warp+bounds into `_warp`, add `mercator_indexed_png
  (values, lats, lons, normalize, cmap, n_levels=255)` — warp, map values→uint8
  index via `normalize` (→[0,1], NaN=land→index 255), write a P-mode PNG with a
  256-entry palette and `tRNS` on the land index. `mercator_rgba_png` stays (the
  inertial raster still uses it).
- `_currents.py`: `SHADING_STEP_H/BACK_H/FWD_H` + `SHADING_OFFSETS_H`; bump the
  window forward reach; `to_speed_frames(window)` → `(frames, meta)`. Replaces
  `to_speed_png`. `to_velocity_json` unchanged (fed the now-slice).
- `_vorticity.py`: `to_vorticity_frames(window)` → `(frames, meta)`, shared
  symmetric vmax. Replaces `to_vorticity_png`. `zeta_over_f` unchanged.
- `build.py` `_derive_slow`: fetch the window first; slice now → currents.json;
  write the 8+8 frame PNGs + the two metas; forecast/hindcast/inertial/API-cache
  as before. Drop `fetch_field`.
- `app.js` + `index.html` + `style.css`: a bottom-centre time-slider control (8
  ticks, −12 h…+72 h, "now" at 0) that `setUrl`s the speed & vorticity overlays,
  updates the sidebar displayed-time line and the deploy tool's
  `displayedFieldTime`, and prefetches frames lazily.

## Non-goals

- Framing the flow trails / near-inertial animation (they are one-instant
  textures; per-frame trails would re-inflate transport by ~8 MB of JSON).
- Client-side temporal delta compression (a canvas reconstruction pipeline; the
  indexed-PNG + lazy-load path hits the budget without it).
</content>
</invoke>
