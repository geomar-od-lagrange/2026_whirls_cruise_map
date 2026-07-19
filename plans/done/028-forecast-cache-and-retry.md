> **Implemented** — see [docs/deploy_tool.md](../../docs/deploy_tool.md)
> ("Surviving the 60 s gateway timeout: result cache + client retry"). The seed cap stays
> at **2000**: lowering it was considered, but the retry/cache *recovers* an over-timeout
> placement, so the cap need not be sized to one 60 s window.

# Forecast cache + client retry (survive the 60 s gateway timeout)

Lower the batch-forecast seed cap and make a large placement survive the
OpenShift router's 60 s network timeout via a server-side result cache the
client can simply retry into.

## Why

The deploy tool POSTs a whole placement's seeds to `POST /api/forecast`; the API
advects each seed with RK4 through the CMEMS window. That loop is GIL-bound and
serialises on the single sync worker, so a big placement can run for a while.

The deployment gateway (plan 017) fronts the API behind an OpenShift Route whose
network timeout is **60 s**. A batch that advects longer than that has its
connection cut by the router (a 504, or a dropped connection) — the browser's
`fetch` fails and no forecast is drawn, even though the work was nearly done.

Two levers were on the table; only the second ships:

1. **Cap the work — considered, not taken.** Lowering the per-request seed cap
   (e.g. 2000 → 1000, roughly what fits in one 60 s window) would make each
   placement smaller. But it is unnecessary once the timeout is recoverable: a
   placement that advects longer is *recovered*, not forbidden. So the cap stays
   at **2000** — its only job is bounding the pod-exhaustion worst case — keeping
   its single source of truth (`_MAX_SEEDS`, enforced by the request model,
   advertised by `GET /api/forecast/limits`).

2. **Make the timeout recoverable.** A FastAPI *sync* endpoint runs in the
   threadpool, and a sync task keeps running to completion after the client
   disconnects (sync work is not cancellable). So the server can finish the
   advection past the router's cut, **cache** the result keyed by the request,
   and the client just **re-POSTs the identical body**: the retry finds the
   result already computed (a fast cache hit) or, if the first compute is still
   running, **coalesces** onto it. No job IDs, no polling, no 202/Location — the
   same POST, retried, is the whole protocol.

## Design

### Server — result cache + single-flight (`_api.py`)

- A process-local `OrderedDict` cache, bounded to a handful of recent results
  (each entry is a whole placement's FeatureCollection, so the working set is a
  few, not thousands). One pod, one worker — the deployment the module already
  assumes — so the cache is coherent across a client's retries (they hit the same
  pod).
- **Key** = SHA-256 of `(seeds, horizon_h, mark_step_h, field_version)`. The
  field version is the window file's mtime, folded in so a fresh cron write
  rotates keys — a new field never serves a stale forecast. A retry re-POSTs the
  byte-identical body, so it hashes to the same key.
- **Single-flight.** Each key maps to a slot with a `threading.Event`. The first
  request (leader) computes, fills the result, sets the event; any concurrent
  identical request (a retry that arrives mid-compute) is a follower that waits
  on the event and returns the same result — the GIL-bound advection runs **once**
  even while a retry is in flight, so retries never pile a second contending
  compute onto the worker.
- **Failures are not cached.** A leader that raises removes its slot (after
  waking followers with the same error), so a later retry recomputes rather than
  replaying a transient 503. `ValueError → 422` / missing-window `→ 503` contracts
  are unchanged.

### Client — retry the identical body (`app.js`)

- Wrap the forecast POST in a retry loop that re-sends the **same** body string
  (so the server key matches). Each attempt is bounded a touch under the router's
  60 s by an `AbortController`, so the retry cadence is ours, not the router's.
- Retry on the timeout signals only — a `502`/`504` gateway status or an
  aborted/dropped connection. A real `4xx`/`5xx` from our app (e.g. a `503`
  "field unavailable", a `422`) returns immediately and surfaces its message.
- The status line reports the retry ("still forecasting… (retry k/n)") so a slow
  placement reads as progress, not a hang.

## Not doing

- **Async job queue (202 + poll).** More machinery than a single-user planning
  tool needs; the cache+retry gets the same "survive the timeout" outcome with no
  new endpoints or client state.
- **Disk/PVC-persisted cache.** In-process is enough for retry-after-timeout (the
  retry hits the same warm pod within seconds); persistence would only matter for
  cross-pod or cross-restart reuse, which this doesn't need.
- **TTL expiry.** The LRU cap bounds memory and the field-version key rotates
  stale entries out on each cron write; a time-based sweep adds a knob for no gain
  here.

## Steps

1. `_api.py`: add the cache + single-flight; route the endpoint through it.
   `_MAX_SEEDS` stays 2000 (see Why — lever 1 not taken).
2. `app.js`: retry the identical body on timeout signals, with status feedback.
3. `docs/deploy_tool.md`: document the cache/retry contract and the 60 s
   rationale; note the cap is not sized to the timeout.
4. `tests/test_forecast_api.py`: cache hit computes once; field-version and
   distinct-request both recompute; failures aren't cached; single-flight
   coalesces concurrent identical requests; the cache is bounded.

When done: fold the contract into `docs/deploy_tool.md`, move this plan
to `plans/done/` with a one-line pointer, and update `ROADMAP.md`.
