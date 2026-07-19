# FTLE latitude registration correction

> **Implemented 2026-06-24** — the latitude correction (`FTLE_LAT_CORRECTION_DEG =
> -0.13` in `_ftle.fetch_ftle`) is live and verified (south-coast FTLE-low → CMEMS
> land edge dropped from −0.13° to −0.02°); the FTLE overlay was later removed from the codebase entirely, and its doc
> went with it.
> The optional ocean clip was deferred to [BACKLOG](../BACKLOG.md).

The SPASSO FTLE field is plotted **~0.12–0.14° (~13–15 km) too far north**: its
geophysical content sits that far north of where its (pristine) latitude labels
say. The fix is a small, documented **latitude-only** coordinate correction in
`_ftle.fetch_ftle`, plus an **ocean clip** of the contour to remove the residual
near-coast filaments a shift alone can't clear. This plan records the evidence —
including the experiments that *break* the earlier ambiguity — and the fix.

## How this was settled (two independent reviews + a tie-breaker)

Two independent Opus investigations reached **opposite** headline verdicts:

- **"No real shift — coastal artifact."** The FTLE carries finite *low* values
  over land (no land mask, no NaN, no fill), and that low-FTLE region overhangs
  the coast by ~14 km. Hypothesis: a seaward low-FTLE *skirt*, not a field shift;
  the open-ocean FTLE-vs-CMEMS structural test "aligns at ~0".
- **"Real rigid −0.12° shift."** The land-mask offset vs the coast-correct CMEMS
  mask is −0.14°, level-independent, and reproduces across days; the structural
  test is simply *too insensitive* to see 14 km (silent, not contradictory).

Both agreed on everything else (see "What isn't wrong"). The disagreement reduced
to one question — **is the offset a coast-hugging artifact or a whole-field
shift?** — settled by two tie-breaker experiments:

1. **Injected-shift calibration of the structural test.** Inject known latitude
   shifts into FTLE and check whether the open-ocean FTLE↔CMEMS cross-correlation
   recovers them. The machinery is sound (FTLE-vs-itself control: slope +1.00,
   intercept 0.000) and two boxes resolve cleanly — but the recovered **intercept
   is confounded**: backward-FTLE ridges sit on eddy peripheries, not on
   instantaneous speed maxima, so the absolute offset mixes registration error
   with a physical LCS-vs-speed offset (and even leans the wrong sign). **Lesson:
   the open-ocean structural test cannot cleanly measure registration** — it is
   silent, exactly as the second reviewer argued. The earlier plan's claim that
   structure "corroborates" the shift was wrong.

2. **Orientation discriminator (decisive, confound-free).** A *rigid* field shift
   is a **constant** vector on every coast segment (dlat<0, dlon≈0). A *seaward
   skirt* is a vector that **rotates to stay coast-perpendicular**. Fitting the
   2-D shift of the FTLE-low mask onto the coast-correct CMEMS land mask, using
   each segment's well-conditioned component:

   | segment | orientation | well-constrained | result |
   |---|---|---|---|
   | south coast 20–25 E | E–W → pins **lat** | dlat | **−0.12 to −0.14°** (north) |
   | SE coast 25–27 E | diagonal | both | −0.16° / lon ≈ 0 |
   | west coast 15.5–18 E | N–S → pins **lon** | dlon | **≈ 0** (−0.02°) |

   A seaward skirt **must** show a strong **westward** offset on the west coast
   (seaward = west there — its best-conditioned, most detectable signature). It is
   **absent** (dlon ≈ 0, stable across thresholds 0.02–0.04). The offset stays a
   **constant northward vector** at every orientation → **rigid northward field
   shift**, skirt hypothesis falsified. The west-coast jump to −0.28° at threshold
   0.05 is offshore-pedestal contamination (IoU collapses to 0.47), not signal.

**Conclusion:** there is a **real, rigid, latitude-only northward offset of ~0.13°**
baked into the SPASSO product. The earlier plan's *fix* (≈ −0.13° lat) was right;
its *justification* (structural corroboration) was not, and it understated the
residual coastal filaments.

## What's wrong, and what isn't

Measured per layer (diagnostic scripts in `tmp_ftle_preview/`, plus the
tie-breakers, gitignored):

- **FTLE overlay — real rigid offset, ~0.12–0.14° N.** Best-conditioned estimate
  is the E–W south coast: FTLE-low edge sits **−0.12 to −0.14° lat** of the
  coast-correct CMEMS mask, stable across thresholds and **reproducing on a second
  day**. The orientation test shows it is a **constant north vector**, not a
  coast-perpendicular skirt → a rigid latitude shift. Longitude component ≈ 0
  (west coast pins lon at −0.02°) → **no lon correction**.
