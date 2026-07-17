# 049 — Full-audit remediation (2026-07-17 review rollout)

Implements the findings from
[`docs/reviews/2026-07-17-full-audit.md`](../docs/reviews/2026-07-17-full-audit.md).
That doc is the source of truth for *what* each finding is and *why*; this plan is the
*rollout* — grouping, ordering, and cross-session progress.

## Strategy

Sequenced focused MRs, each merged straight to `main` as it's reviewed. **No staging
branch** — the groups are independent and safe to land one at a time. Only SEC-1 is an
operational urgency; everything else is hygiene, structure, or idiom.

Two hard ordering constraints:
- **G3 (`_geo.py`/`_time.py`) before G4** — the per-module idiom deletions consume the
  shared primitives; landing the primitives first avoids touching the same lines twice.
- **G5-FS1 before G6** — FS-2/3/4 build on the module boundaries FS-1 introduces.

Everything else is order-free. Sizes (S/M/L) are rough.

## Cross-repo coupling — read before starting G5

**FS-1 (the `app.js` ES-module split) breaks the deploy image unless the frontend build
is updated in the sibling deploy repo.** The frontend image copies a hard-coded per-file
allowlist of `site/map/` assets in two places:
- `deploy/_lib.sh:234` (stages the build context)
- `deploy/_frontend/Dockerfile:23-31` (COPYs into the image)

New module files (`site/map/core/*.js`, `features/*.js`, …) are in neither list → they
never enter the image → `index.html`'s `<script type=module>` imports 404 in
production. Works locally, blank page once deployed.

**Handling:** we do *not* edit the deploy repo from here. Instead, the FS-1 commit
message must spell out the required deploy change so the deployment bot agent picks it
up and adapts. Bake this into the G5-FS1 commit body verbatim:

> DEPLOY: this splits `site/map/app.js` into multiple ES module files
> (`type=module`, no bundler). The frontend build currently copies a per-file
> allowlist and will drop the new modules. Update `deploy/_lib.sh:234` and
> `deploy/_frontend/Dockerfile:23-31` to copy `site/map/` recursively instead of
> per-file. Keep the `.js` extension (stock nginx `mime.types` has no `.mjs` mapping →
> would serve `application/octet-stream` and browsers refuse the module).

MIME/cache are otherwise fine: both nginx confs `include mime.types` and serve `.js` as
`application/javascript`; no gateway conf sets `Cache-Control`, so multiple module files
aren't cache-pinned.

## Rollout status (2026-07-18)

Session progress, newest facts here so a resumed session has the full picture:

- **Merged to `main`:** G1 (!33), G2 (!34), G3 (!35), G4a (!36), G4b (!37), G4c (!38),
  G4d (!39), G4e SRC-1/3/4 (!42), G4f FC-2 (!43), G5a (!40), G5b (!44), G7 (!41).
- **Open MRs awaiting the user's browser validation:**
  - **!45 — G6** (branch `audit/g6-selection-clock`): FS-2 (selection → `core/selection.js`),
    FS-3 (clock fan-out → `core/clock.js`), FS-4 (controls → `core/controls.js`), **plus a
    perf follow-up** (makeSelection uses a plain property, not a getter — fixed a
    select-all/first-paint stutter the user reported). Rebased on main; statically verified
    (tsc 0 ref-errors, esbuild, node-check); needs the running map eyeballed.
  - **!46 — SEC-1 principled fix ("#40")** (branch `audit/sec1-resync-spread-fix`):
    wall-clock resync in `_batch_advect` bounds memory by the horizon window, removes
    `_MAX_START_SPREAD_DAYS`; bit-identical, 179 tests pass. Backend-only; reviewed
    line-by-line. Safe to merge on review; the user wanted it landed.
- **Remaining, in order:**
  1. **API-1** — fold into #46's area (`_get_field_index` → `(span, version)` one locked
     result, drop `_field_version`'s out-of-lock assert). NOT started; note the reload test
     `test_forecast_api.py:428` asserts `_get_field_index() is _index` → update to `[0]`.
  2. **DER-3** — move `_currents` layer-neutral privates (`_quantize_unit`, `_slice_at`,
     `frame_valid_time`, `frame_filename`, `N_BINS`) into a shared `_frames.py`; own MR off
     clean main. Safe relocation.
  3. **Wrap-up** — once G6 + #46 land: move this plan to `plans/done/`, mark the review doc
     actioned. (SRC-2 deliberately skipped — verifier-down-graded, render-pixel risk.)
