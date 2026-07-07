> Implemented — see [docs/interactive_forecast.md](../../docs/interactive_forecast.md).
> The single-click +12 h tool this describes is superseded by the one polyline
> Deploy tool + batch API ([023](023-simplify-deploy-polyline.md)).

# Interactive click-to-deploy drift forecasts

Click a position on the map → advect a passive particle **+12 h** through the
CMEMS current field server-side → draw a solid line with a dot every 3 h; each
click adds another. The generalization to *patterns* (a batch of drifters
deployed along the ship's path, each entering the water at its own time) is the
open work.

## The decisive choice: compute server-side, ship only the answer

The exploration weighed advecting **client-side** (ship the field to the browser)
against a **live API** (keep the field on the server). The API won, on transport:

- Shipping the field is ~26–52 MB (full-res window) or ~0.1–2 MB (the
  `_inertial.py` decomposition, at reduced fidelity), and the deployed GitLab
  Pages cache defeats reuse — **uncompressed, deployment-scoped ETags,
  `max-age=600` vs a ~10-min rebuild** — so a reload re-pulls the whole field.
  Hostile to the at-sea VSAT link.
- The API keeps the ~66 MB window in server memory and returns a **~1–2 KB
  polyline** per forecast — less than the existing `forecast.geojson`, and it
  scales with *particles requested*, not with the field. See
  [docs/interactive_forecast.md](../docs/interactive_forecast.md).

## Plan of work

- **Phase 0 — architecture. DONE.** Chose the live-API route over client-side
  advection (transport + VSAT caching, above). Two endpoints (static `:8000`,
  API `:8001`) so the static half can fall back to Pages; one origin under the
  [017](017-whirlsview-openshift.md) gateway.
- **Phase 1 — RK4 API + deploy-mode UI. DONE (PoC).** `_api.py` (FastAPI) reuses
  the build's RK4 (`_forecast._integrate`, now parameterized on horizon/marks,
  with shared `_anchor_t0` / `_advection_feature`); one hourly window fetched
  once, disk-cached, held in memory. `GET /api/forecast?lat=&lon=&start=` returns
  a GeoJSON Feature. Client `app.js` gains a **Deploy forecast** toggle control,
  a resolved API base (`resolveForecastApi`), and a green +12 h line with 3/6/9/12 h
  dots. `start` (ISO-8601) is locked client-side to the displayed CMEMS snapshot
  time, so the track begins at the field-on-the-map's instant.
- **Phase 2 — validation vs OceanParcels v4. DONE.** `_api_parcels.py`
  (`serve-parcels`, `:8002`) advects the *same* window with parcels v4 (`main`);
  `tmp_parcels_compare/` measures agreement + performance. Result: the engines
  agree to **metres** over 12 h (the ~0.067 % residual is the `deg2m` constant),
  and RK4 is **~100× faster** per single-particle forecast — so **RK4 is the
  interactive engine, parcels the correctness oracle / batch-ensemble tool.**
  Installing parcels pinned the project to Python 3.12 and pulled its dep tree
  from conda-forge.
- **Phase 3 — deployment patterns. OPEN.** Respect a deployment *run*: a start
  time and the ship's speed/heading generate per-particle `(x, y, t)` seeds (a
  row/transect of drifters entering the water at staggered times), each advected
  from its own `start`. A batch endpoint (array of seeds → FeatureCollection),
  and a client pattern editor. The `start`-per-particle seam and the
  window-bounds check are already in place; the window `fwd` span must cover
  `run duration + horizon`.
- **Phase 4 — productionization. OPEN.** Move the field to the
  [017](017-whirlsview-openshift.md) slow-tier: a CronJob writes the window to a
  shared volume; the API switches from lazy-fetch-with-TTL to **load-latest +
  mtime-reload** (no CMEMS creds, no egress in the API pod). Deploy the API as
  the `/analysis` FastAPI `Deployment` behind the gateway. A small `/api/window`
  bounds endpoint lets the client clamp the start-time picker so users don't hit
  the 422.

## Open decisions

- **`deg2m` constant.** Keep `R = 6.371e6` (mean Earth radius, more physical) and
  cite the 0.067 % as a known negligible convention difference, **or** adopt
  parcels' nautical-mile `111120` (set `R = 6 366 707`) so the RK4 is a bit-exact
  parcels reference. Either is defensible.
- **Window span / refresh.** Expose `back_h`/`fwd_h` and the TTL as config; size
  `fwd_h` to the longest deployment run + horizon once Phase 3 lands.
- **Horizon.** +12 h is the current PoC target; deployment planning may want
  longer (the integrator and window just need widening).

## Out of scope

- Client-side advection over a shipped field (rejected in Phase 0).
- Windage / Stokes / drogue-depth corrections — the line is surface-current-only,
  the same caveat as the build forecast ([docs/forecast.md](../docs/forecast.md)).
- Parcels as the interactive engine (100× too slow per click; kept as oracle).
