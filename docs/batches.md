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
  consistent with the surface currents (order 0.1–1 m/s here), not near-zero
  (on deck) and not ship-transit fast (several m/s).

The check is done by hand against the live positions, the derived per-segment
speeds, and the R/V Marion Dufresne track (see [ship.md](ship.md)); the resulting
roster is what lands in `deployments.json`. Examples so far: **Deployment 1** —
20 drifters released and drifting with the currents; **Deployment 2** — a later,
smaller batch (3 drifters) that had separated from the ship and drifted steadily
for over an hour. Drifters that merely reported the same day while stationary on
deck, or that showed only brief ship-transit motion, stayed `pre_deploy`.

## GUI: the batch filter

A "Drifters" control sits at the top-right of the map. It lists one checkbox per
batch present in `latest.geojson`, each with a round colour swatch and the batch's
marker count. Unchecking a box removes that batch's markers; rechecking restores
them. Deployment batches start visible; the staging `pre_deploy` batch starts
**hidden** (the drifters are still aboard, not in the water), so the map opens on
the deployed drifters — recheck its row to show the staged ones.

The control is **data-driven**: it builds one `L.featureGroup` per distinct
`batch` value found in the data and renders a row for each. A new batch therefore
appears automatically once the data contains it — no client code change. Known
keys get friendly labels (`pre_deploy` → "Pre-deployment"); any other key is
shown verbatim, so an unanticipated batch is still legible.

Master **overlay** rows head the list — **True track**, **Forecast** and
**Hindcast** — each with a short line swatch in its own colour (the round swatches
below key batches to their markers; the line swatches key overlays to their lines).
A horizontal divider separates these line rows from the batch (marker) rows below.
Each overlay row turns its lines and dots (see [trajectories.md](trajectories.md),
[forecast.md](forecast.md)) on or off for every batch at once, and composes with
the per-batch rows: a batch's overlay shows only when both its batch row and that
overlay's row are checked. So unchecking a batch hides its markers *and* its
trajectory, forecast and hindcast together. All overlays start hidden.

This control — not the Leaflet layer control — governs all drifter visibility
(markers and the trajectory/forecast/hindcast overlays); the layer control
retains the speed, flow, and ship overlays.

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
Deployment 2 teal), so in-water drifters stand out from those still awaiting
deployment and successive deployments read apart. Colours live in `BATCH_STYLES`
in `app.js`; a further deployment with no entry falls back to blue until given
one.
