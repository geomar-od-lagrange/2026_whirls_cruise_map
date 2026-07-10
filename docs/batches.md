# Drifter batches

Drifters are grouped into deployment **batches**. Every drifter fix carries a
`batch` string (set in `_clean.py`, propagated into `latest.geojson` and
`tracks.geojson`), and the map offers a checkbox panel to show or hide each
batch independently.

## What a batch is

A batch labels a set of drifters by when/how they entered the water. Before a
drifter is deployed it sits in the staging batch `pre_deploy`; during the cruise
drifters move to deployment batches as they go overboard.

## Batch source: the deployment roster

The *assignment* of drifters to deployment batches lives in
`src/whirls_cruise_map/deployments.json` — a `batch → [D_number, …]` roster
curated per deployment. `_clean.load_deployments()` inverts it to a
`D_number → batch` map and `_clean.clean` stamps every fix accordingly; any
drifter absent from the roster stays `pre_deploy`. Assignment is per drifter
(the whole track), so a deployment batch also covers the drifter's transit leg
from port — the batch is an identity of the drifter set, not a per-fix state.

A drifter joins a deployment batch once it is confirmed in the water and
drifting freely — not merely because it has a recent fix. Adding a deployment is
a data-only edit to `deployments.json` (add the next `deployment_N` key); no code
change.

### Criterion for "deployed and drifting"

A drifter is confirmed in the water — and so eligible for a deployment batch —
when all of the following hold. These separate a freely-drifting drifter from one
still on deck (stationary) or still aboard during transit (ship-speed motion):

- **Drifting freely now** — its latest fix is recent (within ~30 min), so the
  behaviour is current, not stale.
- **Drifted away from the ship** — it started near the vessel and its distance to
  the vessel has been increasing; it is separating, not riding along on deck.
- **Sustained realistic drift** — it has drifted for at least an hour at speeds
  consistent with the surface currents (order 0.1–1 m/s, reaching ~1.5–1.7 m/s in
  the region's fast Agulhas-current jets), not near-zero (on deck) and not
  ship-transit fast (several m/s).

The check is done by hand against the live positions, the derived per-segment
speeds, and the R/V Marion Dufresne track (see [ship.md](ship.md)); the resulting
roster is what lands in `deployments.json`. Examples so far: **Deployment 1** —
20 drifters released and drifting with the currents; **Deployment 2** — a later,
smaller batch (3 drifters) that had separated from the ship and drifted steadily
for over an hour; **Deployment 3** — a further 3 drifters (D-566, D-567, D-582),
each confirmed separated from the vessel (2.9–11.6 km) and drifting at surface-current
speed (~0.5–0.7 m/s) at assignment; **Deployment 4** — an array staged together on
the Marion Dufresne and left in the water as the ship departed station, seeded with
the two drifters (D-433, D-434) confirmed separated (21–44 km) and drifting (~0.2 m/s)
first, to be extended as the array's other staged drifters report their own free
drift; **Deployment 5** — a large overnight array of 70 drifters that went into the
water between 2026-07-09 21:29 and 2026-07-10 05:30 UTC, each confirmed separated
from the vessel (17–67 km) and drifting steadily at surface-current speed (~0.3–1.7
m/s) by assignment. Drifters that merely reported the same day while stationary on
deck, or that showed only brief ship-transit motion, stayed `pre_deploy`.

## GUI: the instrument filter

An **"Instruments"** control sits at the top-right of the map. It lists one
checkbox per instrument — each drifter batch present in `latest.geojson` **and**
each glider platform (XSPAR buoy, seagliders; see [gliders.md](gliders.md)) — with
a colour swatch (a round one for drifter batches, the instrument colour for
gliders) and the row's marker count. Unchecking a box removes that instrument's
markers; rechecking restores them. Deployment batches and gliders start visible;
the staging `pre_deploy` batch starts **hidden** (the drifters are still aboard,
not in the water), so the map opens on the deployed drifters — recheck its row to
show the staged ones.

The control is **data-driven**: it builds one `L.featureGroup` per distinct
instrument key — a drifter `batch`, or a glider `type` — and renders a row for
each. A new batch or a new glider therefore appears automatically once the data
contains it — no client code change. Known keys get friendly labels
(`pre_deploy` → "Drifter pre", `deployment_1` → "Drifter batch 1", the gliders →
"XSPAR buoy" / "Seagliders"); any other key is shown verbatim, so an unanticipated
instrument is still legible.

Master **overlay** rows head the list — **True track**, **Forecast** and
**Hindcast** — each with a short line swatch in its own colour (the marker swatches
below key each instrument to its markers; the line swatches key overlays to their
lines). A horizontal divider separates these line rows from the instrument (marker)
rows below. Each overlay row turns its lines and dots (see
[trajectories.md](trajectories.md), [forecast.md](forecast.md)) on or off for every
instrument at once, and composes with the per-instrument rows: an instrument's
overlay shows only when both its own row and that overlay's row are checked. So
unchecking an instrument hides its markers *and* its trajectory, forecast and
hindcast together. All overlays start hidden. (Gliders ride the True-track,
Forecast and Hindcast overlays too; a glider with a single fix has no track, only
its marker.)

This control — not a Leaflet layer control — governs all instrument visibility
(markers and the trajectory/forecast/hindcast overlays). The field layers and the
vessels live in two separate titled Leaflet layer controls stacked below it:
**Currents** and **Ships** (the two vessels). In Currents the two shadings (speed,
ζ/f vorticity) are **mutually-exclusive radios** — plus a *None* radio to turn
shading off — since both fill the one `shading` pane; the flow trails and
near-inertial animation, which coexist with either shading, are **checkboxes**.
The three controls share one `.map-control` style so they read as one set, and
each is built only when it has something to show (no dead, empty box).

### Why a custom control, not the Leaflet layer control

The Leaflet layer control toggles whole named overlays. Modelling each batch as
its own overlay there would work, but it mixes per-batch drifter filtering in
with the basemap/overlay choices and offers no room for the per-batch swatch and
count. A small dedicated control keeps batch filtering visually distinct and
leaves space to grow (per-batch colour, highlight-on-hover) as batch assignment
lands.

### Per-batch colour

`styleForBatch()` is the single seam for per-batch appearance, and the swatches
in the control follow whatever it returns. Staged drifters (`pre_deploy`) render
muted grey; each deployment batch has its own vivid colour (Deployment 1 blue,
Deployment 2 teal, Deployment 3 orange, Deployment 4 purple, Deployment 5 magenta),
so in-water drifters stand out from those still awaiting deployment and successive
deployments read apart. Colours live in `BATCH_STYLES` in `app.js`; a further
deployment with no entry falls back to blue until given one.
