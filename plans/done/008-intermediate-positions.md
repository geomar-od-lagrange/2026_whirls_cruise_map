> **Implemented** → [docs/trajectories.md](../../docs/trajectories.md),
> [docs/ship.md](../../docs/ship.md), [docs/batches.md](../../docs/batches.md).
> Two things changed from the intent below as it was built: the Trajectories
> master toggle lives in the **Drifters control**, not the Leaflet layer control,
> so it composes with the per-batch checkboxes (unchecking a batch hides its
> trajectory); and the per-fix popups grew **reported + derived velocity** rows,
> with every speed shown in **both knots and m/s**. The ship track + dots also
> moved *below* the drifter markers (a `shipTrack` pane) so they can't intercept
> drifter clicks where the early cruise track crosses the staging cluster.

# Intermediate positions for drifters and the ship

Show the intermediate fixes that make up a trajectory, not just its latest
position, so a viewer can read where a platform has been — not only where it is.

## Drifters

The "Trajectories" overlay currently draws one orange `LineString` per drifter.
Add a small dot at every fix along that line, in the **track colour** (so the
dots read as part of the trajectory, distinct from the blue latest-position
markers). Each dot carries the **same popup as the drifter's main marker**,
populated with *that fix's* `D_number` / time / battery / position. The dots
live in the Trajectories overlay so they toggle with the lines.

This needs per-fix data the current `tracks.geojson` does not carry: the
`LineString` has coordinates but no per-vertex time/battery. Enrich
`tracks_geojson` to emit a `fixes` array parallel to `coordinates`, each entry
`{date_UTC, batteryState}`. The client reads `fixes[i]` for the dot at
`coordinates[i]`. Stay robust to a `fixes`-less artifact (older build): fall
back to the line-level `D_number`/`batch` with an unknown time.

## R/V Marion Dufresne

The ship layer draws a cased polyline plus a boat marker at the latest fix. Add
a small dot at **each 10-minute fix** along the track, in the ship-track colour.
Each dot carries the **same popup as the latest position** (`shipPopupHtml`),
populated with that fix's met data and a per-segment derived speed/heading.

Per-fix motion means deriving speed/heading from each fix and its predecessor,
not just the last segment. Extract `motionBetween(prev, cur)` and have
`deriveMotion` (latest segment) call it; reuse it per dot.

The dots are maintained alongside the polyline: a full rebuild on
`setPositions`, an incremental add per fresh fix on `append`, so live polling
keeps appending only the new tail. Use a canvas renderer — the track can reach
hundreds of fixes over the cruise.

## Heading row: always shown, NA when speed is too low

Today the ship popup/sidebar **drops** the "Heading (derived)" row when the
heading is suppressed (speed below `MIN_HEADING_KN`, or no prior fix). Change
the readout to **always** show a Heading row, with value `NA` when no heading is
available. The row is shared by popup and sidebar via `shipRows`, so the change
is one place. Speed still drops when underived; only heading is forced.

## Out of scope

- Per-fix popups for drifters showing derived velocity (the `U_*` columns are
  unreliable; deriving from fixes is a separate BACKLOG item).
- Track thinning/decimation (BACKLOG) — at current fix counts the dots are
  cheap; revisit if a dense track lags.
