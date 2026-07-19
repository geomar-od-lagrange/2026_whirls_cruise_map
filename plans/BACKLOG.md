# Backlog

Unscheduled ideas, not yet promoted to a plan.

- **Track DB parquet cache** — persist the derived `(D_number, date_UTC)` table
  and ingest only new snapshots, if full re-read from the share gets slow.
  *Largely subsumed by [018](done/018-ingest-derive-data-seam.md)*: the ingest→derive
  seam persists the cleaned tracks (as CSV, not parquet), which is the
  persistence this wanted; parquet stays a deferred efficiency companion there.
- **Track thinning (payload, not rendering)** — simplify/decimate dense
  trajectories (the ship's 10-min grid, ≤5 min drifter snapshots over weeks) for
  transfer size. The per-fix dots were removed in
  [031](done/031-track-zorder-and-data-link.md), and rendering cost itself is no
  longer the concern: each track is now **one multi-part polyline per
  instrument** on a shared canvas renderer, not one polyline per fix-to-fix
  segment ([done/050-zoom-track-collapse.md](done/050-zoom-track-collapse.md) —
  see also [039-track-rendering-performance.md](039-track-rendering-performance.md),
  still open, for the remaining rendering-side levers). **Still a transfer-size
  problem**: `tracks.geojson` is 14.6 MB for 146 drifters × ~2 weeks, and the
  target scenario (drifters stay in the water past cruise end) is ~200 drifters ×
  ~100 days under a ~10 MB transfer budget. The bytes are dominated by the
  per-vertex `fixes` records (ISO strings + mostly-null fields), not
  coordinates. Levers, in payoff order: (1) compact parallel arrays instead of
  per-vertex objects (epoch-delta ints for time, drop null fields); (2)
  Douglas–Peucker thinning at ~100–200 m tolerance (retained vertices keep
  their real times, so position-at-time interpolation still works; inertial
  loops survive at display scale); (3) precompressed artifacts inflated
  client-side (the current OpenShift `oc_gateway` nginx can gzip; the retired
  GitLab Pages deploy couldn't). Together plausibly 2–5 MB for the target
  scenario. Deliberately kept out of
  [done/034-deployment-focused-app.md](done/034-deployment-focused-app.md).
- **GPS despike at ingestion** — several Deployment-1 drifters show single-fix
  out-and-back GPS spikes (one stray fix implying 15–140 m/s to both
  neighbours; seen in D-577, D-602, D-606, D-610, D-630, worst in D-611 and
  D-612 with 4 each, per the 2026-07-03 inertial survey). The build currently
  ingests them raw into tracks, markers, and the derived popup speeds. A
  despike in `_clean.py` — e.g. flag a fix whose implied speed exceeds a
  threshold on **both** adjacent segments — would clean tracks and popups. Under
  [018](done/018-ingest-derive-data-seam.md) this becomes a *visible* ingest step,
  reflected in the published `drifters.csv` (a `despiked` flag or documented
  drop) rather than an invisible in-memory filter.
- **Awaiting-first-fix view** — surface staged-but-not-yet-transmitting drifters.
- **Currents bbox auto-fit** — track the drifter cloud instead of a fixed box.
- **Time scrubber** — animate positions/tracks over time.
- **Flow-trail land bleed** — the animated current trails reach onto land near
  the coast while the speed shading stops cleanly at it. Cause: leaflet-velocity
  needs a hole-free grid and has no land mask, so land is fed in as zero velocity
  (`_currents._component`, `nan -> 0`) and the client bilinearly interpolates
  across the ocean->0 boundary, smearing coastal velocities onshore; the stride-3
  coarsening widens it. The shading instead masks land with per-pixel alpha
  (`to_speed_png`). Not fixed — a fix would erode the ocean by one coarse cell
  before serving, or draw a land polygon above the flow pane; both trade some
  near-shore coverage. Cosmetic only. *Possibly moot*: the flow overlay no longer
  ships as a leaflet-velocity grid — it is now a precomputed streamline WebP (see
  [done/038-precomputed-flow-vis.md](done/038-precomputed-flow-vis.md)) — so this
  needs re-checking against the current renderer before anyone picks it up.
- **Ship-API first-poll windowing** — the Marion Dufresne live position poll
  fetches the *entire* cruise window on every first call
  (`ship.lastDate() ?? SHIP.cruiseStart`, `site/map/app.js`; `SHIP.cruiseStart` in
  `site/map/config.js`) rather than a bounded recent range, so a cold load pulls
  far more history than the map needs.
- **Missing favicon** — there is no `site/map/favicon*` and no `<link rel="icon">`
  in `site/map/index.html`, so every cold load 404s on the browser's default
  favicon request.
- **Re-run the cache-header check against OpenShift** — the at-sea performance
  pass checked response cache headers against the (now-retired) GitLab Pages
  deploy; re-check them against the current OpenShift `oc_gateway` serving path
  instead.
- **`tracemalloc` peak-memory regression tests for the slow-derive OOM fix** —
  [045-slow-derive-oom.md](045-slow-derive-oom.md)'s own Verification section
  calls for two `tracemalloc` peak-memory assertions (bounding the float32
  chunked-cast load and the single-slice landmask call) and neither exists; the
  dtype/byte-equivalence tests that do exist verify *what* the fixes produce, not
  that peak RSS during the call is actually bounded. Add the assertions, or
  explicitly strike them from that plan's Verification section as not worth the
  test complexity — either way, a future refactor that reintroduces a
  full-window materialize currently passes every existing test.
- **`identityColor(kind, key)` colour seam** — [041-track-visual-overhaul.md](041-track-visual-overhaul.md)
  specced a single function as the sole colour source for a track's line, dot,
  and head; colour is instead inlined per call site (`site/map/app.js`,
  `site/map/features/deploy.js`). Since the palette work already converged
  line = dot = head in practice (`done/047-instrument-palette.md`), this is a
  refactor-for-its-own-sake question: extract the seam, or strike the
  requirement from 041 and leave it inlined.
