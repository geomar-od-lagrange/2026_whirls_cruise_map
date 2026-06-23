# FTLE latitude registration correction

The FTLE overlay is plotted **~0.13–0.14° (~14 km) too far north** along the whole
coastline — its ridges penetrate land. This is a real registration error, not a
masking artifact, confirmed by direct measurement and by eye on the live map. The
fix is a small, documented latitude correction; this plan records the evidence and
the open questions so it can be applied (and confirmed with the product authors)
in one pass.

## What's wrong, and what isn't

Measured per layer (diagnostic scripts in `tmp_ftle_preview/`, gitignored):

- **FTLE overlay — offset, ~0.13–0.14° N.** The FTLE land plateau edge sits
  **+14.6 km north** of the coast-correct CMEMS land mask on the clean E–W south
  coast (20.5–24.5°E), with a 2-D IoU fit of −0.14° lat. The offset is **stable
  across two days** (20260623, 20260622) and roughly constant in longitude → a
  **rigid, latitude-only shift**, not a scale/projection warp. Longitude component
  is within noise (flips sign between segments) → no lon correction.
- **Currents particle traces — NOT offset (0 km).** The leaflet-velocity header is
  exact and leaflet-velocity 1.7.0 places `la1`/`lo1` as grid points with no
  half-cell bug (reconstructed error 0.000 km; velocity land mask vs coast IoU
  0.995, zero shift). The visual impression came from bright trails riding along
  the offset FTLE ridges, plus ≤14 km coarse-cell bleed and known screen-ghosting
  already handled in `app.js`.
- **Speed shading — NOT offset (~0).** Independent check (projecting the coastline
  *into* the PNG pixel grid, not inverting the warp under test) puts the
  land-alpha edge −4 km from the true coast — within one native cell. The
  `_raster` Mercator-warp + edge-bounds fix is correct.

So there is exactly **one bug: the FTLE latitude**.

## Root cause

The FTLE file's coordinate arrays are pristine — `lats` is a clean
−44.50→−25.01 @ 0.01° linspace matching the file's own `geospatial_lat_min/max`
to the digit, `lons` likewise. Our assignment in `fetch_ftle` is correct. The
displacement is **between the `ftle` data content and its own `lats` labels** (the
content sits ~13 rows north of where `lats` says) — i.e. it is **baked into the
SPASSO product's grid registration**, not introduced by our code. The file carries
**no CRS, grid_mapping, or cell bounds** to derive a principled correction from.

Note on magnitude: a naive `ftle < 0.02` land threshold over-reads the offset
(~−0.20°) because low-FTLE "pedestals" exist over calm open ocean and get counted
as land. Clean land-edge methods converge on **~0.13–0.14°**. The eye sees ~20 km
because the 0.135 contour also catches genuine high near-coast filaments that
smear a few km further inland than the 14 km coordinate shift.

## Fix

Apply a documented empirical latitude correction where coords are assigned in
`_ftle.fetch_ftle` (`_ftle.py:65`):

```python
FTLE_LAT_CORRECTION_DEG = -0.13   # SPASSO grid registers ~0.13 deg N of its
                                  # geophysical content; measured vs CMEMS/NE coast
ftle = ftle.assign_coords(lat=("lat", lat1d + FTLE_LAT_CORRECTION_DEG),
                          lon=("lon", lon1d))
```

- **Tune the constant empirically, don't guess.** Apply, rebuild, re-run the
  coastal-band land-edge measurement, and adjust until the FTLE land edge sits on
  the coast (expect ~−0.13 to −0.14°). Confirm visually with the `coast_check`
  overlay.
- **Latitude only** — no longitude term (within noise).
- Write a short `docs/` note (the measurement, the value, the CI −0.09…−0.18°,
  and that it's a product-registration quirk).

## Open questions / decisions for tomorrow

1. **Confirm with the SPASSO authors first?** This is their product's registration
   quirk (contact in the file: Louise Rousselet). Either get the authoritative
   grid convention and apply that, or ship the empirical −0.13° now and reconcile
   later. A GEOMAR connection may make the former quick.
2. **Residual near-coast ridges.** After the shift, decide whether to also clip the
   FTLE to ocean (the unmasked high near-coast filaments that smear inland) — see
   the masking options discussed for the overlay — or leave them.
3. **Lock the exact constant** from the empirical tuning (−0.13 vs −0.14°).

## Validation

Rebuild (`pixi run build`), re-measure the FTLE land-edge offset vs the coast
(should drop to ~0), and eyeball `coast_check` over the south coast. The currents
and shading need no change and should be re-confirmed unchanged.
