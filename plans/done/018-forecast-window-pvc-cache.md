> **Implemented.** See [docs/deployment.md](../../docs/deployment.md)
> § *The field: one window, cron-written, reloaded on change* for the current state.

# 018 — Forecast API reads the u,v window from the PVC (no per-process fetch)

Closes the map-repo half of issue #3 (deployment side lives in `oc_gateway`).
This is ROADMAP #15 Phase 3 / #22 productionization, plans/017 forecast **(B)**.

## Problem

`_api.py` fetches one CMEMS window on the **first** request and holds it for the
**process lifetime**. The 12 h TTL in `_load_window()` only fires at process
*startup* (the sampler is built once, never rebuilt), so a long-lived
single-replica pod **never refreshes at runtime**: its forward window edge is
consumed by pod uptime until "now"-seeded 48 h forecasts truncate, then die.
Secondary cost: the API pod holds `cmems-creds` + internet egress only for this
fetch.

The slow build cron **already fetches this exact window** (`_derive_slow` →
`fetch_field_window` → forecast/hindcast/inertial) and discards it. So: the
producer persists the window it already has; the consumer reads it instead of
fetching.

## Design

**Producer — `build.py` `_derive_slow`.** Widen the *single* window fetch to the
forecast API's reach and reuse it (no second fetch):

- Fetch `back=FORECAST_WINDOW_BACK_H (12)`, `fwd=FORECAST_WINDOW_FWD_H (60)`.
- forecast/hindcast are unaffected — they advect ±6 h, well inside either width,
  same hourly slices → byte-identical output.
- **Inertial decomposition is width-sensitive** (`_inertial.decompose` relies on
  the ~24 h span staying under an inertial period to keep mean vs NI separated).
  So slice the wide window back to the narrow `WINDOW_BACK_H + WINDOW_FWD_H` (24 h)
  span before `decompose` — `inertial_field.json` stays identical.
- Persist the wide window atomically (`*.tmp` + `os.replace`) to
  `<map_dir>/_cache/forecast_window.nc` whenever `window is not None`. Under
  `site/map/data/`, already git-ignored (`site/map/data/` + `*.nc`).

**Window sizing — single source of truth (`_currents.py`).** The served window
must cover a full run started at "now" even when the cache is one slow-cron
cadence stale: `fwd ≥ FORECAST_HORIZON_H + SLOW_CADENCE_H` (48 + 12 = 60). Tie
the reach to those drivers so a horizon bump can't silently outrun the window.
`_api._DEFAULT_HORIZON_H` reads `_currents.FORECAST_HORIZON_H` — one horizon
constant, shared by the request default and the window sizing.

**Consumer — `_api.py`.** Replace fetch-with-TTL + once-per-process sampler with
**load-latest + reload-on-mtime**:

- Window path from env `WHIRLS_FORECAST_WINDOW`, default `<repo>/site/map/data/
  _cache/forecast_window.nc`.
- `_get_sampler()` (kept as the seam tests monkeypatch): under `_field_lock`,
  `stat` the file each request, rebuild `_Field` only when mtime changes. A fresh
  cron write is picked up within one request, no restart.
- `_load_window()` reads the file (no fetch). Remove `_currents.fetch_field_window`,
  `copernicusmarine`, all egress from the API. Missing/unreadable file → 503
  (unchanged fetch-failure contract).
- `_api_parcels` keeps using `_api._load_window()` (offline oracle, reads the same
  PVC file); update its path reference.

## Acceptance (from the issue)

- [x] Slow cron writes `…/_cache/forecast_window.nc` atomically each run, reusing
      the window it already fetched (no second CMEMS pull).
- [x] API serves reading only that file; warm-file restart is instant.
- [x] Fresh cron write picked up without a restart (mtime reload).
- [x] API pod carries no creds / no egress; missing file → 503.
- [x] A "now"-seeded 48 h forecast is full-length between cron runs
      (`fwd ≥ horizon + cadence`).
- [x] `inertial_field.json` / forecast / hindcast unchanged (narrow slice for
      decompose; wider forward window is inert for ±6 h advection).
- [x] docs/deployment.md reflects PVC-load + mtime-reload.

Gateway-side (`data/_cache/` unrouted, PVC mount, drop creds/egress) is an
`oc_gateway` follow-up, tracked there.
