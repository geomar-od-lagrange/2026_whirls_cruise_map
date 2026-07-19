# 049 — Full-audit remediation (2026-07-17 review rollout)

> **Done (2026-07-18).** All groups landed on `main` as focused MRs; see the
> **Rollout record** below for the per-group MR list. The audit itself —
> [`docs/reviews/2026-07-17-full-audit.md`](../../docs/reviews/2026-07-17-full-audit.md) —
> is marked actioned. One finding (SRC-2) was deliberately skipped, documented below.

Implements the findings from
[`docs/reviews/2026-07-17-full-audit.md`](../../docs/reviews/2026-07-17-full-audit.md).
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

## Rollout record (completed 2026-07-18)

Every group landed as a focused MR merged to `main`; the final state:

- **G1** SEC-1 field-cache cap (!33) · **G2** web-surface hardening (!34) · **G3** shared
  `_geo.py`/`_time.py` primitives (!35) · **G4a** vertex-cadence `_batch_advect` (!36) ·
  **G4b** `_derive_slow` split (!37) · **G4c** point `NamedTuple` (!38) · **G4d** cache-reset
  + batch idioms (!39) · **G4e** deferred backend SRC-1/3/4 (!42) · **G4f** FC-2 `_StoreArray`
  validation (!43) · **G5a** ES-module system + leaf extractions (!40) · **G5b** deploy-tool
  module (!44) · **G6** selection/clock refactors FS-2/3/4 + perf follow-up (!45) · **G7**
  frontend correctness (!41) · **DER-3** shared `_frames.py` (!47).
- **SEC-1 principled follow-up ("#40", deploy-reported):** wall-clock resync in
  `_batch_advect` bounds forecast memory by the horizon window and removes
  `_MAX_START_SPREAD_DAYS` entirely (!46) — the durable answer to the spread issue the test
  deployment surfaced, superseding G1's coarse cap. **API-1** (`_get_field_index` →
  `(span, version)` under one lock, `_field_version` gone) folded into the same MR.
- **Deliberately skipped:** SRC-2 (a shared `_render_frames` helper) — the review verifier
  down-graded it; the two renderers' loops differ enough that a forced merge risked a
  render-pixel regression for negative structural gain. DER-3 (also verifier-LOW) *was* done
  because it only relocates truly-shared primitives with no behaviour change.
- **Dev tooling added (G5a):** `pixi run check-frontend` = `tsc --checkJs` (0 cross-module
  reference errors is the ES-module split's blank-page guard) + `esbuild --bundle` for the
  import graph — the standing verification loop for any future `site/map/` module change.
- **Cross-repo DEPLOY note:** carried verbatim in G5a's commit body; the sibling deploy repo
  made the `site/map/` copy recursive, so every later module file (config/format/api/
  features/deploy/core/*) rides that one change with no further per-MR deploy edit.

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

- [ ] `docs/hosting.md:261` claims a `128k` request-body cap the live gateway conf no
      longer has — the gateway relies on nginx's 1 MB default. Correct or drop the claim.
- [ ] The review's SEC-1 "3 Gi" figure — note the pod is 4 Gi (captured in G1 above).

## Done criteria

Each group: implemented, reviewed by a separate agent, merged to `main`. When all groups
land, move this plan to `plans/done/` with a pointer, and either write a short
`docs/` note or mark the review doc as actioned.
