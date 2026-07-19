# 046 — Cache the observed-drifter forecast (server-side response cache)
> Implemented. See [docs/deploy_tool.md](../../docs/deploy_tool.md) — the server-side forecast response cache.

The map fires the observed-drifter forecast at page load for **every client**, and it
is a **byte-identical** `POST /api/forecast` at a given data version — seeds come off
`latest.geojson`, horizon/direction are fixed. The API is one uvicorn process on one pod
(`replicas: 1`, no `--workers`), so recomputing that same ragged-start run per client
burns the pod's CPUs redundantly. Memoize it.

This is the cheap, server-only, no-behavior-change win. The higher-value
**precompute-as-static-artifact** alternative (build writes `drifter_forecast.geojson`,
frontend GETs it) is recorded in the perf issue as a follow-up, not done here.

## Design (`_api.py`) — a `functools.lru_cache`, a few lines

- `_cached_batch_run(field_version, direction, horizon_h, seeds)` wraps `_batch_run`
  under `@lru_cache(maxsize=32)`. `seeds` is a hashable tuple of `(lon, lat, start)`
  triples; the endpoint builds it from the request.
- **Version key = the field-store manifest mtime** (`_field_version()` → `_get_field_index`
  refreshes `_index_mtime`, bumped on every slow build write). A store update ⇒ new key ⇒
  the old entries fall out by LRU. A changed seed set / horizon / direction is a different
  key. The request body carries the `latest.geojson` version implicitly (seeds are in it).
- **No `analysis_edge` handling needed.** It is the run's only wall-clock field, and the
  client does not read it back (the observed path uses client-side `nowMs`; `analysis_edge`
  has zero consumers in `site/`). A frozen copy in a cached response is harmless.
- **Exceptions aren't cached** (`lru_cache` only stores returns), so a 422 (bad request) /
  503 (empty store) recomputes. `lru_cache` is thread-safe; concurrent cold-cache misses
  may each compute once (no single-flight) — acceptable, and far cheaper than the current
  per-client recompute.

Custom deploy-tool / arbitrary-seed runs have unique bodies ⇒ miss and are served live.

## Scope

`forecast` endpoint calls `_cached_batch_run(_field_version(), …)` instead of `_batch_run`;
`_batch_run` itself is unchanged (still the direct target of the engine tests). No
request/response shape change.

## Verification (`test_forecast_api.py`)

- An identical request computes `_batch_run` **once** (second served from cache).
- Distinct horizon / direction / seed set ⇒ separate keys ⇒ recompute.
- A manifest-mtime bump (a store write) **invalidates** ⇒ recompute.
- The autouse field-index reset fixture also `cache_clear()`s the response cache.

## Composition with the forecast perf work (issue, separate MR)

Orthogonal: this cuts the *count* of observed recomputes (per-client → per-data-version);
the numba/lean-field speedup cuts the *cost* of whatever still computes (the first client
per version, and every interactive deploy run). Neither depends on the other.
