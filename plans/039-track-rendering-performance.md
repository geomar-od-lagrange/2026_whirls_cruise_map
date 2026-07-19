# 039 — Track rendering performance (the "Show tracks" lag)

## Why

After the deployment-focused refactor (plans 034/035) the map felt laggy. The
lag was **specific to the "Show tracks = on" state** — with tracks off, pan/zoom
and scrubbing stayed smooth. This plan recorded what was measured, the root
cause, and a ranked set of fixes (render-side and data-volume), with
trade-offs.

**Status: the render-side fixes (A and B, below) have landed** — tracks now
draw on a canvas renderer as one polyline per instrument. **The data-volume
fixes (C and D) are still open** and are the real remaining levers: the eager
payload is still the full per-fix `fixes[]` array, still un-simplified, still
fetched and parsed up front. This plan stays open for C/D.

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

- [`plans/performance.md`](done/performance.md) — cold-load / at-sea transfer
  profiling (serial fetches, Leaflet CDN risk, wasted tiles). Still open; those
  items are load-time, largely orthogonal to this render-time lag.
- [`plans/done/032-download-volume.md`](done/032-download-volume.md) — made
  `tracks.geojson` **lazy** and cropped coords to **4 dp**. **The 034/035
  refactor regressed both of these** (see Root cause). This plan restores the
  spirit of 032 for the new clock-following design.

## What renders when (the current pipeline)

`site/map/app.js`, `main()`:

**On load, tracks OFF (the smooth case):**
- `latest.geojson` (57 KB) → ~146 drifter `circleMarker`s (SVG). Cheap.
- `speed_*.webp` → one `imageOverlay` raster in the `shading` pane. Cheap.
- `gliders.geojson` (388 KB) → glider markers. Cheap.
- Drifter/glider "heads": one marker per instrument that follows the app clock.
- **But `tracks.geojson` (19 MB) is still eager-fetched and parsed** (`app.js`,
  the `fetchJSON(DATA.tracks, { optional: true })` call feeding
  `buildInstrumentRows`) and turned into one polyline per instrument via
  `addTrack` (`app.js:926`). The lines aren't necessarily in the DOM yet (see
  below), so pan/zoom stays smooth, but this is still a fetch + JSON-parse +
  object-build pass over the full per-fix payload right after load — see item 2
  below.

**"Show tracks" → ON/OFF**: each instrument's track is now a *single*
`L.polyline` (built once by `addTrack`, one multi-part line per instrument —
gaps from de-spiked outliers are drawn as disjoint parts via `splitAtGaps`),
rendered on one shared `L.canvas` renderer in the `overlayPane`
(`trackRenderer`, `app.js:838-839`). Toggling visibility adds/removes ~150
polyline objects, not ~100k `<path>` DOM nodes.

**Scrub** (`updateClock`, rAF-throttled): clips each track's polyline to the
clock via `setLatLngs` rather than the old per-segment `addLayer`/`removeLayer`
churn — a few hundred `setLatLngs` calls per frame across all instruments, not
one check per fix.

## Root cause (as originally measured, and what changed)

The two render-side items below (1 and 2) have been fixed — recorded here for
the diagnosis trail. Items 3 and 4 are unchanged and remain the open levers
(see Recommendations C and D).

1. **One `L.polyline` per fix-to-fix segment — fixed (Recommendation B).** The
   original design (`addTrackSegments`, no longer present) built a separate
   polyline for every leg so each could carry its own hover tooltip and be
   clipped by segment membership. `tracks.geojson` has **146 LineStrings / ~100k
   vertices**, so this was ~100k SVG `<path>` nodes. See Recommendation B for
   what replaced it.

2. **Default SVG renderer — fixed (Recommendation A).** Tracks now render on a
   dedicated canvas renderer instead of SVG. See Recommendation A for the
   implementation and a hover/hit-testing gotcha found along the way.

3. **Eager load + eager build — still open (item D below).** Plan 032 made
   tracks lazy — fetched and built only when the layer was first switched on.
   Plan 035 made it eager because the drifter **heads now follow the clock
   from load** and need the per-drifter time series. The head only needs
   *times + positions*, not the rendered polylines, so the app still pays a
   full fetch + parse + polyline-build over the entire `tracks.geojson` up
   front even though the lines may be hidden. With the render-side fix (1/2
   above) this build is far cheaper than it was (~150 polylines, not ~100k),
   but the eager **payload** (item 4) is unchanged.

