# 043 — Default continent shading (land/sea basemap)

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

Add a small builder (e.g. `_landmask.to_landmask_webp(...)` or a function beside
`to_speed_frames`) that:
- takes any single CMEMS current slice (any `uo`/`vo` frame — we only need its
  NaN pattern and grid),
- `to_rgba(field)`: `NaN` (land) → opaque gray (e.g. `#c9c9c4`); valid (sea) →
  a flat blue (e.g. `#dfe7ee`, matching the retired CSS tone, or a touch
  deeper); full alpha everywhere (it's the base — no transparency needed),
- runs through `mercator_rgba_webp` so it co-registers **exactly** with the
  shadings' `meta.bounds` (same grid → same `_warp_north_up` edge bounds),
- writes `landmask.webp` into `map_dir` and records **just its filename** in the
  existing `currents_meta.json` as `meta["landmask"] = "landmask.webp"` — no
  separate `landmask_meta.json` and no separate bounds, since the frontend reuses
  `meta.bounds` (used today by the speed overlay, `app.js:3730`).

Wire it into `build.py` alongside the shading writes (`330–364`,
`atomic_write_bytes` at `343`, `currents_meta.json` at `350`). It only needs
**one** current frame's grid, so it piggybacks the window already fetched by
`fetch_shading_window` — no extra CMEMS egress. Pick a **known-complete frame**
(or intersect the NaN pattern across frames) so transient missing-data NaNs
aren't baked as spurious "land". Keep it lossless-or-tiny WebP; a 2-colour mask
compresses to a few kB.

Note it's in the **slow (CMEMS) tier** since it reads a CMEMS grid; but as the
mask is static, it could also be baked once and committed if we'd rather not
depend on the slow cron. Decide during build: piggyback the slow tier (simplest,
consistent with the other rasters).

## Frontend (`app.js`)

1. Add a pane **below** the shading pane (nothing custom sits below z350 today):
   near the pane block (`3545`), `map.createPane("basemap").style.zIndex = 300;`.
2. Near the shading construction (`3721–3751`), add — **gated**, not
   unconditional: when `meta?.landmask && meta.bounds` exist,
   `L.imageOverlay(frameUrl(meta.landmask), meta.bounds, { pane: "basemap",
   className: "crisp-raster" }).addTo(map);` — a permanent base, **not** one of
   the `currentShading` radio entries. On a static-only / no-CMEMS deploy `meta`
   is null, so the overlay is simply skipped.
3. **Keep** the CSS `#map` background (`style.css:97`, `#dfe7ee`) as the
   pre-load / 404 / no-field fallback — the mask paints over it when present.
   (Match the mask's sea colour to it so there's no flash.)

The shading radios (speed / ζ·f / **None**, `4038`) stay in the `shading` pane
**above** the basemap: pick a shading and it paints over the land/sea base; pick
**None** and the gray-land/blue-sea base shows through — which is the #29 default
view. (Consider whether the map should open on **None** so the continent is the
first thing seen; today it opens on speed. Small default choice — flag it.)

## Verify

Served app: on first load (or with shading = None) the continent renders as a
crisp gray landmass on a blue sea, aligned exactly with the coastline where the
speed shading stops; panning is instant (no tile fetches); selecting a shading
overlays it cleanly; the `landmask.webp` transfer is a few kB.
