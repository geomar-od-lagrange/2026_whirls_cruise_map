# Full repository review — 2026-07-17

Point-in-time audit of the whole `2026_whirls_cruise_map` codebase, focused on
three packages plus a standalone security pass:

1. **Backend API** — the web-facing FastAPI forecast service.
2. **Data fetching & building** — the ingest→derive pipeline and its per-source modules.
3. **Frontend structure** — the static Leaflet client (`site/map/`).
4. **Security** — a separate threat model of the *deployed, internet-facing* app
   (CMEMS and the team's own cluster are trusted; the public forecast endpoint and
   the static site are not).

This is an audit snapshot, not evergreen documentation — it describes the tree at
commit `062ee74` and will go stale as findings are addressed. It lives under
`docs/reviews/` rather than `docs/` for that reason.

## How this review was produced

A `/code-review`-style multi-agent workflow: **nine parallel finder agents** (one
per package slice plus cross-cutting *python-idioms* and *security* lenses), each
reading the actual source at high reasoning effort, followed by **one adversarial
verifier per finding** that re-opened the cited code and tried to *refute* it. Only
findings that survived verification appear below; severities are the verifier's
adjusted values, not the finder's original. Of 41 raw findings, **2 were refuted**
and dropped, and several were down-graded (mostly medium→low) once checked against
the pre-alpha, single-pod, internal-API-is-free context.

Every finding is anchored to `file:line`. The verifier notes occasionally correct a
line number or count — those corrections are folded into the text below.

## Overall assessment

**The codebase is in good shape.** It reads as *deliberately* built, not
accidentally grown: the ingest→derive `/data` seam is clean, every write is atomic,
best-effort layering is consistent (a dead upstream drops one artifact, never the
build), the RK4 advection math and its vectorized twin hold up under inspection, and
the public forecast endpoint is genuinely hardened on the compute axis (seed cap,
horizon cap, seeds×hours budget, `extra="forbid"`, `allow_inf_nan` on the horizon).
The two-module API split (`_api` production / `_api_parcels` oracle) is intentional
and documented. There is no gross code smell.

The findings are therefore mostly **structure, duplication, and idiom** cleanups,
with a small number of **real issues that deserve action before the deployment API
carries live cruise traffic**:

