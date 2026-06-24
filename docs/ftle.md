# FTLE / LCS ridge overlay

The map overlays a **backward FTLE** (finite-time Lyapunov exponent) ridge contour
to expose **Lagrangian coherent structures** — eddy rims, filaments and transport
barriers — the mesoscale "whirls" the cruise targets. High backward-FTLE marks
attracting structures; a single iso-FTLE line traces their crests.

## Source

The WHIRLS cruise's own **SPASSO v2.1** product on IPSL THREDDS (public, no auth),
a daily **00Z** field over the Cape Basin box (lon 5.70..28.29 E, lat
−44.50..−25.01 S, 0.01°, units day⁻¹), fetched over OPeNDAP. The build picks the
file whose 00Z time is nearest the currents valid-time and within 24 h, else the
nearest available day; if none qualifies the layer is simply absent (best-effort,
independent of the CMEMS step).

## Latitude registration correction

The SPASSO product registers **~0.13° of latitude north of its actual geophysical
content**: the `ftle` array sits ~13 rows north of its own (otherwise pristine)
0.01° `lats` labels. `fetch_ftle` therefore applies a documented empirical
`FTLE_LAT_CORRECTION_DEG = -0.13` to the latitude coordinate; longitude is left
untouched.

This is a real, rigid, latitude-only shift baked into the product — not a
coordinate-assignment bug and not a measurement artifact. The file's `lats`/`lons`
are clean linspaces matching its own `geospatial_*` attributes to the digit, and
they arrive as 1-D arrays that `assign_coords` promotes faithfully, so the
displacement is between the data content and its own labels (the file carries no
CRS, grid_mapping or cell bounds to correct from principled-ly, hence the empirical
constant). The magnitude was pinned by registering the low-FTLE land-like region
against the coast-correct CMEMS land mask on coast segments of differing
orientation: the offset is a **constant northward vector** regardless of coast
orientation — the south coast, which pins latitude, gives −0.12…−0.14°; the west
coast, which pins longitude, gives ≈0 — the signature of a rigid field shift rather
than a coast-perpendicular skirt, and it reproduces across days. (An open-ocean
FTLE-vs-CMEMS structural cross-check is too insensitive to localize a ~14 km shift,
since backward-FTLE ridges track the flow's history rather than the instantaneous
speed field, so it neither confirms nor refutes — the geographic land-mask
registration is the basis for the constant.) The speed shading and flow trails are
independently verified correctly registered and need no such correction.

The field is **not** ocean-masked, so a few genuine high-FTLE near-coast filaments
still cross the coastline after the shift (~1.6% of contour vertices, the deepest
~30 km inland); masking the field to ocean before contouring would remove them
(see `plans/BACKLOG.md`).

## Representation: one vector contour, not a raster

The overlay is a **single iso-FTLE line contour** shipped as GeoJSON, not a shaded
raster. Three choices define it:

- **Vector, not raster.** Leaflet projects the lon/lat geometry to Web Mercator
  itself, so the layer needs no manual Mercator warp (unlike the speed shading).
  It stays crisp at every zoom and is far smaller: ~205 KB GeoJSON / **~53 KB
  gzipped**, versus ~2.6 MB for the equivalent alpha-ramped PNG. The cost is the
  loss of a continuous intensity ramp; a single well-placed crest line reads the
  coherent structures clearly enough for a rudimentary map, and more levels can be
  added (graded colour) if intensity cueing is wanted.
- **Lines, not filled polygons.** Both come from the same contouring; lines keep
  the surface-speed shading underneath **visible** between ridges, where filled
  red bands would re-create the opaque blanket the shading is tuned to avoid.
- **One level, placed within the clipped range.** The level sits at **0.40 of the
  p2..p98 range** of the field, not at a raw high percentile. The Agulhas
  retroflection and jet hold the strongest FTLE in the box, so a raw global p90+
  level traces almost only them and starves the Cape Basin eddies. Clipping to
  p2..p98 and taking a fraction of *that* range (mirroring the contrast clip a
  raster would use) keeps the level low enough to catch basin structure while the
  extreme tail can't pull it up. Today's level ≈ 0.13 day⁻¹.

## Compression pipeline

Raw iso-contours of a 4.4 M-cell field are huge (~100 k+ vertices, multiple MB).
The build keeps the filament *geometry* and discards sampling redundancy and
noise:

1. **Light 3×3 smooth** (`xarray.rolling`) so the contour has fewer per-cell
   jaggies to simplify.
2. **Extract** the level with `contourpy` (ships with matplotlib).
3. **Douglas–Peucker simplify**, tolerance ≈ 0.015° (~1.5 cells) — removes
   collinear/sub-cell vertices (~20×) while keeping meanders.
4. **Length prune** — drop rings shorter than ~8 km; these are noise specks, so
   pruning shrinks the file *and* declutters the signal.
5. **Round coordinates to 3 dp** (~110 m, well below the 1.1 km cell) — effectively
   lossless, roughly halves the bytes; GitHub Pages gzip does the rest.

Today: 903 ridge lines, ~11.5 k vertices. The lossy knobs (level fraction, DP
tolerance, prune length) trade size against filament fidelity; coordinate rounding
is the free lever. No new dependency — `contourpy` is already present and the
simplify/prune are a few lines of numpy.

## Client rendering

`app.js` loads `ftle.geojson` into `L.geoJSON` on a **canvas renderer** in the
`ftle` pane (z-index 360 — above the speed shading, below the flow trails, below
the drifter markers), styled as thin red lines. On by default, toggleable in the
layers control. The sidebar shows the field's valid-time and a legend keyed to the
level value, both data-driven from `ftle_meta.json`.

## Artifacts

- `ftle.geojson` — one-feature `FeatureCollection` (a `MultiLineString`) with the
  ridge lines; the feature carries `level` (day⁻¹), `frac` and `rank` properties.
- `ftle_meta.json` — `valid_time`, `units` and the ordered `levels`
  (value + ramp fraction + colour) for the legend.
