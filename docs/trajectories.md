# trajectories

Each drifter's **free-drift path** over time — its "true track" — drawn as a line
with a dot at every fix, so a viewer can read where a drifter has drifted, not
just where it is now. The layer is labelled **True track** in the control.

## What is drawn

For every drifter with at least two free-drift fixes, the True track layer draws:

- **a line** over its time-sorted positions, in the track colour (orange,
  distinct from the blue latest-position markers); and
- **a dot at every fix** along that line, in the same track colour.

For a **deployed** drifter only the **free drift** is drawn: the path is
truncated at its deployment (see *Truncation at deployment* below), so the
port-staging and transit legs — where it was still on the vessel — are excluded.
A **pre-deployment** drifter keeps its **full track** (it has no free drift to
isolate, and its whole path — port, on deck — is what a viewer wants). A drifter
with fewer than two drawn fixes (single-fix, or a deployed one still on the
vessel) has no line and so no dots; it still shows its latest-position marker.

## Tooltips: every fix shows the marker's info on hover

Each dot — and the latest-position marker — shows the **same tooltip on hover**,
filled with *that fix's* own data:

- **identity** (`D_number`), **last fix** time, **battery**;
- **velocity, derived and reported, side by side**;
- **position**.

The two velocity rows exist because the drifters' *reported* velocity columns
(`U_speed_mps` / `U_Dir_deg`) are unreliable, especially before deployment. So
the tooltip shows them next to a velocity **derived** from the track itself — the
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
`null`, never `NaN`, so the JSON parses client-side and the tooltip renders a dash.
The client reads `fixes[i]` for the dot at `coordinates[i]`; a `fixes`-less
artifact from an older build degrades gracefully (dots fall back to the
line-level identity with blank time/velocity).

## Truncation at deployment

`tracks_geojson` keeps only a **deployed** drifter's **free drift**: it takes a
per-drifter deployment start (`{D_number: first-free-drift time}` from `_deploy`)
and drops every earlier fix. Pre-deployment drifters are exempt — they keep their
full track. `latest_geojson` and the forecast/hindcast are untouched — all key
off the latest fix, which is post-deployment.

`_deploy.deployment_starts` detects deployment as **detachment from the vessel**,
using the R/V Marion Dufresne track fetched at build time (`_ship`, the same
source the client polls live; see [ship.md](ship.md)). For each fix it takes the
great-circle distance to the vessel (position interpolated to the fix time) and
walks the fixes in time order. A fix within 1 km (`NEAR_SHIP_KM`) marks the
drifter **attached** (on deck / alongside); the scan stops at the drifter's
first **clear departure** — consecutive fixes beyond 5 km (`DETACHED_KM`) after
it has been attached. Once that far out it is deployed for good and the cut is
frozen — the ship works among its own drifters routinely (station-keeping,
recovery, deploying the next batch nearby), and a later close pass must not
blank an established free track. Two kinds of distance noise are inert: far
fixes *before* the first near one (drifters sit in the staging port days before
the vessel arrives, so a far pre-history does not mean deployed), and a lone
far fix amid near ones (a GPS outlier must not end the attached leg early and
leak the remaining transit into the free track — hence *consecutive*). The cut
sits **after the last attached fix** before the departure — conservative on
purpose: the exact deployment instant does not matter, but not leaking a
vessel-following fix into the free track does. Everything kept up to the freeze
is beyond 1 km of the vessel; after the freeze, fixes are kept regardless of
distance (a later close pass is part of the free drift). A drifter that has
never cleanly departed and is within 1 km at its latest fix has no free track
yet (drawn as nothing); one never within 1 km keeps its full track; and if the
vessel fetch fails, no track is truncated (full tracks). Because the cut
discards each track's pre-deployment predecessor, the first free fix derives its
velocity from nothing and shows a blank derived row — correctly, as its real
predecessor was a vessel-following fix.

Detection itself is purely geometric (distance to the vessel, batch-agnostic),
but whether a drifter is *truncated* depends on its deployment batch: only
drifters in a deployment batch are cut to their free drift; `pre_deploy` drifters
keep the full track regardless. So the roster (see [batches.md](batches.md))
decides *who* is truncated and the detection decides *where* — the roster drives
batch colour/filtering, the detection supplies the cut point.

## Control: coupled to the instrument filter

True tracks are governed by the **Instruments** control (top-right), not the
Leaflet layer control — the same control that filters drifter batches and glider
platforms (see [batches.md](batches.md)). A master **True track** checkbox turns
the lines and dots on or off for every instrument at once; each instrument's own
checkbox turns that instrument's markers on or off. The two compose: **an
instrument's track shows only when both its own row and the master True track row
are checked**, so unchecking an instrument hides its markers *and* its track
together. Markers start visible; tracks start hidden.

