# 030 — Deploy tool: CSV / paste import of spatial waypoints
> Implemented. See [docs/deploy_tool.md](../../docs/deploy_tool.md) — importing a deployment from pasted or uploaded spatial waypoints.

## Goal

Let a user seed a deployment from the **vessel's route as a list of waypoints** they
already have (a cruise plan, a spreadsheet, a pasted block), instead of only by
clicking the path on the map. The waypoints are the *ship route*; the drops — hence
the number of drifters — are derived by resampling that route at the **Drop spacing**
knob and staggering by **Ship speed**, exactly as the clicked path does. The start
time comes from the **time scrubber** (the displayed field's `valid_time`).

## Decisions

**Upload *and* mask, one parser.** The question "CSV upload or input mask?" is a
false choice: a file picker and a paste box differ only in where the text comes
from. So the UI is a **textarea (the mask)** as the source of truth, plus a **file
picker** whose only job is to read a `.csv` into that textarea. One `parseWaypoints`
runs on the textarea for both. This makes the pasted-block workflow (what the user
did in chat) first-class, round-trips the Download CSV, and needs no file-format
branch.

**No CSV-parser dependency.** The format is `lon,lat` decimal degrees — a handful
of lines. A hand-rolled tolerant parser (~30 lines) is smaller than wiring a
library, and keeps the client free of a new CDN `<script>` (the map already pulls
Leaflet + leaflet-velocity from a CDN, but the offline-VSAT / future-CSP concerns in
[deploy_tool.md](../../docs/deploy_tool.md) argue against adding
more). PapaParse would be the pick if we needed quoting/streaming/type inference; we
don't. Noted as the alternative, not chosen.

**Rows are the vessel route, resampled like a clicked path.** The imported waypoints
are the ship's track, identical to a clicked polyline, so the tool **resamples them at
the Drop spacing knob** into equally-spaced drops — the **number of drifters follows
from the route length and spacing**, not the number of waypoints. "Ship speed"
staggers each drop's water-entry time along the route, and "Forecast (h)" applies. The
CSV path is therefore the click path fed as text: it calls the same `placeDeployment`,
so all downstream behaviour (forecast, export, highlight, clear) is shared for free.

**Format.** Decimal degrees, negative = S/W. Tolerant:
- blank lines and `#` comments skipped;
- delimiter comma / semicolon / whitespace / tab;
- a header row (any non-numeric token) maps columns by name — `lat*`/`lon*|lng` —
  so the Download CSV (`…,latitude,longitude,…`) round-trips directly;
- headerless rows are `lon,lat` (GeoJSON x,y and the seed object's key order);
- rows that aren't two finite numbers in range are skipped and counted.

DMS (`12° 15.6' E`) is **not** parsed in this prototype — decimal only. Noted as a
follow-up; the chat waypoints are converted to decimal for the test fixture.

## Implementation

Client-only (`site/map/app.js`, `style.css`), no backend change — the API already
takes arbitrary `(lon, lat, start)` seeds.

1. **Refactor** `placeDeployment` → extract `commitDeployment(routeVertices, drops,
   totalKm, …)` (everything from seed-building onward: seeds, waypoint registry,
   ship track, over-cap guard, POST, draws). `placeDeployment` becomes resample +
   commit — click behaviour byte-identical, and reused as-is for imports.
2. `parseWaypoints(text)` → `{ latlngs, skipped, error }`.
3. `buildDeployTool(deployLayer, getStartTime)` — thread a `getStartTime()` closure
   (`() => displayedFieldTime`) from `main()` so the import button reads the live
   scrubber time (clean plumbing, no new global). Add an **Import** sub-section to
   the Deploy tab: textarea + "Load file…" (hidden `<input type=file>`) + "Place
   from CSV". Place → `parseWaypoints` → `placeDeployment` (the parsed rows are the
   route, resampled at the spacing knob — no dedicated import commit path needed).

## Test

Use the 12 chat waypoints (converted to decimal, `lon,lat`, with a header) as a
vessel-route fixture. Pasting/loading them and clicking Place must resample the
~185 km route at the spacing knob into drops, staggered from the scrubber time, and
(API up) draw the drift lines. Verify locally with `pixi run serve` +
`pixi run serve-api`.

## Docs

Add an "Importing a deployment" section to
[deploy_tool.md](../../docs/deploy_tool.md) (the export section's
inbound twin). Move this plan to `done/` when merged.
