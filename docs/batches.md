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
`D_number → batch` map and `load_raw` stamps every fix accordingly; any drifter
absent from the roster stays `pre_deploy`. Assignment is per drifter (the whole
track), so a deployment batch also covers the drifter's transit leg from port —
the batch is an identity of the drifter set, not a per-fix state.

A drifter joins a deployment batch once it is confirmed in the water and
drifting freely — not merely because it has a recent fix. Deployment 1, for
example, is the 20 drifters that were released and drifting with the surface
currents; three others that reported the same day while still stationary on deck
stayed `pre_deploy` until they too drift. Adding a future deployment is a
data-only edit to `deployments.json` (add a `deployment_2` key); no code change.

## GUI: the batch filter

A "Drifters" control sits at the top-right of the map. It lists one checkbox per
batch present in `latest.geojson`, each with a colour swatch and the batch's
marker count. Unchecking a box removes that batch's markers; rechecking restores
them. All batches start visible.

The control is **data-driven**: it builds one `L.featureGroup` per distinct
`batch` value found in the data and renders a row for each. A new batch therefore
appears automatically once the data contains it — no client code change. Known
keys get friendly labels (`pre_deploy` → "Pre-deployment"); any other key is
shown verbatim, so an unanticipated batch is still legible.

A master **Trajectories** checkbox heads the list. It turns the track lines and
per-fix dots (see [trajectories.md](trajectories.md)) on or off for every batch
at once, and composes with the per-batch rows: a batch's trajectory shows only
when both its batch row and the Trajectories row are checked. So unchecking a
batch hides its markers *and* its trajectory together. Trajectories start hidden.

This control — not the Leaflet layer control — governs all drifter visibility
(markers and trajectories); the layer control retains the speed, flow, FTLE, and
ship overlays.

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
muted grey; every deployment batch gets a vivid blue, so in-water drifters stand
out from those still awaiting deployment. Distinct per-deployment colours
(`deployment_2`, …) are a one-entry addition to `BATCH_STYLES` in `app.js`.
