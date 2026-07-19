# trajectories

Each drifter's **free-drift path** over time — drawn as a line, so a viewer can
read where a drifter has drifted, not just where it is now. Hovering anywhere along
the line shows that leg's fix. Every observed track line — drifter, glider, and ship
— is shown or hidden together by the single **Show tracks** master in the time-slider
box (see *Control* below).

## What is drawn

For every drifter with at least two free-drift fixes, the tracks layer draws
**a line** over its time-sorted positions, in that drifter's **identity colour** —
the same colour as its batch marker and moving head, so its line, deployment dot,
and head all read as one instrument (see [palette.md](palette.md)). The line is
built as **one multi-part polyline for the whole track** (see *Tooltips* below and
*Performance*), drawn on a shared canvas renderer rather than one path per
fix-to-fix segment — the whole line is still a single hover target, and a
blanked de-spike gap (see *Hiding GPS outliers*) simply splits it into disjoint
parts with no line drawn across the gap.

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
tracks keep an at-time marker — it is their only head ([deploy_tool.md](deploy_tool.md)).

## Tooltips: every fix shows the marker's info on hover

The track carries no separate dot markers; instead each track's line carries **one
sticky tooltip** for the whole line, whose content is resolved on hover to the
fix nearest the cursor rather than being bound per segment. So hovering anywhere
along the track — and hovering the latest-position marker — shows the tooltip,
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

## Hiding GPS outliers (client-side)

Alongside the build-time deployment despike (a different mechanism — see
[data.md](data.md)), the client can hide **out-and-back GPS spikes** in the
tracks it already has, using the per-fix derived speeds baked into `fixes`. A
fix is flagged as a spike when the implied speed is anomalous on **both** the
segment arriving at it and the segment leaving it — a genuine fast leg trips
only one side, so a real manoeuvre is never flagged. The threshold is a fixed
`OUTLIER_SPEED_MPS = 5`, well above the drift regime (well under 2 m/s); a real
spike implies speeds far higher still. A track's first and last fix are never
flagged (no incoming or no outgoing segment to test), and neither is a fix with
a null derived speed.

This is controlled by the **"Hide GPS outliers"** checkbox in the Instruments
control (see [controls.md](controls.md)), on by default. With it on, the flagged
fixes are dropped from the drawn track and its clock entries alike, so the
cleaned path is what the head follows too, not just what is drawn. Dropping a
fix leaves a gap between its two now-adjacent kept neighbours: if they are no
more than `OUTLIER_MAX_GAP_MS` (24 h) apart, the gap is bridged with a straight
segment; beyond that the segment is left blank instead, splitting the track's
multi-part polyline so no line is drawn across an unknowably long span.
Toggling the checkbox rebuilds every observed track in place for the new state.

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
The client keeps `fixes` aligned with `coordinates` and, on hover, resolves the
cursor position to the **nearest fix vertex** in the track (not a segment lookup),
so the tooltip always shows a real fix's data; near a leg boundary that nearest
vertex may be either endpoint of the hovered leg. The latest-position head marker
covers the last fix, so every fix stays reachable on hover.

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

