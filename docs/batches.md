# Drifter batches

Drifters are grouped into deployment **batches**. Every drifter fix carries a
`batch` string (set in `_clean.py`, propagated into `latest.geojson` and
`tracks.geojson`), and the map offers a checkbox panel to show or hide each
batch independently.

## What a batch is

A batch labels a set of drifters by when/how they entered the water. Before a
drifter is deployed it sits in the staging batch `pre_deploy`; during the cruise
drifters move to deployment batches as they go overboard. The *assignment* of
drifters to deployment batches is supplied per deployment and is resolved during
the cruise — until then `_clean.py` assigns every drifter `pre_deploy`.

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

### Deferred: per-batch colour

`styleForBatch()` currently returns a single marker style for all batches, so the
swatches are uniform today. It is the single seam for per-batch appearance: when
batch assignment is wired up, colour differentiation is a one-function change
there, and the swatches follow automatically.