- **One HIGH:** an unbounded per-request field-cache residency that lets a *cheap*
  request pin the entire field store in RAM and OOM the 3 Gi pod — the seeds×hours
  budget bounds CPU but not memory. (#1)
- **A cluster of web-surface hygiene issues** the security pass turned up: a catch-all
  `except`→503 that both masks real 500s and leaks the store's filesystem path (#2,
  #36), an unescaped DOM sink for third-party ship-API fields with no CSP (#8),
  unbounded `lon`/`lat` + `inf`/`nan` on seeds (#35), and no in-app request-body cap (#34).
- **Two headline structural themes:** the single **4,564-line `app.js`** (frontend
  structure, below) and a **scatter of un-centralized geodesy/time primitives** —
  the Earth radius, haversine, Coriolis, m/s→deg conversion, and ISO-8601
  formatting/parsing are each copy-pasted across 3–5 modules (#25, #26, #27, #28).
  A small shared `_geo.py`/`_time.py` collapses most of the idiom findings at once.

None of the medium/low items are correctness bugs in the shipped default
configuration; they are latent risks, maintainability debt, and diagnostics quality.

## Priority action list

Ranked for action. "Effort" is a rough size, not a promise.

| # | Sev | Area | Action | Effort |
|---|-----|------|--------|--------|
| 1 | **HIGH** | security | Cap `day_cache_cap_for_starts` at a ceiling that keeps several concurrent requests inside 3 Gi; gate endpoint concurrency (semaphore / lower AnyIO thread limit); optionally 422 on excessive seed-start spread | M |
| 2 | MED | backend-api | Narrow the `except Exception`→503 to the real "field missing" types; stop interpolating `str(exc)` into the public body (fixes #36 too) | S |
| 8 | MED | security | Escape/`textContent` the third-party ship-API fields before they hit `innerHTML`; add a restrictive CSP to `index.html` | S |
| 34 | MED→LOW | security | Add an in-app request-body-size guard (413 before parse); don't rely on an out-of-repo nginx cap | S |
| 35 | MED→LOW | security | Bound seed `lon∈[-180,180]`/`lat∈[-90,90]`, set `allow_inf_nan=False` on `Seed`, fix the misleading docstring | S |
| 3 | MED | forecast | Have `_batch_advect` store only vertex-cadence rows, not every 5-min substep (~8/9 of the buffer is discarded) | M |
| 4 | MED | data-ingest | Break `_derive_slow` into `_render_currents`/`_render_vorticity`/`_render_inertial` helpers; drop the manual `del`/`gc.collect()` | M |
| 5 | MED→LOW | data-derive | Normalize the drifter vs glider point-tuple ordering (one `NamedTuple`) to kill the positional reshuffling (#29 same root) | M |
| FS-1 | HIGH¹ | frontend | Split the 4,564-line single-scope `app.js` into ES modules along the existing banners (`type=module`, no bundler); start with the self-contained deploy tool | L |
| FS-2 | MED | frontend | Collapse the four near-identical selection state machines into one parameterized `Selection` helper | M |
| 6 | MED | frontend | `Promise.all` the five independent optional fetches in `main()` instead of awaiting them serially | S |
| 7 | MED | frontend | Skip the full per-segment restyle sweep when the zoom weight-bucket didn't change | S |
| 25–28 | LOW | cross-cutting | Add `_geo.py` (Earth radius, haversine, Coriolis, uv→deg) and `_time.py` (ISO format/parse); delete the 3–5 copies each | M |
| — | LOW | *(remaining)* | See per-area sections; batch as convenient | — |

¹ FS-1 is **HIGH as a structural/maintainability priority** — the app runs correctly
today; this is not an operational bug like SEC-1. Treat SEC-1 as the only urgent item.

---

## 1. Security — threat model of the deployed app

The unauthenticated `POST /api/forecast` is well-defended on compute but has memory
and hygiene gaps. Trust boundary as briefed: CMEMS + own cluster trusted; the public
endpoint and static site are the attack surface.

### [SEC-1] (HIGH) Unbounded field-cache residency → memory-exhaustion DoS
`src/whirls_cruise_map/_api.py:327` · `_field_store.py:521`

The `seeds×horizon_h` budget (`_MAX_SEED_HOURS`) bounds CPU but **not resident
memory**. The per-request `StoreField` day cache is sized by
`day_cache_cap_for_starts(min_start, max_start) = max(4, spread_days + 2)` with **no
upper clamp**, and `_batch_advect` never resyncs seeds to a shared wall clock — each
seed keeps its original start-day for the whole run, so *all* of a request's distinct
calendar days stay resident simultaneously (~50 MB/day).

A request of ~30 seeds placed one-per-store-day with `horizon_h ≈ store width` has a
trivial budget (30×720 ≈ 21 600 ≪ 1 000 000) and cheap compute (~1–2 s), yet forces
the **entire store span resident at once** (~1.5 GB for a 30-day store). The handler
is a sync `def` run in the default AnyIO threadpool with **no concurrency gate**, and
`_field_lock` is released before advection, so 2–3 concurrent such requests exceed the
3 Gi pod limit → OOM kill of the forecast service. This directly contradicts the
documented "memory stays flat … the pod's 3 Gi limit" guarantee
(`docs/deployment.md:149`, `_api.py:322-324`), whose own comment endorses "holding its
whole span resident … no separate per-request bound is needed" — that is the bug.

**Fix:** clamp `day_cache_cap_for_starts` to a ceiling (e.g. 8 days) that keeps
several concurrent requests inside 3 Gi, rejecting/clamping larger spreads; add a
module-level semaphore around `_batch_run` (or lower the AnyIO thread limiter);
optionally 422 when the seed-start spread exceeds a fixed number of days. The static
map is unaffected either way, which bounds blast radius to the forecast service.

### [SEC-2] (MED) Catch-all `except`→503 leaks store path and masks real 500s
`src/whirls_cruise_map/_api.py:460` (also `:472`, `_api_parcels.py:253`)

`except Exception → HTTPException(503, f"forecast field unavailable: {exc}")` does two
harmful things on the public surface: (1) any non-`ValueError` bug in
`_batch_run`/`_batch_advect`/`_get_field_index` is reported as a transient "field
unavailable" 503, hiding a real 500-class defect; (2) `str(exc)` echoes internals to
the client — the `FileNotFoundError` at `_api.py:173` names the absolute store dir
(`WHIRLS_FIELD_CACHE`/PVC path). Same at `limits()` and the parcels oracle.

**Fix:** map only the specific "field missing" exceptions (`FileNotFoundError` and
whatever `StoreField` raises for an empty/partial store) to a 503 with a *fixed*
message; let everything else surface as a real 500, logged server-side. Never
interpolate `str(exc)` into a public body. (Resolves SEC-7/#36 as well.)

### [SEC-3] (MED) DOM XSS: third-party ship fields → `innerHTML`, no CSP
`site/map/app.js:3855` (also `shipPopupHtml` `:3661`)

`renderShipInfo` and `shipPopupHtml` string-interpolate ship-fix values straight into
`innerHTML`. For the Marion Dufresne, `mdRows` passes `truewindspeed`, `truewinddir`,
`seatemp`, `airtemp`, `pressure` from the **live third-party** API at
`localisation.flotteoceanographique.fr` (CORS-open, browser-polled, explicitly outside
the trust boundary) with only a `!= null` guard — no numeric coercion, no HTML escape.
A compromised/malicious ship API returning `"<img src=x onerror=…>"` in any met field
runs script on the map origin, which is the **same origin as `/api`**. There is no CSP
in `index.html`.

**Fix:** assign values via `textContent` (build the row nodes), or coerce numerics
with `Number(...)`/run through an HTML escaper. Add a restrictive CSP
(`default-src 'self'; connect-src 'self' https://localisation.flotteoceanographique.fr`)
as defense-in-depth. Impact is bounded (no auth/cookies/secrets on the origin), so
MED, but it is a genuine unescaped sink on in-scope untrusted input.

### [SEC-4] (MED→LOW) No in-app request-body-size limit
`src/whirls_cruise_map/_api.py:233`

`Field(max_length=_MAX_SEEDS)` rejects >2000 seeds only *after* Starlette has buffered
and JSON-parsed the whole body into a Python list. No body-size middleware exists, so a
multi-hundred-MB array is fully materialized (GBs of transient objects) before the 422
fires — compounding SEC-1 under concurrency. Production sits behind nginx (default
`client_max_body_size` 1 MB would 413 first), which is why this is LOW, but the app
relies on an out-of-repo cap absent in the dev flow.

**Fix:** add an ASGI/Starlette body-size guard that 413s bodies well under ~1 MB
(2000 seeds ≈ far less) before parsing — don't depend on the external gateway.

### [SEC-5] (MED→LOW) Seed `lon`/`lat` unvalidated; `inf`/`nan` accepted
`src/whirls_cruise_map/_api.py:212`

`Seed.lon`/`lat` are bare floats — no `ge`/`le`, no `allow_inf_nan=False` — so `nan`,
`inf`, `1e308` all pass. `allow_inf_nan=False` is set on `horizon_h` only, yet the
`ForecastRequest` docstring claims "every field is bounded" and "allow_inf_nan rejects
inf/nan". Current impact is benign (non-finite coords sample off-field, the seed
freezes at step 0 and is skipped-and-counted), so this is a robustness/doc gap, not a
live exploit — but it's an unenforced invariant on an unauthenticated endpoint.

**Fix:** `lon = Field(ge=-180, le=180)`, `lat = Field(ge=-90, le=90)`,
`allow_inf_nan=False` on `Seed`; correct the docstring.

### [SEC-6] (LOW) Parcels oracle: wildcard CORS + unbounded `lat`/`lon`
`src/whirls_cruise_map/_api_parcels.py:233`

The comparison oracle (`serve-parcels`, `:8002`) uses `allow_origins=["*"]` and
unbounded `lat`/`lon` query params, unlike `_api.py`'s scoped dev-only CORS. It is a
PoC, not part of the production deployment, serves only already-public forecast data,
and does bounded single-particle compute — hence LOW.

**Fix:** document that it is never deployed and keep it off the public gateway; if it
ever can be exposed, mirror `_api.py`'s CORS and bound the coords.

### [SEC-7] (LOW) 503 body leaks internal field-store path
`src/whirls_cruise_map/_api.py:461` — same root as SEC-2; fixed by the same change.

---

## 2. Backend API structure

The two-module split is coherent and intentional. Field-index caching (mtime-keyed
rebuild under `_field_lock`) and the `lru_cache` response memoization are reasonable
for a single pod. The sharpest structural issue is the caching layer's coupling to
module globals.

### [API-1] (LOW) `_field_version` reads a global via `assert`; field index resolved twice
`src/whirls_cruise_map/_api.py:399`

`_get_field_index()` computes both the span and the version token (`_index_mtime`)
under the lock but returns only the span, so `_field_version()` calls it for its side
effect and then reads the global `_index_mtime` **outside the lock**, guarded by
`assert _index_mtime is not None`. On a cache miss the index is resolved twice per
request (here for the cache key, again in `_batch_run` at `:272`). The verifier
down-graded the two scary consequences: the `-O`-strips-assert→`None`-key path can't
actually occur (`_index`/`_index_mtime` are only ever set together), and the TOCTOU
window merely keys a *correct* response under a stale version (a wasted cache slot),
not wrong data. Still avoidable coupling.

**Fix:** return `(span, version)` as one locked result and thread the version through
to the endpoint; `_field_version` and the second resolution both disappear.

### [API-2] (LOW) Module-global caches with no reset API push a reset burden onto tests
`src/whirls_cruise_map/_api.py:94`

`_index`, `_index_mtime`, and the `_cached_batch_run` `lru_cache` (plus
`_fieldset`/`_times_epoch` in the parcels module) are process globals with no reset
hook, so `tests/test_forecast_api.py` must poke all three by name around every test
(the parcels pair is in fact *not* reset — supporting the concern).

**Fix:** one `_reset_caches()` entrypoint (or fold the state into a small holder the
app owns) so invalidation is one call, not three named globals.

### [API-3] (LOW) Duplicated epoch/ISO conversion across `_epoch`, `_parse_start`, now-ISO idiom
`src/whirls_cruise_map/_api.py:195` — `_parse_start` re-implements the `_epoch` cast
instead of delegating; `_iso(_epoch(datetime.now(timezone.utc)))` is spelled out at
`:274` and `:474`. **Fix:** delegate `_parse_start` to `_epoch`; add `_now_iso()`.
(Part of the `_time.py` consolidation, IDIOM-2.)

### [API-4] (LOW) Parcels oracle imports generic time helpers from the production module
`src/whirls_cruise_map/_api_parcels.py:215` — imports `_api` only for `_parse_start`
and `_iso`, making the oracle depend on the production endpoint for utilities. **Fix:**
move those helpers to `_forecast` or a shared `_time` util both APIs already reach.

---

## 3. Forecast engine & field store

The advection math is sound: RK4, the vectorized batch twin, the scalar/vector
"bit-identical" mirroring, atomic manifest/day writes, the final-day rollover rule and
hourly-continuity guards all hold. Issues are at the seams.

### [FC-1] (MED) `_batch_advect` materializes every RK4 substep though the consumer keeps ~1/9
`src/whirls_cruise_map/_forecast.py:356`

`positions` is `(N, k_max+1, 2)` float64 with a full row written every 5-min substep,
but the sole caller reads it only at stride `vertex_every` and emits no marks — ~8/9
of the buffer is discarded. At the real cap (`horizon_h ≤ 2400 h` under the seed-hours
budget) the transient buffer is ~180 MB, held on the same 3 Gi pod that concurrently
holds the day-array LRU (see SEC-1). Integration state lives in separate `lon`/`lat`/`t`
working arrays, so dropping rows loses no accuracy.

**Fix:** thread the vertex cadence (and any mark steps) into `_batch_advect`; allocate
`(N, n_vertex_rows, 2)` and record only rows the caller reads (~`vertex_every`-fold
smaller).

### [FC-2] (LOW) `StoreField` drop-in relies on an implicit, unenforced indexing contract
`src/whirls_cruise_map/_field_store.py:599`

`_StoreArray.__getitem__` hard-codes exactly the two index shapes `_forecast` uses
today (scalar time index; 3-tuple of parallel arrays) with no validation. Any new
access pattern `_forecast` might grow (a slice, a scalar 3-index, a negative index)
silently mis-dispatches or raises deep inside a batch run rather than at construction.
Documented in prose and pinned by a test, but not asserted in code. **Fix:** validate
the key shape with a message naming the unsupported pattern, or expose the two
operations as named `plane(jt)`/`gather(jj, iy, ix)` methods on both `_Field` and
`StoreField` so a new pattern is a compile-visible method add.

### [FC-3] (LOW) Cadence divisibility invariant stated backwards in the docstrings
`src/whirls_cruise_map/_forecast.py:177` (and the comment at `:36`)

The docstrings say `mark_hours`/`vertex_min` must divide `step_min`; the code enforces
the reverse (`step_min` divides `vertex_min` and each mark interval, via `round(...)`).
With the shipped defaults `vertex_min=15` does **not** divide `step_min=5`, so the
stated rule is literally impossible for the current config. Because the docstring
invites the interactive API to pass its own knobs, a developer following it can pick
values where `round()` silently snaps a mark to the wrong step.

**Fix:** reword to the correct direction; optionally assert on non-dividing knobs.

---

## 4. Data ingest & build

The ingest→derive seam is well-conceived — clean CSV `/data` boundary, atomic writes,
consistent best-effort layering. `ingest()` reads as a coherent orchestrator.

### [ING-1] (MED) `_derive_slow` is a grab-bag mixing orchestration, rendering, and manual GC
`src/whirls_cruise_map/build.py:302`

Where `ingest()`/`_derive_fast()` are thin orchestrators, `_derive_slow()` is ~130
lines doing field-store update, shading-window fetch + frame planning, three
near-identical speed/flowvis/vorticity render+write loops, landmask bake, meta
assembly, pruning, an explicit `del shading; gc.collect()` with a multi-paragraph
justification, and a second window load + inertial decompose — across seven sequential
`try/except` blocks. The manual GC is a smell: memory ownership is hand-managed in the
orchestrator because the render steps aren't encapsulated.

**Fix:** extract `_render_currents(...)`, `_render_vorticity(...)`,
`_render_inertial(...)`, each owning its `try/except` and letting `shading` go out of
scope naturally (dropping the manual `del`/`gc.collect()`); collapse the three write
loops into one `_write_frames(map_dir, frames)`.

### [ING-2] (LOW) Empty snapshot glob fails ingest with an opaque pandas message
`src/whirls_cruise_map/_clean.py:53` — a valid zip whose internal folder was
renamed/emptied yields `[]`, and `pd.concat(())` raises `ValueError: No objects to
concatenate` with no hint the cause is an upstream layout change. It fails *closed*
(correct), but cryptically. **Fix:** guard the empty case with an actionable message.

### [ING-3] (LOW) `awaiting(clean)` recomputes `tracks()` that `build.py` already holds
`src/whirls_cruise_map/_clean.py:114` — `awaiting()` re-calls `tracks(raw)` (redoing
the sentinel filter + sort) though `build.py` computed `tracks` one line earlier.
**Fix:** derive `awaiting = sorted(set(clean["D_number"]) - set(tracks["D_number"]))`.

### [ING-4] (LOW) `platforms.csv` re-read and re-parsed three times per derive-fast run
`src/whirls_cruise_map/_data.py:395` — `_read_platforms()` has no memoization and is
hit by `read_drifters`, `read_deploy_starts`, and `read_awaiting` in one pass. **Fix:**
read once and thread the frame, or `lru_cache` on the path.

### [ING-5] (LOW) `clean()` `drop_duplicates` keeps the oldest snapshot copy of a repeated fix
`src/whirls_cruise_map/_clean.py:100` — default `keep="first"` on
`(D_number, date_UTC)`; if upstream ever revises a fix's coordinates under the same
timestamp, the correction is silently dropped. The assumption (identity ⇒ identical
payload) isn't defended in code. **Fix:** `keep="last"` (after confirming filename
sort is chronological), or a one-line comment stating the assumption.

### [ING-6] (INFO) Comment claims "lazy" imports that are eager top-level imports
`src/whirls_cruise_map/_data.py:39` — the storage seam eagerly imports `_agulhas`,
`_fetch`, `_ship` purely for four provenance URL strings. **Fix:** correct the comment;
optionally pass provenance URLs in from `build.py` at write time to drop the coupling.

---

## 5. Per-source data modules

Mostly right-sized: `_ship`/`_agulhas` share a clean `fetch_raw`/`parse` split,
`_deploy` is self-contained pure logic, `_currents` is a legitimate xarray/CMEMS
snowflake. Duplication is concentrated in two places.

### [SRC-1] (LOW) IPSL-portal HTTP fetch re-implemented inline in `_agulhas`
`src/whirls_cruise_map/_agulhas.py:66` — `_gliders` already factors the portal fetch
into `_get`/`_get_bytes` (the 403-avoiding `_HEADERS`, `urlopen(timeout=30)`,
`decode('utf-8','replace')`); `_agulhas.fetch_raw` re-declares a byte-identical
`_HEADERS` and re-implements the same dance against the same host. **Fix:** extract a
shared `_portal.get/get_bytes` (natural home for a consistent retry policy too).

### [SRC-2] (LOW) Per-frame render loop copy-pasted three times
`src/whirls_cruise_map/_currents.py:427` — `to_speed_frames`, `to_flowvis_frames`, and
`_vorticity.to_vorticity_frames` share the slice→(sort)→warp→`{valid_time,file,image}`
scaffold; only the inner render differs. (Verifier note: the vorticity loop's sort
lives inside `zeta_over_f`, and return shapes differ, so they're less identical than
the finder claimed — still worth a shared `_render_frames(window, times, kind, render_fn)`.)

### [SRC-3] (LOW) Three parallel `_parse_time` implementations
`src/whirls_cruise_map/_gliders.py:100` — `_gliders`/`_ship`/`_agulhas` each define a
`str → datetime|None` UTC parser; on Python ≥3.12 the gliders four-encoding parser is a
superset of the other two. **Fix:** one tolerant `_parse_time` in a shared module, with
the day-first branch kept strictly a fallback.

### [SRC-4] (LOW) Retry coverage and the `timeout=30` literal applied unevenly
`src/whirls_cruise_map/_agulhas.py:67` — `with_retry` wraps CMEMS + `_fetch._download`
but not the sibling portal/API fetches; `timeout=30` is an inline literal in three
modules while `_fetch` names `_TIMEOUT`. The Agulhas CSV is relied on as last-good
state, so a transient blip silently omits the vessel for that build with no retry.
**Fix:** centralize the timeout constant and give the shared portal helper an explicit
per-source retry policy.

---

## 6. Derived-product modules

The geophysical math checks out where assessable: the vorticity spherical-metric
derivatives and ζ/f sign, the inertial complex-least-squares closed form, and the
Mercator edge-to-edge warp registration. NaN/land handling is careful. Issues are
structural.

### [DER-1] (MED→LOW) Two conflicting point-tuple orderings force fragile reshuffling
`src/whirls_cruise_map/_geojson.py:109`

Within one module, drifter points from `_point()` are `(lat, lon, time)` and
`_segment_motion`/`_haversine_m` consume that order, but glider fixes are
`(time, lat, lon)`, so every glider call site hand-permutes indices
(`(pt[1], pt[2], pt[0])`, etc. — ~6 sites), and `_coord(lon, lat)` is a third ordering.
No name or type catches a transposed index, so a slip silently yields wrong
speeds/headings or lon/lat-swapped geometry. All current permutations are correct, so
it's a latent silent-swap risk, not a live bug. **Fix:** one internal `NamedTuple
Point(lat, lon, time)` built at the boundary (also resolves IDIOM-4/#29).

### [DER-2] (MED→LOW) Mercator warp loops `np.interp` per longitude column
`src/whirls_cruise_map/_raster.py:52` — the interpolation targets and source abscissa
are identical for every column, so `np.searchsorted`+weights computed once and applied
to the whole `(nlat, nlon)` array in one gather gives identical output without the
per-column Python loop. Build-time only (never on the request path), so the verifier
down-graded it to a cleanup, not a material speedup. **Fix:** vectorize with shared
weights.

### [DER-3] (LOW) Derived-product modules reach into `_currents` private helpers
`src/whirls_cruise_map/_vorticity.py:115` — `_vorticity` uses `_currents._quantize_unit`,
`_slice_at`, `frame_valid_time`, `frame_filename`, `N_BINS`. These are layer-neutral
(quantization, time-slicing, frame naming) yet live in the fetch module. **Fix:** move
them into a small shared `_frames.py`/`_raster.py` both layers import.

### [DER-4] (LOW) `OMEGA`, Earth radius, and the Coriolis expression duplicated
`src/whirls_cruise_map/_inertial.py:46` — see IDIOM-1; consolidate into `_geo.py`.

---

## 7. Frontend structure

`index.html` loads Leaflet then a single classic `<script src="./app.js">`
(`index.html:91`, **not** `type=module`, 0 imports/exports), so all **4,564 lines,
102 top-level functions, and every module-level binding share one flat global scope**
with no dependency graph — the only structure is comment banners. The code is
unusually well-commented and internally disciplined (CSS design tokens, consistent
selection/clock patterns, documented pane ordering), but it has outgrown a single
file. This is the headline frontend concern the review was asked to surface. Note the
severity below is **structural/maintainability**, not an operational bug like SEC-1 —
the app runs correctly today.

### [FS-1] (HIGH, structural) Split `app.js` into ES modules — along a concern spine, not the banners verbatim
`site/map/app.js:0` (whole file)

The move is: switch the tag to `<script type=module>` (the browser resolves the graph
natively and offline — no bundler, matching the vendored/same-origin constraint) and
split the file. The 19 `// --- section ---` banners are a good *map* of where the seams
are, but **do not lift them one-banner-one-module** — that reproduces a structural flaw
already latent in the banners.

**Why not the banner-derived list.** The obvious cut (`config.js`, `format.js`,
`selection.js`, `tracks.js`, `controls.js`, `deploy.js`, `inertial.js`, `gliders.js`,
`currents.js`, `ships.js`, `main.js`) silently mixes **two decomposition axes** — some
modules are *by concern* (`selection`, `tracks`, `controls`), others are *by
feature/instrument* (`gliders`, `ships`, `deploy`). The tell is the asymmetry: it gives
gliders and ships their own modules but **no `drifters.js`**. That is not principled — it
is an artifact of the file's history. Drifters are the *core, first* data type, so their
rendering became the "generic" machinery (`buildBatchGroups:1031`, `displayTrack:885`,
`addTrackSegments:901`, plus the instrument palette / batch styling / click-to-highlight /
at-time markers), while gliders (`buildGliderMarkerGroups:3353`, `gliderIcon`,
`gliderPopupHtml`) and ships were added later as **self-contained sections that mirror**
that machinery. So a `gliders.js`-without-`drifters.js` split would carve off the
late add-ons and leave the core type smeared across `tracks`/`selection`/`controls` —
baking in the exact coupling FS-1 exists to remove.

**Recommended shape: a concern spine + thin per-instrument adapters.** Because the render
/ selection / clock / controls machinery is already shared across instrument types, make
*concern* the backbone and express each instrument family as a small adapter implementing
a common `{icon, popup, markerGroups, trackGroups}` contract:

```
core/
  render.js       generic track/marker rendering (displayTrack, addTrackSegments, marker groups)
  selection.js    the one unified Selection helper (FS-2)
  clock.js        register()/tick() fan-out (FS-3)
  controls.js     dock / time slider / cursor readout (1097–1546, FS-4)
instruments/      each implements the same contract, plugged into core/
  drifters.js     ← buildBatchGroups + drifter marker/popup, pulled OUT of the generic soup
  gliders.js      ← already this shape (3295–3436)
  ships.js        ← already this shape (3514 onward: ship tracks + course/speed)
features/         genuinely self-contained subsystems
  deploy.js       the PoC deploy tool (~1360 lines, cleanest first extract — FS-5)
  inertial.js
  currents.js
config.js (17–206), format.js (206–303), main.js (3865–4557, wiring entry point)
```

The evidence that this is the right axis: `buildBatchGroups` (drifter) and
`buildGliderMarkerGroups` (glider) are near-parallel today — making drifters an
`instruments/drifters.js` sibling of `gliders.js` turns that accidental parallel into an
explicit shared contract, and is a natural candidate to unify the two later. Treat the
filename list as a sketch to rework around this spine, not a spec. Start with
`features/deploy.js` (FS-5): it is the most self-contained third of the file and proves
the `type=module` switch at low risk.

### [FS-2] (MED) Four near-identical selection state machines should collapse into one
`site/map/app.js:361`

Four parallel highlight subsystems repeat the same shape — a module-level
`let selectedX`, a `selectX(key)` toggle, an `applyXSelection()` restyle loop, and a
clear branch in the background-click handler: instrument
(`selectedInstrument:336`/`selectInstrument:375`/`applySelection:361`), at-time set
(`:398`/`:484`/`:477`), drop set (`:2532`/`:2548`/`:2542`), deploy track
(`:2533`/`:2606`/`:2600`). The `main()` click handler clears all four in sequence. This
quadruples the surface for state bugs. **Fix:** one `Selection` helper parameterized by
`{getEntries, restyle(entry, isSelected)}`, instantiated four times; drive the
background-click clear by iterating the instances. (Also relieves FE-2's restyle cost.)

### [FS-3] (MED) The clock fan-out is the real data-flow spine but is hand-wired
`site/map/app.js:456`

`updateClock(ms)` is the single tick driving every time-aware layer, but it does so by
reaching into **six independent module-level registries** populated by unrelated
sections, with a load-bearing ordering comment (`:469`, forecast clip must run last to
override the observed head). Any new time-aware layer must both create its own registry
and be manually appended here, and correctness depends on call order in one rAF
callback. **Fix:** a clock module with `register(entry) → unregister` and one `tick(ms)`
that iterates subscribers in an explicit, documented priority; layers register
themselves at build time; the forecast-last precedence becomes a declared priority.

### [FS-4] (MED) The "clock-following tracks" banner spans ~1000 lines / three concerns
`site/map/app.js:521`

The single banner at `:521` runs to the next at `:1548` (~1000 lines) and bundles (a)
observed-track rendering + clock-clipping (`displayTrack:885`, `addTrackSegments:901`),
(b) the **entire** control-dock/UI-chrome builder family (`buildInstrumentRows:1097`,
`buildShadingRows:1241`, `buildControlDock:1308`, `buildTimeSlider:1410`,
`buildCursorReadout:1531`), and (c) low-level track geometry. A reader following the
banner gets no signal the UI builders live here. **Fix:** break at ~`:1097`
(`controls.js`) even before a full split lands.

### [FS-5] (LOW) Deploy tooling is ~1,360 lines / five banners — the cleanest large extract
`site/map/app.js:1548`

The interactive deployment planner (explicitly a PoC) spans five consecutive banners
and ~⅓ of the file, is highly self-contained (own state registries, own selection pair,
own API calls), and `main()` already consumes it through the `deployTool` object built
at `:3957`. **Fix:** extract wholesale into `deploy.js` exporting `buildDeployTool(...)`
— the lowest-risk large extraction and a good first proof of the `type=module` switch.
Isolating it also makes the PoC cheap to gate or replace.

### [FS-6] (INFO) `style.css` is structured, not sprawling
`site/map/style.css:1` — a balancing positive: 1,188 lines but `:root` design tokens,
section comments over essentially every rule group, only 167 rule blocks (generously
commented, not duplicated), and 5 well-scoped media queries (incl. reduced-motion and a
phone breakpoint). No copy-paste/override sprawl. Size is the only cost; no bundler
needed. Optionally co-locate CSS by concern if `app.js` is modularized, but discretionary.

---

## 8. Frontend correctness & lifecycle

The client is careful about clock coalescing, selection-restyle bookkeeping, and
best-effort fetches (`fetchJSON`'s `optional` contract, the memoized limits probe, the
deferred tracks reconcile). No clock/selection race or stale-closure defect surfaced in
that machinery. Weaknesses are around network resilience.

### [FE-1] (MED) `main()` serializes five independent optional fetches
`site/map/app.js:4034` — after the required `latest`, `main()` awaits `meta`,
`vorticityMeta`, `inertialField`, `gliders`, then `awaiting` strictly one-after-another
before building the control dock (the app's primary capability). None depend on each
other. On the at-sea VSAT link the code itself calls out, this adds ~5× RTT to first
interactivity. **Fix:** `Promise.all([...])` the five optionals; only `latest` must
precede `fitBounds`.

### [FE-2] (MED) `applySelection()` restyles every track segment on each zoom / selection
`site/map/app.js:3892` — the `zoomend` handler unconditionally sweeps all ~100k
registered track parts calling each restyle closure (because `trackWeight` depends on
live zoom), and the same full sweep runs on every `selectInstrument()` click. Weight
only changes at three zoom buckets. **Fix:** early-return when the bucket is unchanged;
restyle only the newly/previously-selected keys on a selection change. (Correct anchor
is `:3892`, not the finder's `:4892`.)

### [FE-3] (LOW) No fetch timeout: a stalled forecast API can wedge the deploy tool
`site/map/app.js:1580` — no fetch uses `AbortController`/timeout. `getDeployLimits()`
memoizes the in-flight promise with only `.catch(()=>null)`; a backend that accepts the
connection but never responds yields a promise that never settles, and because it's
memoized, every later placement awaits the same dead promise. `commitDeployment()` is
left exposed (unlike the dock, which `main()` deliberately keeps off this await).
Verifier down-graded to LOW: behind the documented 60 s gateway timeout the realistic
worst case is a bounded stall with no feedback, not a permanent wedge. **Fix:** bound
the limits/forecast fetches with an `AbortController`; don't memoize an aborted result.

### [FE-4] (LOW) Re-polled `agulhas.json` lacks the cache-buster the live ship API uses
`site/map/app.js:4539` — `loadAgulhas()` re-fetches `./data/agulhas.json` every
`SHIP.refreshMs` so a rebuild's fixes appear without a reload, but unlike the live MD
poll (which appends `cb=Date.now()`) it has no cache-buster, so a static host with any
positive freshness lifetime serves it from cache and the new fixes never surface.
**Fix:** append `?cb=${Date.now()}` or fetch `{cache:'no-store'}`.

### [FE-5] (LOW) `resolveApi()` silently falls back to origin-root `/api/forecast`
`site/map/app.js:1566` — the same-origin branch strips a trailing `map/` from
`pathname`; if the site is ever mounted at a non-`map/` subpath the regex fails,
`prefix` becomes `''`, and the API resolves to `/api/forecast` at the origin root — a
silent mis-resolution (the design comment even claims "no origin-root assumption").
Harmless for the documented deploys. **Fix:** derive the prefix from the document base
URL, or `console.warn` when the pattern doesn't match so it fails loudly.

---

## 9. Cross-cutting Python idioms

The package is generally idiomatic and well-typed (NamedTuple/BaseModel where records
matter). The recurring weakness is un-centralized small primitives.

### [IDIOM-1] (LOW) Earth radius + haversine duplicated across four modules
`src/whirls_cruise_map/_geojson.py:39` — `_EARTH_RADIUS_M = 6_371_000.0` in `_forecast`,
`_vorticity`, `_geojson`; `_EARTH_RADIUS_KM = 6371.0` in `_deploy`; the haversine body
is line-for-line identical in `_geojson._haversine_m` and `_deploy._haversine_km`.
**Fix:** `_geo.py` with one `EARTH_RADIUS_M` and one `haversine_m(...)` (km caller
divides by 1000).

### [IDIOM-2] (LOW) Three ISO-8601 formatters, two named `_iso` with incompatible signatures
`src/whirls_cruise_map/_field_store.py:172` — `_iso(when: datetime)` vs
`_iso(epoch_s: float)` (`_api.py:190`) vs `iso_utc(when)` (`_data.py:72`); the
`.replace("Z","+00:00")` parse idiom is smeared across five files. The same-name /
different-signature collision is a real trap. **Fix:** a `_time.py` with `iso_z(dt)`,
`iso_z_from_epoch(s)`, `parse_iso(s)`; drop the per-module copies.

### [IDIOM-3] (LOW) m/s→deg/s conversion duplicated within `_forecast.py`
`src/whirls_cruise_map/_forecast.py:127` — the scalar (`math.cos`) and vectorized
(`np.cos`) forms are the same conversion, with a docstring at `:211` asking the two to
stay arithmetically identical — an invariant that exists only because it's duplicated.
**Fix:** one `_uv_to_deg_per_s(u, v, lat)` on numpy ufuncs (which accept scalars).

### [IDIOM-4] (LOW) Positional tuple reordering between fix tuples and `_segment_motion`
`src/whirls_cruise_map/_geojson.py:141` — same root as DER-1; four sites reshuffle
`(time,lat,lon)`↔`(lat,lon,time)` by raw index. **Fix:** unify the ordering or use a
`NamedTuple`.

### [IDIOM-5] (LOW) `range(len(...))` index loops that want `zip`/pairwise
`src/whirls_cruise_map/_geojson.py:139` — clean `zip` candidates at `_api.py:141`
(`zip(bounds, bounds[1:])`) and `_gliders.py:457` (`zip(secs, lat, lon, nat)`). The
verifier noted `_geojson.py:139` and `_api.py:348` genuinely need the index (they
subscript into other arrays), so `enumerate` there, not plain `zip`.

### [IDIOM-6] (INFO) Over-broad `except Exception` around one pure-Python parse
`src/whirls_cruise_map/_ship.py:55` — `json.loads(text)` wrapped in `except Exception:
return []` where `json.JSONDecodeError` is the only expected failure, so a real bug
would masquerade as "no fixes". (Verifier refuted the finder's breadth claim — the
cited `_gliders` lines wrap *network* fetches, which are legitimately broad; the parse
loops there already use narrow catches.) **Fix:** narrow this one catch to
`json.JSONDecodeError`.

---

## What's healthy (don't regress it)

- **Ingest→derive `/data` seam** with atomic writes and best-effort layering — a dead
  upstream drops one artifact, never the build or the last-good file.
- **Public endpoint compute hardening** — seed cap, horizon cap, seeds×hours budget,
  `extra="forbid"`, `allow_inf_nan` on the horizon, gzip floor, scoped dev-only CORS.
- **Advection correctness** — RK4 and its vectorized twin are bit-identical by
  construction; the scalar/vector mirroring, rollover rule, and continuity guards hold.
- **Geophysical math** — vorticity metric derivatives, ζ/f, the inertial least-squares
  form, and the Mercator warp registration all check out.
- **Frontend clock/selection machinery** — coalescing and restyle bookkeeping are
  careful; no race or stale-closure defect found.
- **Documentation** — `docs/` and `plans/` are unusually thorough and match the code.

## Appendix — coverage & caveats

- Reviewed at commit `062ee74` (`main`). Vendored Leaflet (`site/map/vendor/`) was out
  of scope.
- 41 raw findings → 39 after adversarial verification (2 refuted, several down-graded).
- Severity reflects a *pre-alpha, single-pod, internal-API-is-free, low-value research*
  context: a wide-open DoS outranks a theoretical info leak.
- Line numbers are from this commit and will drift as fixes land.
