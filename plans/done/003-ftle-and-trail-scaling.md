# FTLE (LCS) overlay + trail-velocity scaling

> Superseded. The FTLE overlay was built, redesigned (004/005), then removed from the codebase entirely; the trail-velocity scaling was replaced by the flow-vis pipeline (038). Kept for history.

Two flow-rendering additions: (A) overlay the SPASSO **FTLE** field as red,
alpha-ramped ridges to expose Lagrangian coherent structures — eddy rims,
filaments, transport barriers; (B) compress the velocity magnitude that drives
the particle animation so the slow-but-lively NW eddies actually move. (B) is the
backlogged trail-scaling item, folded in here because it shares the flow code.

## A. FTLE overlay

### Source

IPSL THREDDS — the WHIRLS cruise's own SPASSO v2.1 product (LEGOS/OMP), public,
no auth. Date-templated OPeNDAP URL:

    https://thredds-x.ipsl.fr/thredds/dodsC/WHIRLS/SATELLITE/FTLE/{YYYYMMDD}_FTLE_Copernicus_PHY.nc

`xarray.open_dataset(url)` over OPeNDAP with the default netcdf4 engine works
directly (raw `…/fileServer/…` URL as a download fallback). Daily **00Z**
product; fetch today's date, and if it is not yet published fall back to the most
recent prior day. FTLE valid-time (00Z) is ~12 h before our speed field (12Z) —
acceptable; surface both valid-times.

### Field

- Variable `ftle`, dims `(time=1, lat=1950, lon=2260)`, 0.01°, **backward FTLE**,
  units day⁻¹; high values = attracting LCS (eddy/filament rims, barriers).
- Longitudes already −180..180. No NaN / no land mask. Coordinates are data vars
  `lons`/`lats` (assign as coords or index by position — `.sel` won't work raw).
- Extent: the **Cape Basin box only**, lon 5.70..28.29 E, lat −44.50..−25.01 —
  so on the full map the FTLE occupies the centre, not the whole domain.

### Rendering

- Red, **alpha-ramped** raster: colour = red, `alpha = clip((ftle − vmin)/(vmax −
  vmin), 0, 1)`, with **vmin/vmax = p2/p98** of the field. (Full min..max washes
  to an opaque red blanket; p2..p98 makes the filamentary ridges crisp and lets
  the speed shading show through — confirmed in the preview.)
- **Mercator-warped exactly like `speed.png`.** Both are equirectangular fields
  on a Web-Mercator map; a plain `imageOverlay` of an unwarped raster misregisters
  in latitude (this is the ~200–300 km southward offset seen in the flat preview).
  Warp the FTLE field to EPSG:3857 and place it with `L.imageOverlay` at the FTLE
  box bounds, so it co-registers with the speed shading. **Factor the
  Mercator-warp + RGBA-PNG step into a shared `_raster.py` helper** used by both
  `_currents` (speed) and `_ftle`.
- Artifacts: `ftle.png` + `ftle_meta.json` (`bounds` = FTLE box, `vmin`/`vmax`,
  `valid_time`, `units`).
- Layer: a toggleable **"FTLE / LCS ridges"** overlay above the speed shading;
  default on. Small red-intensity legend + valid-time in the sidebar.

### Build

New `_ftle.py` (fetch → warp → PNG + meta). `build.py` gains an FTLE step,
best-effort and independent of the CMEMS step (build still succeeds if THREDDS is
down). No new dependency — OPeNDAP via netcdf4 and matplotlib are already present.

## B. Trail-velocity scaling

leaflet-velocity moves particles ∝ velocity magnitude, so the jet (≈2.5 m/s)
streaks while the NW eddies (≈0.2 m/s) barely move — a 10× range that makes the
lively NW look dead. Compress the magnitude fed to the animation, direction
preserved. Normalised motion (clip p99≈1.15 m/s), jet/eddy ratio:

| transform | eddy 0.2 | jet 2.0 | jet/eddy |
|---|---|---|---|
| linear | 0.17 | 1.74 | 10× |
| **sqrt (γ0.5)** | 0.42 | 1.32 | **3.2×** |
| pow γ0.4 | 0.50 | 1.25 | 2.5× |
| tanh | 0.33 | 1.00 | 3.0× |

- **Recommend `sqrt` (γ0.5)**: eddies reach ~0.4 of jet motion, jet still clearly
  fastest. `pow γ0.4` if more lift wanted. Apply in `to_velocity_json`
  (`u, v *= f(|v|)/|v|`); retune `velocityScale` for the rescaled magnitudes.
- Caveat: leaflet-velocity keys motion **and** its hover readout/colour off the
  same magnitude, so the readout would show scaled m/s. Mitigate by leaning on the
  speed shading's true-speed legend and dropping the velocity layer's
  `displayValues` (or labelling it "relative"). The dark→white brightness ramp
  re-keys to the scaled max — fine, it lifts the eddies too, which we want.

## Out of scope

FTLE time series / animation, eigenvector orientations, other SPASSO products,
further CMEMS fields.

## Decisions

1. Layer stack (bottom → top): **speed shading → FTLE → particle flow**, with the
   drifter markers above all of them.
2. FTLE layer is **on by default** (toggleable).
3. Velocity transform: **`sqrt` (γ0.5)** for now; drop the velocity hover readout.
4. FTLE date: pick the file **closest** to the speed valid-time, and **give up if
   the nearest available is more than 24 h away** (effectively today's 00Z file).