Because the True-track overlay defaults off and `tracks.geojson` is the heaviest
data artifact, its drifter lines are **not fetched at load**: the first tick of the
master **True track** checkbox fetches `tracks.geojson` once, builds the lines, and
merges them into the overlay — so a viewer who never opens the tracks pays none of
those bytes. The glider tracks ride `gliders.geojson` (already fetched for the
markers), so they appear the instant the master row is checked, even before the
drifter fetch resolves. If `tracks.geojson` is missing, the toggle is a no-op for
drifters and still governs the glider tracks.

The gliders' tracks share this **True track** layer, drawn in the same orange
`TRACK_COLOR` so every past track reads as one layer — the instrument identity
stays on the coloured diamond marker, not the track (see [gliders.md](gliders.md)).

## Selection: click a track to highlight it

Clicking any part of an instrument — its **line**, one of its **dots**, or its
**latest-position head marker** — selects that instrument: its line **brightens**
(to a lighter orange) and thickens, its head enlarges, and its line and dots are
**raised in front of every other track** (`bringToFront`), while **every other
instrument desaturates** — greyed rather than faded, so it recedes without
vanishing. One track lifts out of the overlapping tangle while the rest stay
legible. The raise stops at the track layer: the selected track still draws
*below* the latest-position heads and the ship (which live in higher panes), so
it comes forward among the tracks without ever hiding a marker. Clicking the
selected instrument again, or clicking the empty map, clears the selection.

Selection spans **every instrument that carries a track** — the drifters *and* the
gliders (seagliders, the XSPAR) — since all their tracks share the one orange
`TRACK_COLOR`. Only the ship tracks are excluded (they are not registered). The
current-advection forecast/hindcast lines are not part of it either.

The mechanism is a small registry keyed by instrument (a drifter `D_number` or a
glider `id`). Each element *registers a restyle callback* as it is built — in
`buildTrackGroups`/`buildBatchGroups` (drifters) and
`buildGliderTrackGroups`/`buildGliderMarkerGroups` (gliders) — and selects its
instrument on click; `applySelection` calls every registered callback with the new
state (`"selected"` / `"dim"` / `"normal"`). A callback per element, rather than a
shared restyler, lets each element kind render each state its own way: SVG
lines/dots via `setStyle` (colour swapped for the brighter or the desaturated
tone), and the gliders' `divIcon` heads via `setIcon` (a CSS class scales the
selected diamond; the dim fill is desaturated in the icon HTML).

Dimming is by **desaturation, not transparency**: `desaturate()` mixes a colour
toward its own luminance-grey, leaving opacity untouched, so a de-emphasised track
stays readable against the basemap. The selected track's brighter `SELECTED_COLOR`
and the desaturated others are the two ends of that treatment.

Restyling happens **in place** and mutates each layer's options (`setStyle` /
`setIcon`), so the styling survives a batch toggle's remove/re-add: a hidden track
is restyled too and shows correctly the moment its instrument is re-enabled. The
front-raise is the one part that is *draw order*, not an option, so it is not
carried by a remove/re-add — it is simply reapplied by the next `applySelection`
pass (any selection change or zoom), which is when a re-enabled selected track
returns to the front.
Click and hover are cleanly separated: **hovering** a dot or head shows that fix's
tooltip (see above), while **clicking** selects the instrument — the tooltip is
non-interactive (`pointer-events: none`), so it never intercepts the click.
Selection also composes with the empty-map clear, which works because the track
elements set `bubblingMouseEvents: false`, so only genuine background clicks reach
the map's `click` handler.

## Rendering and stacking order

The trajectory lines and dots draw **below** the latest-position markers, which
stay on top. The line **is interactive** — it selects its drifter on click (it
carries no tooltip). Because the line and its dots resolve to the same drifter,
there is no click for the line to "swallow": whichever the pointer lands on, the
result is the same selection. Dots are individual SVG circle markers (each
independently hit-testable, and each also bearing that fix's hover tooltip).

**Line weight and dot radius scale with zoom** so the tracks read well at every
scale. Zoomed out, overlapping tracks blur together, so lines are **thin** and the
per-fix **dots are hidden** (radius 0); the dots reappear only at the finest four
zoom levels (`DOT_MIN_ZOOM = MAX_ZOOM − 3`), where there is room for them, and the
lines thicken a step. The selected track keeps a fixed extra weight (and slightly
larger dots) on top of whatever the zoom sets, so it reads as picked at any scale.
`trackWeight`/`dotRadius` are pure functions of the live zoom (`trackZoom`, kept
current by a `zoomend` handler that re-runs `applySelection`); the single
`lineStyle`/`dotStyle` pair reads them, so drifter and glider tracks scale
identically. The latest-position heads and the ship track do not scale — only the
track lines and their fix dots.

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
