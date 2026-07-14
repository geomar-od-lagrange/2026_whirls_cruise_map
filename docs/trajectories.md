# trajectories

Each drifter's **free-drift path** over time — drawn as a line, so a viewer can
read where a drifter has drifted, not just where it is now. Hovering anywhere along
the line shows that leg's fix. Every observed track line — drifter, glider, and ship
— is shown or hidden together by the single **Show tracks** master in the time-slider
box (see *Control* below).

## What is drawn

For every drifter with at least two free-drift fixes, the tracks layer draws
**a line** over its time-sorted positions, in the track colour (orange, distinct
from the blue latest-position markers). The line is built as one polyline **per
fix-to-fix segment** (see *Tooltips* below) rather than a single stroke, so the
whole line is a hover target — but the segments abut into one continuous track.

For a **deployed** drifter only the **free drift** is drawn: the path is
truncated at its deployment (see *Truncation at deployment* below), so the
port-staging and transit legs — where it was still on the vessel — are excluded.
A **pre-deployment** drifter keeps its **full track** (it has no free drift to
isolate, and its whole path — port, on deck — is what a viewer wants). A drifter
with fewer than two drawn fixes (single-fix, or a deployed one still on the
vessel) has no line; it still shows its latest-position marker.

## The app clock clips the track — and moves the head

Tracks follow the map's single clock (the bottom-centre scrubber; see
[currents.md](currents.md)): at clock *t* a track draws only the fixes at or
before *t* — whole segments toggled in or out of their group, the crossing
segment trimmed to the interpolated at-clock position — and the instrument's
**latest-position head marker rides the clipped end**, carrying the bracketing
fix's tooltip. Past the track's last fix the full track shows and the head parks
at the latest position, so an untouched clock at load reads as a plain
latest-positions map; before its first fix the instrument hides entirely — it
wasn't in the water yet, so scrubbing across the cruise makes drifters appear as
they are deployed. A **single-fix** instrument (one fix, no LineString — e.g. D-509)
follows the same rule with no line: it hides before its fix and parks at its latest
position after, so it too rides the clock rather than sitting on the map at every
clock position. A clock-hidden head wins over the selection restyle
(`_clockHidden`), so a selection change can't resurface an instrument that
doesn't exist at the displayed instant.

Every head follows the clock from the start, because every track's time series is on
the client at load: the glider tracks ride the eagerly-fetched `gliders.geojson`, the
ship tracks load with their layers, and the drifter tracks come from `tracks.geojson`,
fetched **once at startup** (off the critical path — see *Control* below). The
per-track at-time dots of plan 034 are gone: the moving heads replace them (two
markers on the same moving spot would z-fight), and only the virtual deployment
tracks keep an at-time marker — it is their only head ([deployment.md](deployment.md)).

## Tooltips: every fix shows the marker's info on hover

The track carries no separate dot markers; instead **each line segment** carries a
hover tooltip, so hovering a leg of the track — and hovering the latest-position
marker — shows the **same tooltip**, filled with *that fix's* own data:

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
The client tags segment `i` (from `coordinates[i]` to `coordinates[i+1]`) with
`fixes[i]`, so hovering a leg shows the fix at its start; the **last** fix is not
a segment start but is covered by the latest-position head marker, so every fix
stays reachable on hover. A `fixes`-less artifact from an older build degrades
gracefully (segments fall back to the line-level identity with blank time/velocity).

## Truncation at deployment

`tracks_geojson` keeps only a **deployed** drifter's **free drift**: it takes a
per-drifter deployment start (`{D_number: first-free-drift time}` from `_deploy`)
and drops every earlier fix. Pre-deployment drifters are exempt — they keep their
full track. `latest_geojson` is untouched — it keys
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

## Control: one master in the scrubber, composed with the instrument filter

There is a single **Show tracks** master for every *observed* track line — drifter,
glider, and ship together — and it lives in the **time-slider (scrubber) box** at the
bottom of the map, not in the Instruments control (see [controls.md](controls.md)).
It sits there because these tracks clip to the app clock the scrubber drives. When
there is no currents field (hence no scrubber), the master falls back to a standalone
chip in the same spot.

The **Instruments** control (see [batches.md](batches.md)) carries only per-instrument
marker rows. The two compose: **an instrument's track shows only when both its own
marker row and the "Show tracks" master are on**, so unchecking an instrument hides its
markers *and* its track together. Markers start visible.

The drifter lines are **fetched eagerly**: `tracks.geojson` is fetched once at startup,
off the critical path, so every drifter head follows the app clock from the start
(the glider tracks ride `gliders.geojson`, already fetched for the markers). If
`tracks.geojson` is missing, the master still governs the glider and ship tracks.

The gliders' tracks share this observed-track layer, drawn in the same orange
`TRACK_COLOR` so every past track reads as one layer — the instrument identity
stays on the coloured diamond marker, not the track (see [gliders.md](gliders.md)).

## Selection: click a track to highlight it

Clicking any part of an instrument — its **line** (any segment) or its
**latest-position head marker** — selects that instrument: its line **brightens**
(to a lighter orange) and thickens, its head enlarges, and its line segments are
**raised in front of every other track** (`bringToFront`), while **every other
instrument desaturates** — greyed rather than faded, so it recedes without
vanishing. One track lifts out of the overlapping tangle while the rest stay
legible. The raise stops at the track layer: the selected track still draws
*below* the latest-position heads and the ship (which live in higher panes), so
it comes forward among the tracks without ever hiding a marker. Clicking the
selected instrument again, or clicking the empty map, clears the selection.