4. **19 MB payload, 89 % of it duplicated telemetry — still open (item C
   below).** Unchanged since this plan was first written. Measured on the
   committed artifact:

   | part | size | note |
   |---|---:|---|
   | `properties.fixes[]` (all 146 features) | **16.9 MB (89 %)** | per-fix `date_UTC, batteryState, U_speed_mps, U_Dir_deg, derived_speed_mps, derived_heading_deg` — one entry per vertex, feeds the per-leg hover tooltip. `date_UTC` (full ISO-8601 with offset, ~33 chars) is the single largest field |
   | geometry `coordinates` | 2.1 MB (11 %) | the actual track shape |
   | total | 19.0 MB | **~2.45 MB on the wire** (production nginx gzip-5); the browser still decompresses, parses, and builds objects from the full 19 MB |

   Emitter: `_geojson.tracks_geojson()` (`_geojson.py:251-313`); the per-vertex
   dual append (one coord **and** one `fixes` record per row, via `_fix_record`,
   `_geojson.py:97-111`) is `_geojson.py:296-300`. There is still **no
   simplification/decimation anywhere** in the module. Coordinates are already
   4 dp (`_coord`, `_geojson.py:40`); **precision is not the problem** (see
   below). `gliders.geojson` (388 KB) carries the **same** duplicated per-vertex
   `fixes` pattern (`gliders_geojson`, `_geojson.py:159-215`, 194-211 for the
   track branch).

## Data-volume analysis (answering "binary? low precision? pixel in deg?")

**What is a pixel, in degrees?** This was originally worked out at `maxZoom: 12`;
the map's `MAX_ZOOM` is now **14** (`config.js:88`, used at `app.js:2281`), two
levels deeper — a pixel at the deepest zoom is correspondingly **~4× smaller**
than the figures below, so the px-based DP tolerance table needs re-deriving
against real data at implementation time rather than trusted as-is. At the
original `maxZoom: 12`, Web Mercator, working latitude ≈ −37°:

- world width = `256 · 2¹²` = 1,048,576 px for 360°
- **longitude: 0.000343°/px (~30.5 m/px)**
- **latitude: 0.000274°/px (~30.4 m/px)**

So a pixel at zoom 12 is **~0.0003° ≈ 30 m**; at the current `maxZoom: 14` it is
**~0.00007–0.00009° ≈ 7.6 m**.

**Cropped to low precision?** Coordinates are *already* 4 dp (plan 032) =
0.0001° ≈ 11 m — sub-pixel at zoom 12 (~0.29 px) but roughly **1.2–1.5 px** at
the current `maxZoom: 14`, i.e. close to a pixel rather than clearly beneath
one. Still at/near the GPS noise floor, and 032 measured that cropping tracks
to 4 dp saved only −6 % gzipped either way. **Precision remains a weak lever
compared to vertex count and the telemetry, but re-check the sub-pixel claim
against zoom 14 before treating it as settled.**

**Ranked data-volume levers (biggest first):**

1. **Stop shipping per-fix telemetry in the always-loaded track file** — it is
   16.9 MB / 89 %. The head/clip machinery needs only `[lng, lat, t]` per vertex.
   Move the hover telemetry out of the hot path (see recommendation C). This
   alone takes the eager payload from 19 MB → ~2.1 MB (geometry only), ~0.3 MB
   gzipped.

2. **Simplify the geometry (Douglas–Peucker at build time).** Drifter tracks are
   smooth, so a modest tolerance keeps most of the visual shape while dropping
   most vertices. The table below is the original measurement (at the then-current
   `maxZoom: 12`); the vertex-kept percentages are a property of the tolerance vs.
   the real track geometry (zoom-independent), but the **px-at-max-zoom column is
   stale** now that `MAX_ZOOM` is 14, not 12 — re-run against the current zoom
   before picking a tolerance for "visually lossless":

   | DP tolerance | ≈ px @ then-current z12 | vertices kept |
   |---|---:|---:|
   | 0.0005° (~56 m) | 1.5 | 14,499 (14 %) |
   | 0.001° (~111 m) | 3.0 | 10,137 (10 %) |
   | 0.002° (~222 m) | 6.0 | 7,159 (7 %) |

   At the current `maxZoom: 14` each of those tolerances is ~4× larger in pixels
   (≈6, 12, 23 px) — likely too coarse to still read as lossless at full zoom, so
   the tolerance should probably be tightened (and the vertex-kept counts
   re-measured) rather than reused as-is.

   Caveat: the clock-follows head interpolates along the vertices, so keep enough
   temporal resolution for smooth head-walking — simplify on space but never drop
   so much that a straight-but-slow leg loses its time samples.

