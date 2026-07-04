> **Implemented.** See [docs/vorticity.md](../../docs/vorticity.md) for the
> current-state documentation.

# 016 — Normalized relative-vorticity (ζ/f) overlay

Add a surface **relative-vorticity** raster, normalized by the local Coriolis
parameter (the Rossby number ζ/f), as a toggleable map layer alongside the
current-speed shading and flow trails. It is the diagnostic that makes the eddy
field legible: cyclones and anticyclones read directly as opposite-signed lobes,
which speed magnitude alone does not show.

## What ζ/f is

- Relative (vertical) vorticity: `ζ = ∂v/∂x − ∂u/∂y` (s⁻¹).
- Planetary vorticity: `f = 2Ω sin φ` (s⁻¹), **negative in the Southern
  Hemisphere** (the whole cruise bbox is −55…−15° lat).
- The overlay shows the dimensionless ratio `ζ/f`. With both ζ and f negative for
  a Southern-Hemisphere cyclone, **ζ/f > 0 is cyclonic and ζ/f < 0 is
  anticyclonic** — the standard Rossby-number sign, the same in both hemispheres.
- It is a **signed** field centred on zero, unlike speed (0→max). So it needs a
  *diverging* colour map and a *symmetric* legend — the one real structural
  difference from the speed raster.

## Source and grid

Derived from the **same single-time CMEMS field** `_currents.fetch_field()`
already fetched for the speed/flow overlays (`cmems_mod_glo_phy-cur_anfc_0.083deg`,
1/12°). No new download: vorticity is a spatial derivative of the `uo`/`vo`
already in hand. Rendered at the same near-native grid resolution as speed.png.

Derivatives on the lat/lon grid carry the metric factors:
`∂/∂x = 1/(R cos φ) · ∂/∂λ`, `∂/∂y = 1/R · ∂/∂φ` (λ, φ in radians,
R = 6 371 km), via `np.gradient` along the lon/lat axes. CMEMS land is NaN;
`np.gradient` propagates it, so the coastal cell ring masks out (a one-cell
erosion of the ocean edge — acceptable, and consistent with how the speed warp
already treats land). f never approaches zero over this bbox, so ζ/f is
well-conditioned everywhere.

## New module: `_vorticity.py`

Mirrors the shape of `_inertial.to_inertial_png` — a derived diagnostic rendered
through the shared `_raster.mercator_rgba_png` helper — rather than growing
`_currents.py` (which stays about the current vector field and its two renders):

- `zeta_over_f(field) -> (values2d, lats, lons)` — the ζ/f field on the ascending
  lat/lon grid, land NaN preserved.
- `to_vorticity_png(field) -> (png_bytes, meta)` — diverging cmocean `curl` map,
  clipped to a **symmetric** `±vmax` (vmax = the `CLIP_PERCENTILE`-th percentile
  of `|ζ/f|`), land transparent. `meta` matches `currents_meta.json`'s shape plus
  a **`vmin`** key (= `−vmax`) that signals the symmetric range; `colorbar` stops
  are sampled across the full diverging map; `units: "ζ/f"`; `valid_time` reuses
  `_currents.valid_time(field)` so it shares the speed raster's clock.

## Build wiring (`build.py`)

Inside the existing `if field is not None:` block that renders speed.png — the
same single field, so a third independent best-effort render:

- `vorticity.png` + `vorticity_meta.json`.

A failure warns and skips only the vorticity artifacts, leaving speed/flow intact
(same pattern as every other overlay).

## Client (`app.js` + `index.html` + `style.css`)

- `DATA.vorticity` / `DATA.vorticityMeta`.
- An `L.imageOverlay` in the existing **`shading`** pane, `className: "crisp-raster"`,
  registered into `overlays` as **"Vorticity ζ/f"** but **default off** (never
  `addTo(map)`) so speed stays the default shading. Toggled from the same layer
  control as Current speed / Current flow.
- A **symmetric** legend: a new `renderVorticityInfo(meta)` — a local twin of
  `renderCurrentsInfo`, since the diverging/`vmin`-based scale differs from the
  0→vmax speed bar (the codebase already prefers local twins over widening a
  shared helper, cf. `_inertial._colorbar_stops`). Rendered into a new
  `#vorticity-panel` sidebar section with a one-line sign-convention hint
  (anticyclonic − / cyclonic +).

## Docs

- `docs/vorticity.md` — what ζ/f is, the SH sign convention, why single-time (a
  snapshot diagnostic, not advected), the diverging-map/symmetric-legend choice,
  and the coastal-erosion land note. Compare the alternative of an unnormalized ζ
  (units s⁻¹, no cross-latitude comparability) and of Okubo–Weiss (a different
  question — strain vs rotation).
- Add the layer to `docs/features.md`'s overlay list.
- On completion: move this plan to `plans/done/` with a pointer line; add a
  ROADMAP entry.

## Out of scope

- No time dependence / advection — ζ/f is a snapshot diagnostic.
- No Okubo–Weiss, no eddy-boundary detection, no separate flow-trail change.
- No client-side recompute — it is a baked raster like speed.png.
