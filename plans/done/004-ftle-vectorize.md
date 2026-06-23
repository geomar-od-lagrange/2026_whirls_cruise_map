# FTLE overlay as vector contours

> **Implemented.** See [`docs/ftle.md`](../../docs/ftle.md) for the current state.
> One refinement on landing: a **single** level (0.40 of the p2..p98 range) was
> chosen over the two-level scheme below — the lower line already carries the
> Cape Basin structure, and a second level is left as an optional future add.

Replace the FTLE raster (003) with **vector contours** of the same SPASSO field:
a few nested iso-FTLE levels as GeoJSON line strings, styled red and graded by
level. The raster is a 2.6 MB alpha-ramped PNG that needs a manual Mercator warp
to register and blurs when zoomed; vectorizing answers both of this plan's
questions at once — *which geometry* (lines vs filled polygons) and *how to
compress without losing the filament detail*.

## Why vectorize at all

The current `ftle.png` is **2587 KB**, warped to Mercator by hand because
`imageOverlay` stretches an equirectangular raster wrong in latitude (the
`_raster` helper exists for exactly this). It is also a fixed-resolution image:
crisp at the default view, soft when zoomed into an eddy.

Vector contours fix all three:

- **Size:** 1–2 orders of magnitude smaller (numbers below).
- **No warp:** `L.geoJSON` takes lon/lat and Leaflet projects to Mercator
  itself — the FTLE layer stops needing `_raster` entirely (speed still uses it).
- **Crisp at every zoom**, and client-stylable/toggleable per level.

The one thing a raster gives that a contour set does not is a *continuous*
intensity ramp. We recover the useful part of that with 2–3 nested levels and
graded colour/opacity (faint wide ring = elevated FTLE, bright inner ring =
ridge crest).

## Source

Unchanged from 003: `_ftle.fetch_ftle(target)` already returns the smoothed
`(ftle_2d, valid_time)` over the Cape Basin box (0.01°, 1950×2260, day⁻¹,
backward FTLE). This plan only changes what we do *with* the field after fetch —
`fetch_ftle` is reused as-is.

## A. Geometry: line strings, not filled polygons

Both come from the **same `contourpy` machinery** (`.lines(level)` vs
`.filled(lo, hi)`), so this is a styling decision, not an architectural one.
Recommend **line strings**:

- **Filled red bands re-introduce the blanket problem 003 fought.** 003 tuned
  vmin/vmax to p2/p98 specifically so the red would not wash out the speed
  shading underneath. Filled polygons bring the opaque red back; lines leave the
  speed field visible between ridges.
- Lines are **~1.5–2× smaller** than the equivalent filled bands (a band is two
  nested rings plus holes; a level is one ring).
- The LCS idiom *is* a ridge line — a curve, not an area. Lines read as
  "transport barriers / eddy rims," which is what the layer is for.

True Hessian/eigenvector ridge extraction (the textbook LCS definition) is out
of scope: noisy on observational FTLE, finicky, and overkill for a rudimentary
cruise map. Iso-contours of the (lightly smoothed) field trace the same crests
well enough — confirmed in the preview.

Filled bands stay a one-line styling alternative if we want them later; the
build can emit either from the same extraction.

## B. Compression — the core question

Raw iso-contours are **huge** (the field is 4.4M cells): a single p90 level is
~225k vertices ≈ 9 MB of GeoJSON. The detail that matters is the *filament
geometry* (the meandering crest curves), not the smooth background or the
vertex-per-cell sampling density. So the pipeline throws away sampling redundancy
and noise while keeping the meanders. Four levers, measured on today's field
(2026-06-23), each line level as GeoJSON, gzipped (GitHub Pages serves gzip):

| stage | what it removes | p90 line | p97 line |
|---|---|---|---|
| raw contour | nothing | 9110 KB | 3568 KB |
| + Douglas–Peucker, tol 0.015° (~1.5 cells) | collinear/sub-cell vertices (~20×) | — | — |
| + round coords to 3 dp (~110 m) | float noise far below the 1.1 km cell (~2×) | — | — |
| + gzip | — | **48 KB** | **25 KB** |
| + length prune (drop rings < ~8 km) | speckle/noise, *improves* legibility | well under above | — |

So **2–3 nested line levels land at ~60–110 KB gzipped**, versus the 2587 KB
PNG — a 25–40× reduction *and* crisper. Filled bands (p90–p94 / p94–p97 /
p97–top) measured 75 / 50 / 14 KB gzipped, ~140 KB for all three — still ~18×
smaller, the fallback if we go filled.