Selection spans **every instrument that carries a track** — the drifters *and* the
gliders (seagliders, the XSPAR) — since all their tracks share the one orange
`TRACK_COLOR`. Only the ship tracks are excluded (they are not registered).

The mechanism is a small registry keyed by instrument (a drifter `D_number` or a
glider `id`). Each element *registers a restyle callback* as it is built — in
`buildTrackGroups`/`buildBatchGroups` (drifters) and
`buildGliderTrackGroups`/`buildGliderMarkerGroups` (gliders) — and selects its
instrument on click; `applySelection` calls every registered callback with the new
state (`"selected"` / `"dim"` / `"normal"`). A callback per element, rather than a
shared restyler, lets each element kind render each state its own way: SVG line
segments via `setStyle` (colour swapped for the brighter or the desaturated
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
Click and hover are cleanly separated: **hovering** a line segment or head shows
that fix's tooltip (see above), while **clicking** selects the instrument — the tooltip is
non-interactive (`pointer-events: none`), so it never intercepts the click.
Selection also composes with the empty-map clear, which works because the track
elements set `bubblingMouseEvents: false`, so only genuine background clicks reach
the map's `click` handler.

## Rendering and stacking order

The trajectory lines draw **below** the latest-position markers, which stay on
top. The line **is interactive** — hovering a segment shows that fix's tooltip and
clicking any segment selects the drifter. The track is drawn as one polyline per
fix-to-fix segment (see `addTrackSegments`); the segments share endpoints and one
style, so they read as a single continuous line while each stays independently
hit-testable for its own tooltip. There are **no separate dot markers**, so
nothing sits over the line to intercept a click meant for it.

**Line weight scales with zoom** so the tracks read well at every scale. Zoomed
out, overlapping tracks blur together, so lines are **thin**; they thicken a step
at the finer zoom levels. The selected track keeps a fixed extra weight on top of
whatever the zoom sets, so it reads as picked at any scale. `trackWeight` is a
pure function of the live zoom (`trackZoom`, kept current by a `zoomend` handler
that re-runs `applySelection`); the single `lineStyle` reads it, so drifter and
glider tracks scale identically. The latest-position heads and the ship track do
not scale — only the track lines.

**Every line/track pane sits below every marker pane** — the governing rule of the
stack. Bottom to top: the raster/animation underlays (`shading` 350, `inertial`
360); then the line panes — observed drifter/glider tracks in Leaflet's default
`overlayPane` (400), the `shipTrack` (410), the violet real-drifter forecast lines
(`driftForecast` 420, see below), and the PoC deploy tool's drift lines and drop
discs (`deployTracks` 430, `deployDrops` 440); then the marker panes — the glider
diamonds in Leaflet's default `markerPane` (600), the drifter heads (`drifters`
650), the vessel markers (`ship` 660), the moving at-time heads (`atTime` 670), and
finally `tooltipPane` (680) / `popupPane` (700). Because no line pane reaches 600,
no marker is ever occluded by a track: the MD ship track can't paint over a
seaglider diamond, and no line intercepts a click meant for a marker. This also
keeps the earlier rationale intact — the cruise departs the drifters' staging port,
so the early ship track runs through the pre-deploy cluster, and keeping it below
every marker means its dots never intercept a click meant for a drifter or glider.
See [ship.md](ship.md).

### Violet forecast lines for the real deployed drifters

Every in-water drifter (its `latest.geojson` head whose batch is a `deployment_*`,
so gliders/floats/XSPAR/waveglider are excluded) gets a **violet forecast track**
(`#7c3aed`, `driftForecast` pane): its last observed fix advected forward through
the CMEMS field to the end of the data period. These are computed by the **same
`/api/forecast` endpoint the deploy tool uses** — one asynchronous POST fired after
the map is up, seeded from each drifter's last fix (`lon, lat, start`), advected
server-side. Each returned track is keyed back to its drifter by seed `index`.

Rather than drawn full and always-on, each forecast is **clock-clipped and
future-only** — it is the continuation of the drifter's observed track past *now*:

- it shows **only when the scrubber is past now**, drawn from now up to the clock
  position (a single non-interactive violet polyline, clipped by `setLatLngs`);
- the **drifter's own head marker walks the forecast** as the clock advances into the
  future (its observed head parks at the last real fix; past now the forecast takes over
  — see `clipForecast`, run last in `updateClock` so it wins the head). The marker walks
  the forecast **regardless of the "Show tracks" master**, like the observed heads;
- the **line** is what the **"Show tracks" master** governs: with tracks off the violet
  line is hidden, but the marker still moves along the (hidden) forecast into the future;
- the line also follows the drifter's **Instruments** batch checkbox — unchecking a
  batch drops its forecast lines along with that batch's markers and observed tracks
  (`forecastBatchVisible`); the marker is the batch's own head, so it hides with the batch.

The call is best-effort: it is never awaited, so it never blocks map init, and it is
gated on the dynamic API being reachable — a static-only deploy with no `/api/forecast`
server simply shows no violet forecasts (no error). A drifter whose last fix predates
the loaded field window is skipped server-side and gets no forecast.

## Performance

One polyline per segment means a track of *n* fixes is *n − 1* small polylines
rather than one — roughly the layer count the per-fix dots used to add, traded for
the dots, so it is cheap at current counts (drifters report sparsely, so each
track has few fixes). The ship, on a fixed 10-minute grid, accumulates many more
(hundreds over the cruise); its own per-fix dots stay plain SVG below the drifter
markers for the click-through reason above, which is fine at cruise scale. If a
future dense track lags, decimate it — see the *Track thinning* backlog item.
