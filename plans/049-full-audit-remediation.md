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

## MR groups

- [ ] **G1 — SEC-1: cap field-cache residency** (M) · *urgent, land first*
      `#1`. Cap `day_cache_cap_for_starts` to a ceiling that keeps several concurrent
      requests inside the pod limit; gate endpoint concurrency (semaphore / lower AnyIO
      thread limit); optionally 422 on excessive seed-start spread.
      **NB:** review cites a 3 Gi pod; it is now **4 Gi** (`instance.yaml.tmpl:135`,
      hotfixed). Tune the clamp against 4 Gi.

- [ ] **G2 — Web-surface hardening** (S) · independent
      `#2/#36` narrow catch-all `except`→503 to real "field missing" types, stop
      interpolating `str(exc)`/store path into the public body · `#8` escape/`textContent`
      third-party ship fields before `innerHTML` + add CSP to `index.html` (must allow
      tile `img-src` + ship-API `connect-src https://localisation.flotteoceanographique.fr`)
      · `#34` in-app request-body 413 guard · `#35` bound seed `lon∈[-180,180]`/
      `lat∈[-90,90]`, `allow_inf_nan=False`, fix docstring · `SEC-6/SEC-7` (parcels
      oracle is not deployed — app-side only).

- [ ] **G3 — Shared primitives `_geo.py` + `_time.py`** (M) · *foundation, before G4*
      `#25-28`, `IDIOM-1..4`. Earth radius, haversine, Coriolis, uv→deg into `_geo.py`;
      ISO format/parse into `_time.py`; delete the 3–5 copies each.

- [ ] **G4 — Backend/forecast/ingest refactors** (M, split if large) · *after G3*
      `#3` `_batch_advect` stores only vertex-cadence rows · `#4` break `_derive_slow`
      into `_render_*` helpers, drop manual `del`/`gc.collect()` · `#5/#29` one
      `NamedTuple` for point-tuple ordering · plus `API-*`, `FC-*`, `ING-*`, `DER-*`,
      `SRC-*` batched as convenient.

- [ ] **G5 — FS-1: split `app.js` into ES modules** (L) · *see cross-repo coupling above*
      Split along a concern spine, `type=module`, no bundler; start with the
      self-contained deploy tool. **Commit body must carry the DEPLOY note above.**

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
