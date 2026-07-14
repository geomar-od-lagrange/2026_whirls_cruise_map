> Implemented. See [docs/deployment.md](../../docs/deployment.md)
> ("Batched, not looped") for the current state.

# Vectorize the batch forecast; retire the 60 s-timeout machinery

## Problem

`POST /api/forecast` advects each seed with a pure-Python **scalar** RK4 loop
(`_forecast._integrate` via `_api._batch_forecast`): per seed ~576 steps × 4
derivatives × a Python bilinear over 4 corners, serialised under the GIL on the
single sync worker. Measured against the real cached window (dims time=74,
lat=481, lon=541):

- **23 ms/seed**, perfectly **linear** in seed count.
- 1000 seeds ≈ 20 s; **2000 seeds (the cap) ≈ 46 s**, right against the
  deployment gateway's **60 s** network timeout.
- `_Field` build (~0.1 s, mtime-cached) and JSON are negligible. The scalar loop
  *is* the walltime.

That single slow loop is the reason the whole timeout-survival stack exists
(plan 028): the server result-cache + single-flight (`_cached_forecast` and
friends, ~130 LOC in `_api.py`) and the client retry loop
(`postForecastRetrying` in `app.js`) are all compensation for a compute that can
outrun the router.

## Decision

Replace the per-seed scalar loop with a **vectorized numpy RK4** that advances
**all seeds together in step-index lockstep**, then — because the timeout is
gone with a >40× margin — **remove** the cache/single-flight/client-retry
machinery. Keep the mtime field-reload and the seed cap (its DoS/memory
justification is unchanged).

### Why vectorized numpy (four candidates prototyped + benchmarked on the real window)

All four reproduced the current output bit-for-bit (0.0 m max vertex error);
they differ on speed, dependency cost, and operational risk:

| Approach | Speedup @2000 | n=2000 walltime | New dependency | Verdict |
|---|---:|---:|---|---|
| **Vectorized numpy RK4** | **~40×** | **~1.16 s** | **none** (numpy is core) | **chosen** |
| scipy `RegularGridInterpolator` | 34× | ~1.3 s | scipy direct; *slower* than plain numpy + a 1-cell upper-edge divergence | worse than numpy on every axis |
| numba `@njit` | 87× warm | ~0.53 s | numba direct + **4.1 s cold compile** on a fresh pod, duplicated kernel, per-arch JIT | not needed |
| multiprocessing pool | 9.5× *on 12 cores* | ~4.8 s | none, but **~1–2× on a 1–2 CPU pod** + fork-from-threaded deadlock risk | least applicable |

numpy wins because it is simultaneously a timeout-killing speedup, **zero new
dependency/coupling**, and architecturally trivial for one GIL-bound worker — no
pool lifecycle, no stale-field re-fork, no cold JIT compile, no threading layer.
numba is faster in absolute terms but 0.53 s vs 1.16 s is meaningless when both
are far under 60 s, and it buys that with a cold-start compile (bad on a fresh
AMD64 pod — we test on arm64), a duplicated nopython kernel to keep bit-identical
to `_Field`, and cross-arch JIT complexity. Since numpy gets us far, we don't
touch JIT (per project direction). The env is free to add mature deps, but numpy
needs nothing added, so that latitude isn't required here.

### Why it stays bit-identical

The vectorized sampler mirrors `_Field.velocity` + `_deriv` **exactly**: the same
corner order (`w00·c00 + w10·c10 + w01·c01 + w11·c11`), the same `(1-wt)·lo +
wt·hi` time lerp, the same `dt/6·(k1+2k2+2k3+k4)` combine, the same
`cos(radians(lat))`. Land handling falls out of NaN propagation — a land corner
is NaN and `w·NaN = NaN` (even `0·NaN = NaN`), so any land corner voids the
sample just like `_bilin`'s explicit "any NaN corner → None". Off-grid uses the
same `0 ≤ ix < nlon-1` rule via `searchsorted(side="right")-1` (the exact
upper-edge case scipy RGI got wrong). A seed freezes at its own `n_steps` or the
first step any RK4 stage yields NaN — the same cell/edge/window truncation the
scalar `_integrate` does with `break`. Verified 0.0 m on a 50-seed set (30 %
near-coast + out-of-window), identical forecast/skip counts and marks.

## Implementation