- **Dev tooling added (G5a):** `pixi run check-frontend` = `tsc --checkJs` (0 cross-module
  reference errors is the split's blank-page guard) + `esbuild --bundle` for the import graph.
- **DEPLOY note:** already carried by G5a's commit; the deploy repo makes the `site/map/` copy
  recursive. New module files (config/format/api/features/deploy/core/*) ride that — no further
  deploy change per MR.

## MR groups

- [x] **G1 — SEC-1: cap field-cache residency** (M) · *urgent, land first* · **landed** (MR !33)
      `#1`. Cap `day_cache_cap_for_starts` to a ceiling that keeps several concurrent
      requests inside the pod limit; gate endpoint concurrency (semaphore / lower AnyIO
      thread limit); optionally 422 on excessive seed-start spread.
      **NB:** review cites a 3 Gi pod; it is now **4 Gi** (`instance.yaml.tmpl:135`,
      hotfixed). Tune the clamp against 4 Gi.

- [x] **G2 — Web-surface hardening** (S) · independent · **landed** (MR !34)
      `#2/#36` narrow catch-all `except`→503 to real "field missing" types, stop
      interpolating `str(exc)`/store path into the public body · `#8` escape/`textContent`
      third-party ship fields before `innerHTML` + add CSP to `index.html` (must allow
      tile `img-src` + ship-API `connect-src https://localisation.flotteoceanographique.fr`)
      · `#34` in-app request-body 413 guard · `#35` bound seed `lon∈[-180,180]`/
      `lat∈[-90,90]`, `allow_inf_nan=False`, fix docstring · `SEC-6/SEC-7` (parcels
      oracle is not deployed — app-side only).

- [x] **G3 — Shared primitives `_geo.py` + `_time.py`** (M) · *foundation, before G4* · **landed** (MR !35)
      `#25-28`, `IDIOM-1..4`. Earth radius, haversine, Coriolis, uv→deg into `_geo.py`;
      ISO format/parse into `_time.py`; delete the 3–5 copies each.

- [x] **G4 — Backend/forecast/ingest refactors** (M, split) · *after G3* · **landed** (MRs !36/!37/!38 + G4d)
      `#3` `_batch_advect` stores only vertex-cadence rows (G4a/!36) · `#4` break
      `_derive_slow` into `_render_*` helpers (G4b/!37; the per-variable `del` is gone,
      a single documented phase-boundary `gc.collect()` retained for the OOM-sensitive
      path — plan 045) · `#5/#29` one `NamedTuple` for point-tuple ordering (G4c/!38) ·
      G4d batch: `API-2` (`_reset_caches`), `API-3/4` (folded into G3), `FC-3`,
      `ING-2/3/5/6`, `IDIOM-5/6`, `DER-4` (G3).
      **Deferred (LOW, larger refactors, out of this pass):** `API-1` (single locked
      `(span, version)`), `FC-2` (`_StoreArray` key validation), `ING-4`
      (`platforms.csv` memoize), `SRC-1..4` (shared `_portal`, `_render_frames`,
      `_parse_time`, timeout/retry), `DER-2` (vectorize Mercator warp), `DER-3` (move
      `_currents` privates to a shared `_frames`/`_raster`). Tracked here for a later pass.

- [~] **G5 — FS-1: split `app.js` into ES modules** (L) · *see cross-repo coupling above*
      Split along a concern spine, `type=module`, no bundler; start with the
      self-contained deploy tool. **Commit body must carry the DEPLOY note above.**
      **G5a landed (MR pending user browser-validation): the module system + the safe
      leaf/utility extractions.** `index.html` → `type=module`; extracted `config.js`
      (DATA/palette/fallback/SHIP), `format.js` (pure formatters), `api.js` (forecast
      endpoint helpers). Dev-only verification added — `tsconfig.json` +
      `frontend-globals.d.ts` + `pixi run check-frontend` (`tsc --checkJs`, no build
      step, catches cross-module reference errors), plus `esbuild --bundle` for the
      import graph. **G5b remaining (the behavior-sensitive part, wants in-browser
      validation):** extract `features/deploy.js` (`buildDeployTool(deps)`), which the
      dependency map shows is interleaved with 3 observed-drifter functions in the same
      banner span and has `updateClock`/background-click back-references (replace with
      exposed `clipAllDeployTracks(ms)`/`clearSelections()`); then the `core/` concern
      spine (render/selection/clock/controls) that **G6** (FS-2/3/4) builds on.

- [ ] **G6 — Frontend selection/clock refactors** (M) · *after G5*
      `FS-2` collapse four selection state machines into one parameterized `Selection` ·
      `FS-3` clock fan-out · `FS-4` clock-following-tracks concern split.

- [ ] **G7 — Frontend correctness** (S) · independent
      `#6` `Promise.all` the five optional fetches in `main()` · `#7` skip per-segment
      restyle when zoom weight-bucket unchanged · `FE-3` fetch timeout / AbortController ·
      `FE-4` cache-buster on re-polled `agulhas.json` · `FE-5` fix `resolveApi()` fallback.

## Doc corrections (fold in as tiny cleanups, any MR)

- [ ] `docs/deploy.md:261` claims a `128k` request-body cap the live gateway conf no
      longer has — the gateway relies on nginx's 1 MB default. Correct or drop the claim.
- [ ] The review's SEC-1 "3 Gi" figure — note the pod is 4 Gi (captured in G1 above).

## Done criteria

Each group: implemented, reviewed by a separate agent, merged to `main`. When all groups
land, move this plan to `plans/done/` with a pointer, and either write a short
`docs/` note or mark the review doc as actioned.
