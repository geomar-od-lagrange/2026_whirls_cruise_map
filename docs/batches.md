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
drift; **Deployment 5** — a large overnight array that went into the
water between 2026-07-09 21:29 and 2026-07-10 05:30 UTC, each confirmed separated
from the vessel (14–67 km) and drifting steadily at surface-current speed (~0.3–1.7
m/s) by assignment (a handful included with a last fix ~1 h old — an irregular
reporting gap, not a behavioural one; and five later additions still opening up —
D-492 and D-505 at ~8 km, then D-487, D-514 and D-524 caught mid-separation and
already 11–12 km out — each on ~30–40 min of clean ~1.2 m/s free drift that had
opened from <0.2 km an hour earlier); **Deployment 6** — eight drifters (D-535,
D-548, D-558, D-561, D-563, D-579, D-584, D-605) laid along the ship's track
through 2026-07-16 (reported water entry 08:36–20:00 UTC), each confirmed
separated from the vessel and drifting steadily at surface-current speed
(~0.3–0.75 m/s) for 11–22 h by assignment. The algorithmic free-drift detachment
(see [deploy_tool.md](deploy_tool.md)) landed within +5 to +47 min of each reported entry,
and the drifters are now 38–167 km astern — furthest for the earliest-laid, the
ship having steamed on down its track between drops. Drifters that merely reported
the same day while stationary on deck, or that showed only brief ship-transit
motion, stayed `pre_deploy`.

## GUI: the instrument filter

An **"Instruments"** control sits at the top-right of the map. It lists one
checkbox per instrument in three families read top-to-bottom, each a **two-column
grid** set off by a divider: the **drifter batches** present in `latest.geojson`,
the **glider platforms** (Glider, Float, XSPAR, Waveglider; see
[gliders.md](gliders.md)), and the two **ships** (M. Dufresne, Agulhas II — the
former separate Ships tab, folded in here). Each row carries a colour swatch (round
for drifter batches and ships, a diamond for gliders) and — for the instruments — the
row's instrument count (the number of platforms in that group; the fixed deployment
dot each instrument also carries is not counted). Unchecking a box removes that instrument's markers;
rechecking restores them. Deployment batches and gliders start visible; the staging
`pre_deploy` batch starts **hidden** (the drifters are still aboard, not in the water),
so the map opens on the deployed drifters — recheck its row to show the staged ones.
A small **select all / deselect all** text control at the bottom drives every row at
once (firing each row's real handler, so markers and tracks reconcile).

The control is **data-driven** for the drifter/glider rows: it builds one
`L.featureGroup` per distinct instrument key — a drifter `batch`, or a glider `type` —
and renders a row for each. A new batch or a new glider therefore appears automatically
once the data contains it — no client code change. Known keys get friendly labels
(`pre_deploy` → "batch X", `deployment_1` → "batch 1", the gliders → "XSPAR" /
"Glider"); any other key is shown verbatim, so an unanticipated instrument is still
legible. The two ship rows are rendered eagerly from config (the vessels are known up
front); a ship row toggles its vessel's visibility, but the vessel only joins the map
once its first fix lands, so toggling before then is safe and applied on the fix.

The Instruments control carries **only marker rows** — one per drifter batch,
glider platform, and vessel. The observed *track lines* are governed elsewhere: a
single **Show tracks** master in the time-slider (scrubber) box shows or hides every
observed track line at once (see [trajectories.md](trajectories.md)). The two still
compose: an instrument's track shows only when both its own marker row here **and**
the "Show tracks" master are on, so unchecking an instrument hides its markers *and*
its trajectory together. (Gliders and ships ride the same "Show tracks" master; a
platform with a single fix has no track, only its marker.)

This control — not a Leaflet layer control — governs instrument (and vessel) marker
visibility; it and the **Currents** control are two tabs of the one top-right
[control dock](controls.md). In Currents the two shadings (speed, ζ/f vorticity) are
**mutually-exclusive radios** — plus a *None* radio to turn shading off — since both
fill the one `shading` pane; the flow overlay and near-inertial animation rows are
also present but currently rendered greyed-out and inert (issue #25).

### Why a custom control, not the Leaflet layer control

The Leaflet layer control toggles whole named overlays. Modelling each batch as
its own overlay there would work, but it mixes per-batch drifter filtering in
with the basemap/overlay choices and offers no room for the per-batch swatch and
count. A small dedicated control keeps batch filtering visually distinct and
leaves space to grow (per-batch colour, highlight-on-hover) as batch assignment
lands.

### Per-batch colour

`styleForBatch()` is the single seam for per-batch appearance, and the swatches in
the control follow whatever it returns. Staged drifters (`pre_deploy`) render a
muted grey; each deployment batch takes the next step along the active palette's
**drifter ramp** — an ordinal light-to-dark ramp in one hue family (warm
amber→crimson under the default `ember` palette), so successive deployments read
apart *and* the whole set reads as one drifter family. The ramp and every other
instrument colour are defined once in the palette (`BATCH_STYLES` is built from it);
a further deployment past the ramp falls back to the first step until the ramp is
extended. See [palette.md](palette.md) for the full scheme, the shading-clash and
colour-blindness rationale, and the `?palette=` switch.