These bytes were measured at global p90/p97; the **ramp-fraction levels chosen in
lever 1 sit lower**, so they yield more and longer contours and somewhat larger
files (the calibration's p85 line was 53 KB gzipped — still ~50× under the PNG).
Confirm final sizes in the preview once the levels are fixed.

The levers, and which are lossy:

1. **Level choice** — *the* signal decision, and where the Agulhas
   **retroflection** must not capture the layer. The retroflection + jet hold the
   strongest FTLE in the box, so **raw global high percentiles set the levels from
   that tail**: today's global p97 (0.329 day⁻¹) sits at ~0.95 of the p2..p98
   range, i.e. a p97 contour traces almost only the retroflection and starves the
   Cape Basin eddies the cruise actually targets. The raster (003) avoided this
   with its **p2/p98 contrast clip** — the extreme tail above p98 is clipped out
   of the normalization, and the low p2 floor keeps weak structure visible.
   **Apply the same normalization here:** clip to `[p2, p98]` and place levels at
   fixed *fractions of that clipped ramp*, not at raw percentiles — e.g. context
   at ~0.4 and crest at ~0.65 of `p2..p98` (today ≈ 0.14 and 0.23 day⁻¹), so the
   retroflection's heavy tail can't pull the levels up and the Cape Basin
   filaments stay above the context level. Store the resolved day⁻¹ values +
   their ramp fractions in meta; settle exact fractions in the preview.
2. **Douglas–Peucker tolerance** — lossy knob. 0.015° ≈ 1.5 source cells cut
   vertices ~20× with the filaments visibly intact (preview). Tighten toward
   ~1 cell if meanders look clipped; loosen for more compression.
3. **Length/area prune** — drops tiny rings. *Detail-preserving for the signal*:
   it removes noise specks, not coherent structures, so it shrinks bytes **and**
   declutters. Set conservatively (a few cells across).
4. **Coordinate rounding to 3 dp** — effectively lossless (110 m ≪ 1.1 km cell),
   halves bytes for free.

Light 3×3 smoothing before contouring (already a cheap `xarray.rolling`, no
scipy) removes per-cell jaggies so DP has less to do — keep it small.

**No new heavy dependency:** `contourpy` ships with matplotlib (already present)
and the DP simplify + length prune are ~15 lines of numpy (prototyped, works).
`shapely` would only be worth adding if we switch to filled polygons (robust
simplify of rings-with-holes). `topojson` is a further ~2× lever but needs a
client-side decoder — not worth it when GeoJSON+round3+gzip is already tens of KB.

## Artifacts (replace the raster pair)

- `ftle.geojson` — `FeatureCollection`, one `MultiLineString` Feature per level,
  with a `level` property (the day⁻¹ value) and an ordinal `rank` (0 = faint
  context … N = crest) for client styling. Coords rounded to 3 dp.
- `ftle_meta.json` — `valid_time`, `units`, and the ordered `levels` (value +
  colour stop) for the legend. **Drop `bounds`/`vmin`/`vmax`** — no overlay
  image to place, Leaflet derives extent from the geometry.
- **Delete `ftle.png`** and the FTLE branch's use of `_raster`.

## Rendering

- `L.geoJSON(ftle, { pane: "ftle", renderer: L.canvas(), style: byRank })`
  replaces the `imageOverlay`. Canvas renderer handles a few thousand vertices
  smoothly; SVG is also fine at these counts.
- `byRank`: red stroke, opacity/weight graded by `rank` (e.g. faint thin outer →
  bright slightly thicker crest), no fill. Pane z-index 360 (between speed and
  flow) is unchanged.
- Legend: the existing red-intensity strip, re-keyed to the discrete level
  values instead of a continuous vmin..vmax ramp. Valid-time line unchanged.

## Build

`_ftle.py`: replace `to_ftle_png` with `to_ftle_geojson(field, valid) ->
(geojson_dict, meta)` doing smooth → percentile levels → `contourpy.lines` →
DP simplify → length prune → round. `build.py`'s FTLE block writes
`ftle.geojson` + `ftle_meta.json` instead of the PNG; still best-effort and
independent of CMEMS. No new dependency.

## Validate before wiring

Per project habit (002/003 "confirmed in preview"): in `tmp_ftle_preview/`,
settle the **level set, DP tolerance, and prune length** visually over the speed
shading before editing `src/`. The calibration script and the raster-vs-vector
comparison (`vectorize_calib.py`, `vectorize_preview.py`) are already there; the
two-level p90+p97 preview is the current candidate.

## Out of scope

Filled-band styling (kept as a same-pipeline option), true Hessian ridge
extraction, FTLE time series/animation, TopoJSON encoding, vector tiles.

## Decisions

1. **Vectorize and drop the raster** — ship `ftle.geojson`, delete `ftle.png`
   and the FTLE use of `_raster` (speed keeps `_raster`).
2. **Line strings, not filled polygons** — keep the speed shading visible; filled
   is a same-pipeline fallback.
3. **Two nested levels** (context + crest) by default, third optional; placed at
   **fractions of the p2..p98 clipped ramp** (mirroring the 003 raster) so the
   Agulhas retroflection's tail doesn't capture them — *not* raw global
   percentiles. Exact fractions + tolerance + prune length settled in the preview.
4. **Dependency-free** — `contourpy` (have it) + numpy DP/prune; add `shapely`
   only if we switch to filled polygons.
5. Coordinates rounded to **3 dp**; rely on Pages gzip for the rest.