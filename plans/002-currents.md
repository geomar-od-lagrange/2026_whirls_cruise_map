# Currents inclusion

Turn the placeholder currents overlay into the intended feature: current **speed
as a cmocean-`speed` shaded field** with **animated flow trails** over it, for a
single "today" snapshot. The cruise targets mesoscale eddies ("whirls") in the
Cape Basin / Agulhas region, so the filled speed field — which exposes the eddies
and the retroflection jet — is the point; the trails add direction and texture.

The trails are greyscale, **keyed to speed: dark at typical speeds, white in the
fast jet**, so speed reads from both the green shading and the bright trails and
the Agulhas jet pops. Speed leaves the MVP's speed-coloured particles and moves
into the shading.

## Scope

- **Single snapshot**, the CMEMS field nearest "now". No time slider / forecast
  stepping — the map stays deliberately rudimentary.
- **Product:** `cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i` — 6-hourly
  instantaneous total surface velocity (`uo`/`vo`), 1/12°.
- One CMEMS field per build, so all artifacts share one valid-time.

## Two resolutions, by design

The two layers have different needs, so they take different resolutions from the
same field:

- **Trails need the raw vector grid on the client** — leaflet-velocity animates by
  interpolating `u`/`v` per particle, per frame, in the browser, so the grid ships
  to the client regardless. Native 1/12° as JSON is ~9.5 MB and animates heavily;
  **coarsened ~1/4° (`COARSEN_STRIDE`) is ~1 MB and smooth**, and the trails are a
  texture so the coarsening is invisible. → keep coarsening, trails only.
- **Shading is a raster image**, so near-native 1/12° is still small (~100s of KB
  PNG) and visibly sharper. → **do not coarsen the shading.**

## Artifacts (one CMEMS field → `site/data/`)

- `currents.json` — coarse (~1/4°) leaflet-velocity `[u, v]` grid for the trails.
- `speed.png` — near-native speed raster: |velocity|, cmocean `speed` colour map,
  clipped `0…vmax`, **land transparent (alpha)**, **warped to Web Mercator**
  (EPSG:3857) so a plain `L.imageOverlay` registers correctly — an equirectangular
  PNG would be mis-placed in latitude over our −55…−15 span. The warp is a numpy
  row-resample from even latitude to even Mercator-Y plus the colour map; no heavy
  GIS dependency.
- `currents_meta.json` — small, data-drives the client: `valid_time`, latlng
  `bounds` for the overlay, and `vmax` + `units` + `colorbar` stops for the legend.

`vmax` = the **99th percentile** of in-region speed (today ≈ 1.15 m/s; raw max can
reach ~2.6 at the jet, so the clip keeps eddies legible instead of washed flat).

## Rendering

- **Shading:** `L.imageOverlay(speed.png, bounds)` in a pane below the trails.
- **Trails:** leaflet-velocity with a greyscale **dark→white `colorScale`** over
  `0…vmax` and `maxVelocity = vmax`; canvas above the shading, markers above both.
  `velocityScale` / `lineWidth` / opacity stay the tuning knobs.
- **Legend:** a speed colourbar built from `colorbar` + `vmax` + `units`.
- **Valid-time:** shown in the sidebar so the field's freshness is visible.
- Currents on by default; toggleable in the layers control.

## Robustness

- Pin the subset to the shallowest level to silence the depth-clamp warning.
- The field already falls back to the latest available step when the dataset is
  mid-update — that is intentional and is surfaced through the displayed valid-time.

## Later / out of scope

- CI build needs `copernicusmarine` credentials as secrets — shared with the
  automation step (003).
- Time slider / forecast horizon, Lagrangian advection, SLA/ADT eddy contours and
  other fields — revisit only if the need arises.
- **Static-LIC fallback:** if animation is ever dropped for zero client compute,
  the whole thing (shading + frozen flow streaks) could be one server-rendered PNG
  with no `currents.json` and no leaflet-velocity. Noted, not planned.
