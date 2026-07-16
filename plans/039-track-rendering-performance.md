# 039 — Track rendering performance (the "Show tracks" lag)

## Why

After the deployment-focused refactor (plans 034/035) the map feels laggy. The
lag is **specific to the "Show tracks = on" state** — with tracks off, pan/zoom
and scrubbing are smooth. This plan records what was measured, the root cause,
and a ranked set of fixes (render-side and data-volume), with trade-offs, so we
can pick work deliberately. **Nothing here is implemented yet.**

### Deployment context

Production is the **OpenShift "whirlsview" stack** (`../oc_gateway/`), reached at
`https://whirlsview.geomar.de`: a gateway nginx proxies `/live/` (prod) and
`/live-test/` (staging) to per-instance **frontend nginx** pods that serve the
map's `site/` (shell baked into the image; `map/data/` served from a PVC filled
by build CronJobs), plus a forecast **API** pod for `/live/api/`. GitLab Pages
(`.gitlab-ci.yml`) is a separate **static-only fallback** — no API pod, so the
Deploy tool degrades there.

Crucially, the frontend nginx **already gzips** the GeoJSON
(`oc_gateway/deploy/_frontend/nginx.conf:39-45`: `gzip on`, `gzip_comp_level 5`,
`gzip_types … application/geo+json …`, `gzip_proxied any`; `.geojson` is typed
`application/geo+json` so it matches). So `tracks.geojson` crosses the wire at
**~2.45 MB (gzip-5), not 19 MB**. Compression is *not* a missing lever — which
reshapes the data-volume story below: the win is cutting the bytes the browser
must **decompress + parse + turn into 100k objects**, which gzip does nothing for.

It complements the two prior performance plans, which it partly *supersedes*:

- [`plans/performance.md`](performance.md) — cold-load / at-sea transfer
  profiling (serial fetches, Leaflet CDN risk, wasted tiles). Still open; those
  items are load-time, largely orthogonal to this render-time lag.
- [`plans/done/032-download-volume.md`](done/032-download-volume.md) — made
  `tracks.geojson` **lazy** and cropped coords to **4 dp**. **The 034/035
  refactor regressed both of these** (see Root cause). This plan restores the
  spirit of 032 for the new clock-following design.

## What renders when (the current pipeline)

`site/map/app.js`, `main()` (line ~3470):

**On load, tracks OFF (the smooth case):**
- `latest.geojson` (57 KB) → ~146 drifter `circleMarker`s (SVG). Cheap.
- `speed_*.webp` → one `imageOverlay` raster in the `shading` pane. Cheap.
- `gliders.geojson` (388 KB) → glider markers. Cheap.
- Drifter/glider "heads": one marker per instrument that follows the app clock.
- **But `tracks.geojson` (19 MB) is still eager-fetched, parsed, and turned into
  ~100k polyline objects off-map** (line 4063 → `buildTrackGroups` →
  `addTrackSegments`). The lines aren't in the DOM yet, so pan/zoom stays smooth,
  but this is a multi-second main-thread build + large memory/GC footprint right
  after load.

**"Show tracks" → ON** (`setTracksVisible` → `setInstrumentTracks` →
`buildInstrumentRows.sync()`, line 849): each batch's `featureGroup` is
`addTo(map)`, injecting **all of its per-segment polylines into the DOM at once**
→ ~100k SVG `<path>` nodes appear. This is the laggy state.

**Scrub** (`buildTimeSlider` `onChange` → `updateClock`, line 381): rAF-throttled
(good), but each frame loops **every track entry × every segment** — `clipTrack`
(line 569) walks all ~100k segments calling `setSegShown` (which does
`group.addLayer/removeLayer`, i.e. DOM mutation, when a segment crosses the
clock) plus a `setLatLngs` on each crossing segment. With tracks on, scrubbing is
continuous DOM churn across 146+ tracks.

## Root cause (measured)