3. **Wire compression is already handled — don't chase it.** The production
   frontend nginx gzips `application/geo+json` at level 5 (Deployment context
   above), so tracks cross the wire at ~2.45 MB. Bumping `gzip_comp_level` 5→6
   in `oc_gateway`'s frontend `nginx.conf` buys a little more (≈2.3 MB) for CPU;
   marginal. **The point of levers 1–2 is the client cost gzip can't touch** —
   the browser still inflates and parses the full 19 MB and builds 100k objects.
   (On the GitLab Pages fallback, compression is *not* configured — a `.gz`
   pre-emit in the build would help there — but that host is secondary and
   levers 1–2 make it moot.)

4. **Minify the JSON** (trivial). `json.dumps` still uses default separators
   (`build.py:71`), so **1.41 MB (7.4 %)** of `tracks.geojson` is separator
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

### A. Render tracks on a Canvas renderer — implemented

The targeted variant shipped, not the minimal `preferCanvas: true` alternative
originally floated: `trackRenderer(pane)` (`app.js:838-839`) memoizes one
`L.canvas({ pane })`; `addTrack` (`app.js:926-949`) passes
`renderer: trackRenderer("overlayPane")` for the drifter + glider tracks.
Markers/heads stay SVG. This turns the per-line DOM cost into a single
per-pane canvas redraw: pan/zoom re-projects/re-strokes a few dozen paths
instead of laying out, compositing, and hit-testing ~100k `<path>` nodes.

**Gotcha found while implementing — only ONE full-viewport track canvas may
exist.** A Leaflet canvas renderer's `<canvas>` spans the whole viewport and
hit-tests its entire rectangle regardless of transparency, so a *second* track
canvas stacked above the first would swallow every hover/click meant for the
tracks below it. The first attempt also gave the ship tracks a canvas (in the
`shipTrack` pane, above the overlay-pane track canvas) — that killed all
drifter/glider hover *and* click-to-highlight (at every track pixel the topmost
element was the ship canvas). Fix: **ship tracks stay on SVG** (few segments;
SVG's per-path `pointer-events: visiblePainted` lets events fall through its
transparent areas to the track canvas below, `makeShipLayer`). Net: exactly one
track canvas, in `overlayPane`; everything above it (ship SVG, forecast/deploy
SVG panes, marker panes) either falls through or sits legitimately on top.

The canvas hover hit-test is throttled and skipped during drags, so it does not
tax panning.

### B. Collapse to one polyline per track; clip via `setLatLngs` — implemented (plan 050)

Landed under plan 050 (`fac6023`), after this plan's own A→C→B→D sequencing —
the felt stutter turned out to persist even with canvas (A) alone, because a
canvas renderer still iterates every *layer* per redraw, so ~100k tiny
per-segment polylines kept per-object overhead on every zoom. `addTrack`
(`app.js:926-949`) replaced the per-segment build with **one multi-part
`L.polyline` per instrument** (~150 objects total instead of ~100k), split into
disjoint parts only at a de-spiked gap (`splitAtGaps`). Clock clipping
(`clipLineTrack`, `app.js:586` — the observed-track counterpart to
`clipForecast`) sets the line's coordinates to the vertices up to the clock
plus the interpolated at-clock point via `setLatLngs`, instead of per-segment
`addLayer`/`removeLayer` churn.