- **Currents particle traces — NOT offset (0 km).** leaflet-velocity 1.7.0 places
  `la1`/`lo1` as grid points; reconstructed lat error **0.000 km**; velocity land
  mask vs coast IoU ~0.99, zero shift. No half-cell bug.
- **Speed shading — NOT offset (~0).** The `_raster` Mercator-warp + edge-bounds
  are self-consistent (interior round-trip 0.000 km); on-disk PNG land-alpha edge
  **−3.9 km** from the true coast (within one native cell, slightly *south*).
- **The FTLE field has no land mask** — finite low FTLE everywhere, incl. over
  land — and carries **genuine high-FTLE near-coast filaments**. So even after the
  shift, ~the deepest filaments still cross land (≈2.7% of the deployed contour's
  vertices, median ~14 km, max ~34 km inland, *reducing* under the shift). A shift
  alone is not a complete visual fix → also clip to ocean.

So there are **two coupled issues**: (1) a real ~0.13° latitude registration shift;
(2) an unmasked field with real coastal filaments. Fix both.

## Root cause

The FTLE file's coordinate arrays are pristine — `lats` is a clean
−44.50→−25.01 @ 0.01° linspace (max deviation from a perfect linspace < 0.5 m)
matching the file's own `geospatial_lat_min/max` to the digit; `lons` likewise.
The file's structure is `ftle(time, lat, lon)` with **1-D** `lats`/`lons` data
variables; `fetch_ftle`'s `assign_coords` is a **faithful** promotion of those own
axes (the 2-D `[:,0]`/`[0]` branches never fire). The displacement is therefore
**between the `ftle` data content and its own `lats` labels** — **baked into the
SPASSO product's grid registration**, not introduced by our code. The file carries
**no CRS, grid_mapping, or cell bounds** to derive a principled correction from, so
an empirical constant is the only lever.

## Fix

### 1. Latitude correction (primary)

Apply a documented empirical latitude correction where coords are assigned in
`_ftle.fetch_ftle`:

```python
FTLE_LAT_CORRECTION_DEG = -0.13   # SPASSO grid registers ~0.13 deg N of its
                                  # geophysical content; measured vs CMEMS land
                                  # mask (coast-correct), rigid & latitude-only,
                                  # constant across coast orientation, reproduces
                                  # across days. CI -0.08..-0.16 deg.
ftle = ftle.assign_coords(lat=("lat", lat1d + FTLE_LAT_CORRECTION_DEG),
                          lon=("lon", lon1d))
```

- **Latitude only** — no longitude term (west coast pins lon at ≈ 0).
- **Constant choice.** −0.13° is the midpoint of the well-conditioned south-coast
  estimates (−0.12…−0.14) and within everyone's CI; −0.12° is equally defensible.
  Tune empirically: rebuild, re-run the south-coast FTLE-low→CMEMS land-edge
  measurement, and adjust until it sits on the coast (~0). Confirm visually with
  the `coast_check` overlay.

### 2. Ocean clip (secondary, complementary)

After the shift, clip the FTLE to ocean before/at contouring so residual
high-FTLE coastal filaments don't render into land. Design choice (decide on
implementation): rasterize a bundled Natural-Earth land polygon onto the FTLE grid
and set land cells to NaN (keeps `_ftle` self-contained), **or** reuse the CMEMS
ocean mask already fetched in the build (couples FTLE to the currents step). The
former is cleaner architecturally; the latter reuses a field we already have and
that registers −0.01° vs Natural Earth.

## Validation

Rebuild (`pixi run build`), re-measure the south-coast FTLE-low→CMEMS land-edge
offset (should drop to ~0), and eyeball `coast_check` over the south and west
coasts. Confirm on-land contour vertices drop sharply. The currents and shading
need no change and should be re-confirmed unchanged.

## Open questions

1. **Confirm with the SPASSO authors.** This is their product's registration
   quirk (contact in the file: Louise Rousselet). A GEOMAR connection may make it
   quick to get the authoritative grid convention; ship the empirical −0.13° now
   and reconcile later.
2. **Lock the exact constant** (−0.12 vs −0.13°) from the post-rebuild re-measure.
3. **Ocean-clip implementation** — bundled coastline vs CMEMS-ocean reuse (above).