1. **One `L.polyline` per fix-to-fix segment.** `addTrackSegments`
   (`app.js:715`) builds a separate polyline for every leg so each can carry its
   own hover tooltip (issue #11) and be clipped by segment membership (plan 035).
   `tracks.geojson` has **146 LineStrings / 100,549 vertices** → **~100,400
   segment polylines**. Gliders and both ship tracks use the same builder, adding
   more. Every one is an SVG `<path>` + a bound sticky tooltip + a click handler +
   a `registerPart` restyle closure.

2. **Default SVG renderer.** The map is created with no `preferCanvas`
   (`app.js:3481`) and no per-line `renderer:`, so all track lines render as SVG.
   ~100k `<path>` DOM nodes is the killer: zoom re-projects every path
   (`O(vertices)` `setAttribute("d", …)`), and the browser must lay out,
   composite, and hit-test 100k nodes on every interaction. Canvas draws the same
   lines as a handful of draw calls with **no DOM**.

3. **Eager load + eager build (regression vs 032).** Plan 032 made tracks lazy —
   fetched and built only when the layer was first switched on. Plan 035 reverted
   to eager because the drifter **heads now follow the clock from load** and need
   the per-drifter time series. But the head only needs *times + positions*, not
   the rendered polylines — so we pay the full 100k-polyline build up front even
   though the lines are hidden.

4. **19 MB payload, 89 % of it duplicated telemetry.** Measured on the committed
   artifact:

   | part | size | note |
   |---|---:|---|
   | `properties.fixes[]` (all 146 features) | **16.9 MB (89 %)** | per-fix `date_UTC, batteryState, U_speed_mps, U_Dir_deg, derived_speed_mps, derived_heading_deg` — one entry per vertex, feeds the per-leg hover tooltip. `date_UTC` (full ISO-8601 with offset, ~33 chars) is the single largest field |
   | geometry `coordinates` | 2.1 MB (11 %) | the actual track shape |
   | total | 19.0 MB | **~2.45 MB on the wire** (production nginx gzip-5); the browser still decompresses, parses, and builds objects from the full 19 MB |

   Emitter: `_geojson.tracks_geojson()` (`_geojson.py:242-304`); the per-vertex
   dual append (one coord **and** one `fixes` record per row) is `_geojson.py:288-291`.
   There is **no simplification/decimation anywhere** in the module. The refactor
   added the `fixes` array (~532 KB raw before → 19 MB now). Coordinates are
   already 4 dp (`_coord`, `_geojson.py:27`); **precision is not the problem**
   (see below). `gliders.geojson` (388 KB) carries the **same** duplicated
   per-vertex `fixes` pattern (`_geojson.py:190-193`, 3 fields/fix).

## Data-volume analysis (answering "binary? low precision? pixel in deg?")

**What is a pixel, in degrees?** At `maxZoom: 12`, Web Mercator, working latitude
≈ −37°:

- world width = `256 · 2¹²` = 1,048,576 px for 360°
- **longitude: 0.000343°/px (~30.5 m/px)**
- **latitude: 0.000274°/px (~30.4 m/px)**

So a pixel at the deepest zoom is **~0.0003° ≈ 30 m**.

**Cropped to low precision?** Coordinates are *already* 4 dp (plan 032) =
0.0001° ≈ 11 m ≈ **0.29 px** at max zoom — already sub-pixel and at the GPS noise
floor. 032 measured that cropping tracks to 4 dp saved only −6 % gzipped.
**Precision is a dead lever; vertex count and the telemetry are the levers.**

**Ranked data-volume levers (biggest first):**

1. **Stop shipping per-fix telemetry in the always-loaded track file** — it is
   16.9 MB / 89 %. The head/clip machinery needs only `[lng, lat, t]` per vertex.
   Move the hover telemetry out of the hot path (see recommendation C). This
   alone takes the eager payload from 19 MB → ~2.1 MB (geometry only), ~0.3 MB
   gzipped.

2. **Simplify the geometry (Douglas–Peucker at build time).** Drifter tracks are
   smooth; a tolerance of ~0.0005° (≈ 1.5 px at max zoom) is visually lossless
   and keeps only **14 %** of vertices:

   | DP tolerance | ≈ px @ z12 | vertices kept |
   |---|---:|---:|
   | 0.0005° (~56 m) | 1.5 | 14,499 (14 %) |
   | 0.001° (~111 m) | 3.0 | 10,137 (10 %) |
   | 0.002° (~222 m) | 6.0 | 7,159 (7 %) |

   Caveat: the clock-follows head interpolates along the vertices, so keep enough
   temporal resolution for smooth head-walking — simplify on space but never drop
   so much that a straight-but-slow leg loses its time samples. Even 14 % (≈15k
   vertices total) is ample. This cuts geometry ~2.1 MB → ~0.3 MB **and** cuts the
   rendered vertex/segment count ~7×.

3. **Wire compression is already handled — don't chase it.** The production
   frontend nginx gzips `application/geo+json` at level 5 (Deployment context
   above), so tracks cross the wire at ~2.45 MB. Bumping `gzip_comp_level` 5→6
   in `oc_gateway`'s frontend `nginx.conf` buys a little more (≈2.3 MB) for CPU;
   marginal. **The point of levers 1–2 is the client cost gzip can't touch** —
   the browser still inflates and parses the full 19 MB and builds 100k objects.
   (On the GitLab Pages fallback, compression is *not* configured — a `.gz`
   pre-emit in the build would help there — but that host is secondary and
   levers 1–2 make it moot.)

4. **Minify the JSON** (trivial). `json.dumps` uses default separators
   (`build.py:68`), so **1.41 MB (7.4 %)** of `tracks.geojson` is separator
   whitespace. `separators=(",",":")` reclaims it — mostly redundant under gzip,
   but it also shrinks the string the browser must parse. Free.

5. **Binary transfer** (typed arrays / delta-varint coordinates). After levers
   1+2 the geometry is ~15k vertices; as `Float32` deltas that is ~60–110 KB raw,
   vs ~0.3 MB JSON / ~80 KB gzipped-JSON. **Marginal over gzipped JSON** — worth
   it only if we decide to keep dense per-fix data client-side. Low priority.

**Net target:** telemetry-split + DP + gzip takes the eager track payload from
**19 MB → well under 300 KB on the wire**, and removes the 100k-object client
build — before any renderer change.

## Recommendations (ranked, biggest-impact / lowest-risk first)

### A. Render tracks on a Canvas renderer — the immediate fix

Give every track line a canvas renderer instead of SVG. Two ways:

- Minimal: `L.map("map", { …, preferCanvas: true })` — all vector layers
  (tracks, markers, heads) go to canvas. Verified safe: **no CSS depends on
  `.leaflet-interactive`/path DOM**. Watch the at-time/head `circleMarker`s
  behave (they use `setStyle`/`setRadius`, all canvas-supported).
- Safer/targeted: pass `renderer: L.canvas({ pane })` to the track polylines in
  `addTrackSegments` (one canvas per track pane preserves the z-order stack), and
  leave markers on SVG.

Canvas turns ~100k DOM nodes into a single per-pane redraw: pan/zoom becomes one
`clearRect` + redraw instead of re-projecting/compositing 100k `<path>`s.
**Expected to resolve the reported lag with a small, low-risk change**, while
preserving per-segment tooltips. Do this first.

Note: the canvas renderer still iterates every *layer* per redraw, so 100k tiny
polylines keep per-object overhead — which motivates B.

**Implemented (the targeted variant).** `trackRenderer(pane)` (`app.js`, near
`addTrackSegments`) memoizes one `L.canvas({ pane })`; the drifter + glider
segments pass `renderer: trackRenderer("overlayPane")`. Markers/heads stay SVG.

**Critical gotcha found while implementing — only ONE full-viewport track canvas
may exist.** A Leaflet canvas renderer's `<canvas>` spans the whole viewport and
hit-tests its entire rectangle regardless of transparency, so a *second* track
canvas stacked above the first **swallows every hover/click meant for the tracks
below it**. The first attempt also gave the ship tracks a canvas (in the
`shipTrack` pane, z 410, above the overlay-pane track canvas at 400) — that killed
all drifter/glider hover *and* click-to-highlight (verified: at every track pixel
the topmost element was the ship canvas). Fix: **ship tracks stay on SVG** (few
segments; SVG's per-path `pointer-events: visiblePainted` lets events fall through
its transparent areas to the track canvas below). Net: exactly one track canvas,
in `overlayPane`; everything above it (ship SVG, forecast/deploy SVG panes, marker
panes) either falls through or sits legitimately on top.

The canvas hover hit-test is throttled and skipped during drags, so it does **not**
tax panning; measured empty-area/drag `mousemove` cost ≈ 0 ms. (A one-time ~10 ms
on first-hover-onto-a-track is tooltip render, same as SVG.) So the O(N)-per-redraw
concern above is real for memory/GC but not for pan latency — B remains the
cleanup, not an urgent fix.

### B. Collapse to one polyline per track; clip via `setLatLngs`

Replace per-segment polylines with **one polyline per instrument** (146 drifters
+ gliders + 2 ships instead of ~100k objects). Clip exactly as `clipForecast`
already does (`app.js:629`): set the line's coords to `vertices ≤ clock` + the
interpolated at-clock point — no per-segment `addLayer/removeLayer`, no crossing
`setLatLngs` on a separate object. This:

- cuts object count ~700× (memory, GC, build time, per-redraw iteration),
- makes scrubbing ~150 `setLatLngs` calls per frame instead of ~100k
  `setSegShown` checks,
- removes the "add the whole featureGroup to the DOM" freeze on toggle-on.

Trade-off: per-*leg* hover tooltips are lost. Recover with one sticky tooltip per
track whose content updates on `mousemove` (canvas polylines fire `mousemove`
with a `latlng`; find the nearest fix and set the tooltip). This is a real design
change to `addTrackSegments` / `registerTrackClock` / `clipTrack` — do it after A
proves the renderer direction, and lands cleanly with canvas (A).

### C. Split geometry from telemetry; simplify at build time

Build-side, in `_geojson.tracks_geojson` (`_geojson.py:242-304`) and the shared
`gliders_geojson` (`_geojson.py:151-206`):

- Drop `properties.fixes[]` from `tracks.geojson`. Emit only what the clock needs
  per vertex — a parallel `times` array alongside `coordinates`. Use a **compact
  time encoding** (epoch seconds, or ms-offsets from the track's first fix), not
  the full ~33-char ISO string that dominates the current `fixes` record — so
  heads and clips work from a ~0.3 MB file.
- Move the hover telemetry (`batteryState`, `U_speed_mps`, `U_Dir_deg`) to a
  separate, **lazily fetched** payload (per-drifter, or one file fetched only when
  tracks are switched on / on first hover). `derived_speed_mps` /
  `derived_heading_deg` are computable client-side from consecutive coords+times,
  so they need not be shipped at all.
- Apply Douglas–Peucker (tol ≈ 0.0005°) to the emitted geometry, carrying the
  timestamp of each retained vertex.
- Minify the output (lever 4). Wire compression is already done by nginx.

`gliders.geojson` carries the **same** duplicated `fixes` pattern
(`_geojson.py:190-193`, confirmed) — give it the same treatment via the shared
emitter.

### D. Restore lazy build of the rendered lines (keep eager time series)

The head-follows-clock requirement (plan 035) needs the **time series**, not the
**rendered polylines**. Split the two:

- eagerly load the lightweight geometry+times (cheap after C) and register the
  head clips, so heads walk from load as today;
- build/attach the display polylines **lazily on first "Show tracks = on"**,
  restoring plan 032's deferral for the heavy part.

With A+B+C the eager build is already cheap (~150 polylines, ~0.3 MB), so D is a
smaller win than it was — take it only if profiling still shows a build hitch.

### E. Scrub-path micro-opts (minor)

`updateClock` is already rAF-throttled. After B its per-frame cost is trivial.
Also: `renderCurrentsInfo` rebuilds DOM on every slider `input` event
(`app.js:3844`) — coalesce to the rAF or to frame-index changes. The shading
`setUrl` swap is already guarded by frame-index change. Low priority.

## Also observed — not the cause of the lag

Recorded so the profiling pass is complete; none of these drive the tracks-on
lag, so treat them as separate cleanups.

- **WebP overlays are fine.** 159 files / **6.9 MB** total (53 frames × 3
  families at 12 h cadence, 541×481 px). They are lazy — only a ±8 band is
  prefetched, on first scrub — so they don't affect load or pan/zoom. speed/ζ·f
  are already 12-bin quantized (plan 032 lever 5); `flowvis` is unquantized line
  art at ~65 KB/frame (3.46 MB total) — the heaviest family if we ever want to
  trim, but not urgent.
- **Stale dead artifacts on disk.** `forecast.geojson` / `hindcast.geojson`
  (130 KB each) are leftovers — their generation was removed in plan 036 and the
  frontend never fetches them (real-drifter forecasts now come from
  `POST /api/forecast`). A clean CI build won't reproduce them; delete the local
  copies to avoid confusion. `inertial_field.json` (126 KB) is loaded but the
  overlay is disabled pending #25.
- **Load-time items from `performance.md` are still open** (serial `await` chain
  ≈ 8×RTT; no local Leaflet fallback). Orthogonal to this render-time lag, but
  they matter for the at-sea cold load — sequence them from that plan.

## Suggested sequencing

1. **A — canvas renderer.** Small, low-risk; expected to fix the felt lag on its
   own. Do this first and re-measure before anything heavier.
2. **C — build-side telemetry split + DP simplification.** Cuts the eager payload
   from 19 MB → < 300 KB (and the browser's decompress/parse/build cost, which
   nginx's gzip cannot), and the render/scrub vertex count ~7×.
3. **B — one polyline per track + `setLatLngs` clipping** (with the mousemove
   tooltip). Structural cleanup; removes the last of the per-object overhead.
4. **D / E** only if profiling still shows a hitch.

Wire compression is already in place (nginx gzip-5), so it is not on this list —
the levers above target the client-side render/parse cost that gzip doesn't
reach.

## Verification

- **Render:** with "Show tracks" on, DevTools Performance — a pan/zoom should
  drop from 100k-node SVG layout/paint to a single canvas redraw; target smooth
  interaction on a mid-range laptop with all batches shown.
- **Payload:** serve `site/` (`python -m http.server`), headless-Chrome load,
  assert `tracks.geojson` on the wire is < 0.3 MB (post-C) and — if D lands —
  not fetched until "Show tracks" is first toggled.
- **Correctness:** heads still walk the clock with tracks off; hover still shows
  the right fix (per-leg via canvas mousemove after B); clock clipping still
  trims at the scrubber; DP simplification is visually indistinguishable at every
  zoom.
- **Build:** `pixi run test`; regenerate `tracks.geojson` and confirm vertex
  count, absence of `fixes[]`, and size against the tables above.