The trade-off flagged here — losing per-*leg* hover tooltips — was resolved as
anticipated: one sticky tooltip per track, whose content is resolved on
`mouseover`/`mousemove` to the nearest fix vertex (`nearestVertexIdx`,
`app.js:911-923`, feeding `showTip` in `addTrack`). Ship tracks are the
exception (see A's gotcha): they still build one polyline per segment on SVG,
which is fine at their much smaller segment count.

### C. Split geometry from telemetry; simplify at build time — still open

Not started: `tracks_geojson` and `gliders_geojson` still emit the full
per-vertex `fixes[]`, there is no Douglas–Peucker (or any) simplification
anywhere in `_geojson.py`, and `build.py`'s `json.dumps` still uses default
separators. This is the biggest remaining lever — the eager payload is
unchanged at ~19 MB raw / ~2.45 MB gzipped, and the browser still inflates,
parses, and builds objects from the full per-fix telemetry on every load with
tracks eager-fetched (see D).

Build-side, in `_geojson.tracks_geojson` (`_geojson.py:251-313`) and the shared
`gliders_geojson` (`_geojson.py:159-215`):

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
(`_geojson.py:194-211`, confirmed) — give it the same treatment via the shared
emitter.

### D. Restore lazy build of the rendered lines (keep eager time series) — still open

Not started: `tracks.geojson` is still fetched, parsed, and turned into ~150
`addTrack` polylines unconditionally on load (`app.js:2917`,
`fetchJSON(DATA.tracks, { optional: true })`), whether or not "Show tracks" is
ever switched on. The head-follows-clock requirement (plan 035) needs the
**time series**, not the **rendered polylines**. Split the two:

- eagerly load the lightweight geometry+times (cheap after C) and register the
  head clips, so heads walk from load as today;
- build/attach the display polylines **lazily on first "Show tracks = on"**,
  restoring plan 032's deferral for the heavy part.

With B landed, the eager **build** is already cheap in object count (~150
polylines, not ~100k), so D's win is now specifically about the eager
**fetch + parse** of `tracks.geojson` — which stays ~19 MB raw until C lands,
and would still be a non-trivial ~0.3 MB post-C. D is worth doing once C
lands even if no build hitch remains, purely to avoid paying for track data
that a session with "Show tracks" left off never needs.

### E. Scrub-path micro-opts — moot

`renderCurrentsInfo` (`app.js:1900`) is called from inside the time slider's
`onChange` (`core/controls.js:453-457`, wired to the range input's `input`
event, which browsers already throttle to roughly one callback per animation
frame during a drag) — it is not rebuilding DOM on every raw input tick beyond
that. Combined with B already making the per-frame track-clipping cost trivial,
there is no remaining scrub-path hitch this item was chasing. No action needed.

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

## Sequencing — actual vs. planned

Planned order was A → C → B → D. What actually landed was **A then B**
(`fac6023`, under plan 050), out of order, once profiling after A alone still
showed a zoom stutter from the per-object overhead of ~100k tiny SVG-turned-canvas
paths — the concern flagged in A's original writeup ("B remains the cleanup,
not an urgent fix") turned out to matter sooner than expected. C and D have not
been started.

**Remaining work, in the order that still makes sense:**

1. **C — build-side telemetry split + DP simplification.** Cuts the eager
   payload from ~19 MB raw / ~2.45 MB gzipped toward well under 300 KB on the
   wire (the telemetry split alone gets most of the way there; the exact DP
   contribution depends on re-deriving the tolerance at the current
   `maxZoom: 14`, see the data-volume section above), and the browser's
   decompress/parse/build cost, which nginx's gzip cannot touch.
2. **D — defer the eager fetch/build to first "Show tracks = on".** Worth doing
   independent of C; most valuable once C has shrunk the payload C leaves to
   defer.

Wire compression is already in place (nginx gzip-5), so it is not on this list —
the levers above target the client-side render/parse cost that gzip doesn't
reach.

## Verification

- **Render (done):** with "Show tracks" on, pan/zoom now redraws a handful of
  canvas polylines per pane instead of laying out/compositing ~100k SVG
  `<path>` nodes.
- **Payload (open, item C):** serve `site/` (`python -m http.server`),
  headless-Chrome load, assert `tracks.geojson` on the wire shrinks materially
  from its current ~2.45 MB gzipped, and that `properties.fixes[]` is gone from
  the geometry-only artifact.
- **Lazy load (open, item D):** with item C in place, assert `tracks.geojson`
  is not fetched until "Show tracks" is first toggled.
- **Correctness:** heads still walk the clock with tracks off; hover still
  shows the right fix (nearest-vertex via canvas mousemove — done); clock
  clipping still trims at the scrubber; DP simplification (once added) is
  visually indistinguishable at every zoom, re-checked at `maxZoom: 14`.
- **Build:** `pixi run test`; regenerate `tracks.geojson` and confirm vertex
  count, absence of `fixes[]`, and size against the tables above once C lands.