1. **`_forecast.py` — add the vectorized path next to the scalar one** (so the
   two samplers that must stay in sync live in one file):
   - `_vec_deriv(field, lon, lat, t)` — vectorized twin of `_deriv`: `(dlon,
     dlat)` deg/s for numpy arrays, NaN where scalar `velocity`/`_deriv` returns
     `None`. Reuses `field.lons/lats/u/v/times` (no copy).
   - `_batch_advect(field, lon0, lat0, t0, n_steps, *, step_min=STEP_MIN)` —
     forward-only lockstep RK4; per-seed arrays in, returns `P (N, Kmax+1, 2)`
     full-precision positions (row 0 = head) and `completed (N,)` last-good step.
   - The scalar `_integrate`/`_rk4_step`/`_deriv`/`_Field.velocity` **stay** —
     the build's ±6 h per-instrument `forecast_geojson`/`hindcast_geojson` keep
     them (few instruments; hindcast needs `direction=-1`, which the forward-only
     batch doesn't cover).

2. **`_api.py` — rewrite `_batch_forecast`** to compute the per-seed arrays
   (`run_start`, `offset_h`, `horizon_i`, `alive0`, `n_steps_i`) vectorized, call
   `_forecast._batch_advect`, then run the emit loop (coords at vertex steps,
   marks from `_seed_marks` shifted by `offset_h`). Reuses `_seed_marks`,
   `_anchor_t0`, `_iso`, `_COORD_NDIGITS` unchanged, so the FeatureCollection is
   byte-for-byte the same.

3. **`_api.py` — delete the timeout machinery**: `_Slot`, `_cache`, `_cache_lock`,
   `_cached_forecast`, `_cache_key`, `_evict_locked`, `_field_version`,
   `_FOLLOWER_WAIT_S`, `_CACHE_MAX_ENTRIES`, and the now-unused imports
   (`hashlib`, `json`, `OrderedDict`). `forecast()` calls `_batch_forecast`
   directly. Keep `_get_sampler` / mtime reload and the `threading.Lock` field
   guard. Rewrite the `_MAX_SEEDS` docstring: the cap is a DoS/memory bound only;
   even the full cap now advects in ~1–2 s, so the timeout no longer touches it.

4. **`app.js` — drop the client retry**: remove `postForecastRetrying`,
   `FORECAST_ATTEMPT_MS`, `FORECAST_MAX_ATTEMPTS`, `FORECAST_RETRY_STATUS`,
   `FORECAST_BACKOFF_MS`, `sleep`, and the "still forecasting… (retry k/n)"
   status branch; `placeDeployment` does a plain `fetch(FORECAST_API, …)`. Keep
   `apiErrorText`, the limits pre-check, and the error/`catch` paths.

5. **Tests** (`tests/test_forecast_api.py`): add a **bit-identity guard** — a
   scalar reference batch-forecast (the old per-seed `_advection_feature` loop)
   vs the new vectorized `_batch_forecast` on a synthetic window with a NaN land
   patch and staggered starts (ocean-drift, coast-truncate, out-of-window-skip
   cases), asserting full equality (properties + coords + marks). Remove the
   obsolete cache/single-flight tests (`test_cached_forecast_*`,
   `test_cache_is_bounded`, `test_evict_locked_keeps_pending_slots`,
   `test_follower_recomputes_when_leader_fails`, the `_fresh_cache` fixture). Keep
   the marks/skip/limits/window/reload tests.

6. **Docs** (`docs/deployment.md`): drop the "Surviving the 60 s
   gateway timeout" section; update "The batch endpoint" and the seed-cap
   paragraph to state the vectorized cost (n=2000 ≈ 1–2 s, far under the timeout)
   and that the cap is a DoS/memory bound. Note the vectorized engine as the
   batch path, scalar retained for the build's per-instrument forecast/hindcast.

7. Clean up `tmp_forecast_speedup/` after porting.

## Known ceilings / non-goals

- The transient `P (N, Kmax+1, 2)` float64 array is ~18 MB at n=2000/48 h,
  ~92 MB at n=2000/240 h (the model's horizon cap). Fine for the pod; if a much
  larger cap is ever wanted, store only vertex+mark rows (more code, since
  staggered marks don't always land on vertex steps). Not needed now.
- Seed cap stays **2000** (a modest bump is a separate product call, not required
  by this speedup). Forward-only: the build's backward hindcast keeps the scalar
  path.
- Single-flight is removed too: with ~1 s computes the GIL-contention it guarded
  against is immaterial, and it embedded a load-bearing single-pod/single-worker
  assumption that constrained future scaling — exactly the workaround the project
  ethos says to eliminate rather than carry.
