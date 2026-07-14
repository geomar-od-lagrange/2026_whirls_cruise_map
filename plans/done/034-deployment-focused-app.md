> Implemented (the `oc_gateway` cross-repo section is deferred by the plan
> itself, not by this work). See [docs/deployment.md](../../docs/deployment.md)
> (the deploy tool as primary UI, API v2, streaming StoreField serving),
> [docs/field_store.md](../../docs/field_store.md) (the incremental per-day
> store), and [docs/currents.md](../../docs/currents.md) /
> [docs/controls.md](../../docs/controls.md) (the one-clock scrubber and the
> deployment manager) for the current state.

# 034 — Deployment-focused app: full-cruise field, backward runs, one clock

Reframe the app around **placing and interrogating virtual deployments** — the
capability that distinguishes it from the IPSL/aeris observations map
(https://observations.ipsl.fr/aeris/whirls/#/map). Today the map is an
observation viewer with the Deploy tool bolted on as a dev PoC; this plan
inverts that. Supersedes the root scratch note `deployment-focused-refact.md`
(absorbed here).

## Decisions (agreed 2026-07-13)

1. **Dev target**: the local pixi flows — `pixi run build --tier fast/slow`,
   `serve`, `serve-api` — which mirror the cluster CronJobs/pods 1:1. The
   `--tier` flag spelling stays as-is; no alias tasks.
2. **Coverage**: hard `tmin = 2026-06-28T00Z` (cruise start), `tmax` = end of
   CMEMS forecast reach (~now + 10 d). Applies to both the 12 h scrubber
   frames and the hourly uv field driving virtual-drifter runs. The window
   grows ~1 day/day; design for end-of-scenario size, not today's. `tmin` is
   a single configuration parameter (a `_currents` constant with an env
   override), not a scattered literal — alongside the existing `BBOX`, it is
   the seam for pointing the app at another cruise or region later.
3. **Runs**: forward *and* backward advection of single or line-deployed
   virtual drifters, released at any covered time.
4. **Terminology**: a run is described by **release time + direction (+
   duration)**. The forecast/hindcast vocabulary is retired for virtual runs —
   it conflates run direction with field provenance. "Forecast" remains a
   property of the *field*: times beyond the analysis edge come from the CMEMS
   forecast, and track segments through that region are styled distinctly
   (see D4). The four release/direction quadrants need no names.
5. **One clock, strengthened**: a deployment's release time *is* the displayed
   field time, always (no override). The scrubber gains a type-in datetime
   jump, so "release at t" = jump the whole map to t. Placed tracks stay
   tangent to the displayed field at t = 0 by construction.
6. **At-time markers, always on**: every visible time-aware layer (virtual
   tracks, real drifter tracks, ships) carries a position-at-displayed-time
   marker that moves as the scrubber moves. This **replaces** the synced-t0
   coloured dots (tab20c palette, mark legend, `marks` machinery). Tooltips
   keep per-point times.
7. **Click axes** (kept, recomposed): click an at-time marker → highlight that
   deployment's whole marker set (array shape at this instant); click a bare
   track line → that one particle's full track; click a drop disc → that
   deployment's drops. Background click clears.
8. **Ship tracks simplified**: Marion Dufresne black (`#1a1a1a`) and Agulhas II
   crimson (`#9b1c31`) plain lines at drifter-track width, no per-fix dots,
   along-track time tooltips kept.
9. **Flow trails**: full time coverage kept (no near-now restriction); the
   prefetch policy changes to a band around now, on-demand elsewhere.

## Budgets the cluster imposes (from `oc_gateway`, recon 2026-07-13)

These make two design choices *requirements*, not preferences:

- **PVC `inst-<sha>-data` is 2 Gi** and the current 72 h hourly window cache
  is already 154 MB. The full-coverage hourly field is ~1.3 GB today, growing
  ~50 MB/day. Volume budgets are cheap to change when flagged (single-digit
  Gi is easy; ~50 Gi would not be), so this constrains sizing, not design —
  estimate the end-of-scenario footprint and flag it when `oc_gateway`
  adapts. `retire.sh` deletes the PVC per iteration — see cross-repo notes.
- **API pod limit 3 Gi** (quota math in `oc_gateway/docs/deploy.md` assumes it
  stays there) → the API must **not** hold the full field in RAM. Streaming
  field access (workstream B) keeps API memory flat regardless of window
  length.
- **build-slow: 30 min `activeDeadlineSeconds`, 2 Gi limit**, already tight
  for the current fetch → the fetch must be **incremental and resumable**
  (workstream A), never a *routine* full refetch. Working assumption
  (decided): CMEMS revises nothing behind the current analysis edge.
  Validating that is deferred to a repo issue (compare a stored `final` day
  against a fresh fetch later); the full-refetch escape hatch is the guard
  meanwhile.
- **Edge router hard ~60 s timeout**, unraisable from the namespace (the
  gateway's 240 s `proxy_read_timeout` is moot) → a seeds×hours request
  budget, not just a seed cap.
- **Serving hole**: the fe pod statically serves the `render/` subtree as
  `map/data/`, and nothing blocks `_cache/` — `forecast_window.nc` (154 MB) is
  likely publicly fetchable today. The new store lives in a subtree the fe pod
  never mounts, killing this structurally.

## Workstream A — incremental hourly-field store (build side)

Replace the single `forecast_window.nc` (fetched whole, every slow run) with a
**per-day file store**:

- **Layout**: `uv_YYYY-MM-DD.nc` (24 hourly steps, current `BBOX`, `uo`/`vo`
  float32) plus an atomically-written `field_manifest.json` (span, per-day
  entries with mtime and a `final` flag). Path from `WHIRLS_FIELD_CACHE`
  (default: repo-local `cache/field/`, outside `site/` so neither
  `pixi run serve` nor the fe pod ever serves it).
- **Refresh policy** (built on the rollover working assumption — CMEMS
  revises nothing behind the current analysis edge): each slow run fetches
  every missing or non-`final` day in `[tmin, tmax]`. A day becomes `final`
  once fetched with its whole span behind the fetch-time analysis edge
  (≈ fetch wall-clock now, minus a safety margin — a config knob, default
  12 h); days fetched wholly or partly as forecast stay non-final and are
  refetched each run until they finalize (~11 days ≈ 550 MB per run, well
  inside the cron budget). One `copernicusmarine` subset call per day
  (~50 MB, bounded memory); per-day atomic writes make the initial ~25-day
  backfill **resumable** across cron runs, so a killed backfill just
  continues.
- **Full-refetch escape hatch**: a `--refetch-all` build flag (equivalently:
  wipe the store) forces a complete re-pull — the guard if the deferred
  rollover validation (repo issue: compare a stored `final` day against a
  fresh fetch) ever shows revisions behind the edge, or against an
  unannounced CMEMS reprocessing.
- **First implementation step**: confirm the
  `cmems_mod_glo_phy_anfc_0.083deg_PT1H-m` back-catalogue reaches 2026-06-28
  (one describe/subset probe; the backfill itself would surface it anyway).
- The legacy `_write_window_cache` / `forecast_window.nc` path is deleted once
  the API reads the store (B). The inertial decomposition's narrow ~24 h slice
  reads from the store instead.

## Workstream B — streaming batch engine + API v2

**Engine** (`_forecast.py`):

- Keep `_batch_advect`'s step-index-lockstep structure and bit-identical RK4
  arithmetic (still pinned to the scalar reference by test). Change only the
  field access: a windowed `_Field` variant backed by the store, holding a
  small rolling set of hourly slices. The batch's time cursor is monotone
  (each loop iteration advances every active seed by one dt from its own
  start), so the working set is O(seed-stagger + 1 h) of slices — a few MB —
  loaded on demand and evicted behind the cursor.
- Add `direction` to the batch path (the scalar path already has it: `dt`
  negation, `_forecast.py:191`); backward walks the store in reverse.
- **Adaptive vertex cadence**: 15 min vertices up to 48 h; beyond, widen so a
  track stays ≤ ~400 vertices (a 25-day track at 15 min would be 2400).
- **Implicit per-vertex timing**: each returned feature carries
  `{t0, cadence_s}` so the client computes the position at any instant by
  interpolation. This is the substrate for the at-time markers *and* for the
  later pair-separation / actual-vs-virtual stats. The `marks` /
  `mark_step_h` machinery is removed from the batch API (decision 6); the
  build's instrument forecast/hindcast artifacts keep theirs (out of scope).

**API** (`_api.py`):

- Request: run-level `direction` (`"forward" | "backward"`), `horizon_h`
  bounded by the loaded window, seed cap kept at 2000, plus a combined
  **seeds×hours budget** sized so worst-case compute + serialization stays
  well inside the 60 s edge timeout (compute scales linearly: 2000 seeds ×
  48 h ≈ 1.2 s today; budget ~1M seed-hours ≈ ~12 s leaves margin).
- `GET /api/forecast/limits` grows: window span `[tmin, tmax]`, the analysis→
  forecast boundary time, seed cap, seeds×hours budget. The client validates
  placements up front and styles provenance from it.
- Reload trigger: manifest mtime/content instead of the single file's mtime
  (same one-stat-per-request shape).
- Provenance boundary, pragmatically: times beyond "now at response time" are
  forecast-provenance (the anfc analysis edge trails now by ≤ ~1 d; exactness
  is not needed for a styling cue).

## Workstream C — absolute-time frames + the long scrubber

- **Frame naming goes absolute**: `speed_2026-07-01T00Z.webp` (likewise
  vorticity + flow JSON), manifest-driven (`frames: [{valid_time, file}]`,
  nearest-now index computed client-side). Offset-relative names die with the
  moving anchor.
- **Incremental rendering**: a frame older than the revision horizon that
  already exists is skipped; only recent + forecast frames are (re)rendered
  each slow run. Final frames are immutable files → real HTTP caching on the
  gateway. Frame count is ~2/day (~50 now, ~140 by scenario end) — rasters at
  ~27–37 kB stay trivial; flow JSON at ~0.45 MB/frame reaches ~45 MB total,
  which is why prefetch becomes a **band around now** (±8 frames), on-demand
  elsewhere (the request-token machinery already handles on-demand).
- **Colour scale freezes**: `vmax` (and ζ/f ±vmax) become constants derived
  once from the current pooled scale — re-pooling over a growing history
  drifts the scale and forces all frames into build memory. Legend becomes
  stable across builds.
- **Scrubber widget**: a datetime slider over `[tmin, tmax]` with day ticks, a
  type-in datetime jump (decision 5), and a clear now-marker. The scrubber
  time is the **app clock** at 1 h granularity (the advection field's
  cadence); rasters/flow snap to the nearest 12 h frame; at-time markers and
  the release time use the clock exactly.

## Workstream D — deployment-first frontend

1. **Deploy panel becomes primary**: first tab in the dock, carrying a
   **deployment manager** — one row per placed deployment (id, release time,
   direction, duration, N drops) with visibility toggle, CSV export, delete.
   Replaces the single global Clear. Knobs gain **direction** and **duration**;
   the release time is displayed read-only, following the clock.
2. **At-time markers** on every visible time-aware layer: virtual tracks (from
   `{t0, cadence_s}` + vertices), real drifter tracks (from per-vertex
   `fixes.date_UTC`), ship tracks. Linear interpolation between bracketing
   vertices; no marker when the clock is outside a track's span. One marker
   style, colour inherited from the layer.
3. **Click axes** per decision 7; the tab20c palette, dot legend, and
   dot-column code (`app.js:1422-1527`) are deleted.
4. **Provenance styling**: virtual-track segments beyond the analysis edge
   (from `/limits`) render dashed — the uncertainty cue that replaces the
   forecast/hindcast naming.
5. **Ship-track simplification** per decision 8 (`makeShipLayer`,
   `app.js:2381 ff`).
6. **Terminology sweep**: UI copy and docs move to release/direction/duration.

## Cross-repo coordination (`oc_gateway`) — deferred

The refactor is developed and settled entirely in the local
`pixi run build / serve / serve-api` world; `oc_gateway` adapts afterwards
(the flows mirror 1:1, so the adaptation is mechanical). Checklist for that
later pass, recorded here so nothing is lost:

- PVC request 2 Gi → the end-of-scenario footprint (single-digit Gi; cheap to
  request at that scale); new `cache` subPath mounted into the build CronJobs
  (rw) and the api pod (ro), **never** the fe pod. Retire the `render/_cache`
  mount + `WHIRLS_FORECAST_WINDOW` (→ `WHIRLS_FIELD_CACHE`). This also closes
  the `_cache` public-serving hole.
- API memory limit can stay 3 Gi (streaming keeps usage flat); build budgets
  hold via the incremental fetch.
- **Open question for that repo**: `retire.sh` deletes the PVC per iteration,
  so each promote re-seeds a multi-GB, ~25+-day backfill (resumable, but the
  instance serves a degraded window until it completes). A role-lifetime
  field-cache PVC shared across instances would avoid that; decide there.

## Sequencing and agent approach

Order **A → B → C → D**: B reads A's store; D reads B's API and C's manifest;
C is independent of A/B but shares `_currents.py`/`build.py` surface with A,
so it lands after A to avoid churn. Per `AGENTS.md`: no worktrees — one
branch per workstream in the single workdir, landed sequentially. Per
workstream, a Workflow runs implement (Sonnet) → multi-lens review panel
(correctness / regression / security, cheap models) → fix; architecture seams
and judgment calls stay with the top-level (Fable) session. Recon stays plain
parallel read-only agents.

## Out of scope (later functionality, enabled by this plan)

- **Pair-separation** on virtual multi-drifter deployments, and pair stats on
  real deployments — the uniform per-vertex timing (B) is the substrate.
- **Actual-vs-virtual difference stats** (real drifter vs a virtual twin
  released at the same point/time; needs UI for choosing the release point
  along a real track) — the at-time markers already give the visual read.
- **t0-inversion** (backward-advect an ideal array configuration to staggered
  drop times) — open item from plan 023; backward batch advection (B) is its
  missing engine half.
- **Trajectory transfer budget** (200 drifters × 100 d under 10 MB) — see the
  extended *Track thinning* entry in `BACKLOG.md`; `tracks.geojson` is already
  14.6 MB, dominated by per-vertex `fixes` records, not coordinates.

## Risks / verify early

- PT1H-m back-catalogue reach to 2026-06-28 (A's first step, one probe). The
  rollover assumption's validation is deferred — tracked as a repo issue.
- The 60 s edge timeout on worst-case long-run batches — measure at the
  seeds×hours budget before freezing it.
- Flow-frame total volume vs. at-sea use — full coverage is kept by decision;
  if it hurts in practice, thinning past frames is the fallback lever.
