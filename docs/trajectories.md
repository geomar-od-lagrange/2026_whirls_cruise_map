# trajectories

Each drifter's path over time, drawn as a line with a dot at every fix, so a
viewer can read where a drifter has been — not just where it is now.

## What is drawn

For every drifter with at least two valid fixes, the Trajectories layer draws:

- **a line** over its time-sorted positions, in the track colour (orange,
  distinct from the blue latest-position markers); and
- **a dot at every fix** along that line, in the same track colour.

A single-fix drifter has no line (a LineString needs ≥2 points) and so no dots;
it still shows its latest-position marker.

## Popups: every fix carries the marker's popup

Each dot — and the latest-position marker — opens the **same popup**, filled
with *that fix's* own data:

- **identity** (`D_number`), **last fix** time, **battery**;
- **velocity, derived and reported, side by side**;
- **position**.

The two velocity rows exist because the drifters' *reported* velocity columns
(`U_speed_mps` / `U_Dir_deg`) are unreliable, especially before deployment. So
the popup shows them next to a velocity **derived** from the track itself — the
mean speed and initial bearing of the segment from the previous fix to this one
— and lets the viewer compare the two rather than trusting either alone. A fix's
derived row is blank (`—`) when there is nothing to derive from: a track's first
fix, or a zero-length step. The latest-position marker derives from the
prior fix, so a single-fix drifter shows a blank derived row.

**Units.** Speeds read in **both knots and m/s** (`0.7 kn / 0.34 m/s`), and so
does the ship readout — the ship is nautical (knots), the drifters are
oceanographic (m/s), and showing both keeps every speed on the map comparable.
Directions are degrees true with a 16-point compass label; reported direction is
normalised into 0–360° for display.

## Data

The derivation happens in the **build**, not the client: the Python build has
the full time-sorted track DB, so it computes per-fix speed/heading once and
bakes them in. `tracks_geojson` writes one `LineString` per drifter with
properties:

- `D_number`, `batch`, `n_fixes`;
- `fixes` — a per-vertex list **aligned with `coordinates`**, each entry
  `{date_UTC, batteryState, U_speed_mps, U_Dir_deg, derived_speed_mps,
  derived_heading_deg}`.

`latest_geojson` carries the same per-fix payload in each Point's properties
(its latest fix, derived against the prior one). Non-finite cells are written as
`null`, never `NaN`, so the JSON parses client-side and the popup renders a dash.
The client reads `fixes[i]` for the dot at `coordinates[i]`; a `fixes`-less
artifact from an older build degrades gracefully (dots fall back to the
line-level identity with blank time/velocity).

## Control: coupled to the batch filter

Trajectories are governed by the **Drifters** control (top-right), not the
Leaflet layer control — the same control that filters batches (see
[batches.md](batches.md)). A master **Trajectories** checkbox turns the lines and
dots on or off for every batch at once; each batch's own checkbox turns that
batch's markers on or off. The two compose: **a batch's trajectory shows only
when both its batch row and the master Trajectories row are checked**, so
unchecking a batch hides its markers *and* its trajectory together. Markers start
visible; trajectories start hidden.

## Rendering and stacking order

The trajectory lines and dots draw **below** the latest-position markers, which
stay on top and clickable. The line is **non-interactive** — it carries no popup
and must not swallow a click meant for a dot or a marker. Dots are individual SVG
circle markers (each independently hit-testable).

The ship track and its per-fix dots sit **below the drifter markers** too, for a
specific reason: the cruise departs the drifters' staging port, so the early ship
track runs straight through the pre-deploy cluster. Were the ship dots painted
above the drifters (or on a map-wide canvas), they would intercept the clicks
meant for the drifter markers underneath. The ship's *current-position* marker
still sits on top. See [ship.md](ship.md).

## Performance

A dot per fix is cheap at current counts — drifters report sparsely, so each
track has few fixes. The ship, on a fixed 10-minute grid, accumulates many more
(hundreds over the cruise); its dots are plain SVG for the same
click-through reason, which is fine at cruise scale. If a future dense track
lags, decimate it — see the *Track thinning* backlog item.