Clicking any part of an instrument — its **line** (anywhere along it) or its
**latest-position head marker** — selects that instrument: its line **brightens**
(to a lighter orange) and thickens, its head enlarges, and its line is
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
shared restyler, lets each element kind render each state its own way: the canvas
track line via `setStyle` (colour swapped for the brighter or the desaturated
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
Click and hover are cleanly separated: **hovering** a line or head shows
that fix's tooltip (see above), while **clicking** selects the instrument — the tooltip is
non-interactive (`pointer-events: none`), so it never intercepts the click.
Selection also composes with the empty-map clear, which works because the track
elements set `bubblingMouseEvents: false`, so only genuine background clicks reach
the map's `click` handler.

## Rendering and stacking order

The trajectory lines draw **below** the latest-position markers, which stay on
top. The line **is interactive** — hovering anywhere along it shows the nearest fix's
tooltip and clicking it selects the drifter. The track is built as one multi-part
polyline for the whole instrument (`addTrack`), not one polyline per fix-to-fix
segment, so it reads as a single continuous line (split only at a blanked
de-spike gap) and is hit-tested as one shape by the shared canvas renderer.
There are **no separate dot markers**, so nothing sits over the line to
intercept a click meant for it.

**Line weight scales with zoom** so the tracks read well at every scale. Zoomed
out, overlapping tracks blur together, so lines are **thin**; they thicken a step
at the finer zoom levels. The selected track keeps a fixed extra weight on top of
whatever the zoom sets, so it reads as picked at any scale. `trackWeight` is a
pure function of the live zoom (`trackZoom`, kept current by a `zoomend` handler
that re-runs `applySelection`); the single `lineStyle` reads it, so drifter and
glider tracks scale identically. The latest-position heads and the ship track do
not scale — only the track lines.

**Every line/track pane sits below every marker pane** — the governing rule of the
stack. Bottom to top: the raster/animation underlays (`shading` 350, `flow` 355,
`inertial` 360); then the line panes — observed drifter/glider tracks in Leaflet's default
`overlayPane` (400), the `shipTrack` (410), the real-drifter forecast lines
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

### Forecast lines for the real deployed drifters

Every in-water drifter (its `latest.geojson` head whose batch is a `deployment_*`,
so gliders/floats/XSPAR/waveglider are excluded) gets a **forecast track** in the
`driftForecast` pane: its last observed fix advected forward through the CMEMS field
to the end of the data period. The forecast is drawn in the drifter's **own identity
colour** and styled exactly like its observed track, so the two read as one path
(line = dot = head; see [palette.md](palette.md)). These are computed by the **same
`/api/forecast` endpoint the deploy tool uses** — one asynchronous POST fired after
the map is up, seeded from each drifter's last fix (`lon, lat, start`), advected
server-side. Each returned track is keyed back to its drifter by seed `index`.

Rather than drawn full and always-on, each forecast is **clock-clipped and
future-only** — it is the continuation of the drifter's observed track past *now*:

- a **reporting-lag bridge** spans the gap between the drifter's last transmitted fix
  and *now* (the advected vertices that predate now), drawn **dashed** in the same
  identity colour — the dash, not a colour change, is what marks where the observed
  track hands off to the forecast;
- the solid **forecast** shows from *now* up to the clock position (clipped by
  `setLatLngs`) as the scrubber moves into the future;
- the **drifter's own head marker walks** the bridge then the forecast as the clock
  advances; a small **now-ghost** dot is left at the drifter's now-position once the
  clock passes now, so its present position stays marked while the bright head walks on
  (see `clipForecast`, run last in `updateClock` so it wins the head). The head and
  ghost follow the clock **regardless of the "Show tracks" master**, like the observed
  heads;
- the **lines** (bridge + forecast) are what the **"Show tracks" master** governs:
  with tracks off they are hidden, but the head still walks the (hidden) path;
- the lines also follow the drifter's **Instruments** batch checkbox — unchecking a
  batch drops its forecast along with that batch's markers and observed tracks
  (`forecastBatchVisible`); the marker is the batch's own head, so it hides with the batch.

The call is best-effort: it is never awaited, so it never blocks map init, and it is
gated on the dynamic API being reachable — a static-only deploy with no `/api/forecast`
server simply shows no forecasts (no error). A drifter whose last fix predates the
loaded field window is skipped server-side and gets no forecast.

## Performance

The drifter and glider tracks render on a shared **canvas** renderer, and each
track is **one multi-part polyline per instrument** rather than one polyline per
fix-to-fix segment. Cost scales with the number of *instruments*, not the number
of fixes: a few dozen canvas paths draw in one redraw with no DOM, and — the part
that matters for interaction — Leaflet only has to re-project and re-stroke those
few dozen paths on `zoomend`, instead of one path per fix across the whole fleet.
The alternative — one polyline per fix-to-fix segment — puts `~100k` segments across
the fleet through individual reprojection on every zoom, which is what makes zooming
stutter; one polyline per instrument is what a canvas renderer is fast at.

Only one full-viewport track canvas can exist without one swallowing the other's
hit-testing, so the ship track stays on plain SVG (few segments per ship, and it
needs to sit in its own pane below the drifter/glider markers — see *Rendering
and stacking order*); it is fine at cruise scale. If a future dense track lags,
decimate it — see the *Track thinning* backlog item.
