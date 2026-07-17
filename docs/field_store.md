# The hourly-current field store

The virtual-deployment engine ([deployment.md](deployment.md)) advects drops
through the CMEMS surface-current field, and the speed/vorticity/flow overlays
([currents.md](currents.md)) are rendered from the same field. That field is
kept on disk as an **incremental per-day store** — one small netCDF per UTC day
plus a manifest — that a slow build run maintains and that both the build's own
render step and the live forecast API read back.

## Why a separate store, not part of `/data`

The download product `/data` ([data.md](data.md)) is deliberately **observations
only**: no CMEMS model field ever lands there. The field store is the opposite
kind of artifact — a model field, fetched from Copernicus Marine, re-pulled and
re-finalized on the build's own cadence — so it lives in its own tree with its
own lifecycle rather than folded into the observations directory. It is also not
a *published* artifact: unlike `site/map/data/` (served to the browser) or
`/data/` (served for download), the store sits in a directory **no HTTP server
mounts**, because raw hourly u/v over the whole cruise is both large and not
something a client ever needs whole — only the small advected answers ship
([deployment.md](deployment.md)).

## Layout

```
cache/field/
  field_manifest.json        span + per-day index (atomically rewritten)
  uv_2026-06-28.nc           one UTC day: 24 hourly steps, uo/vo float32, BBOX
  uv_2026-06-29.nc
  …
```

Each `uv_YYYY-MM-DD.nc` holds exactly that UTC day's 24 hourly steps
(`[00:00 … 23:00]`) of `uo`/`vo` over the cruise `BBOX`, cast to float32
(~50 MB/day). Day files **do not overlap** — the one-hour bracketing that
integration needs across a day boundary is done at read time (below), not by
storing duplicate edge hours.

The store directory is resolved from `WHIRLS_FIELD_CACHE` (env), else a
repo-local `cache/field/`. It sits **outside `site/`** so neither `pixi run
serve` nor, in the hosted deployment, the frontend pod ever serves it — the same
structural reason the store replaces the old single `forecast_window.nc`, which
lived under a subtree that *was* statically served and so was likely publicly
fetchable. The store is mounted read-only into the API and read-write into the
build; the frontend never sees it.

### The manifest

`field_manifest.json` is one JSON object, rewritten atomically (`*.tmp` +
`os.replace`) after **every** completed day rather than once at the end:

```json
{
  "dataset_id": "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
  "bbox": {"lon_min": -10.0, "lon_max": 35.0, "lat_min": -55.0, "lat_max": -15.0},
  "tmin": "2026-06-28T00:00:00Z",
  "updated": "2026-07-13T09:12:00Z",
  "days": {
    "2026-06-28": {"file": "uv_2026-06-28.nc", "final": true,  "fetched": "…Z"},
    "2026-07-13": {"file": "uv_2026-07-13.nc", "final": false, "fetched": "…Z"}
  }
}
```

`dataset_id`/`bbox` record what the day files were fetched under, so a later run
with a changed config is caught rather than silently mixing incompatible grids
(below). `days` maps each UTC date to its file, whether it is `final`, and when
it was fetched. The atomic per-day rewrite is what makes a killed backfill
**resumable**: whatever hit disk before the kill is exactly what the next run
sees as already done.

## The coverage span and its config seam

The store covers `[tmin, tmax]`:

- **`tmin`** is a single configuration parameter — `_currents.FIELD_TMIN`
  (`WHIRLS_FIELD_TMIN` env, default `2026-06-28T00:00:00Z`, the cruise start).
  Alongside `BBOX`, it is the seam for pointing the app at another cruise or
  region; it is not scattered as a literal.
- **`tmax`** is read live from the CMEMS catalogue: `_describe_time_range`
  probes the window dataset's advertised time-axis reach (its forecast edge,
  ~now + 10 d). A catalogue/network/auth failure degrades to a conservative
  `now + 10 d` fallback rather than aborting the run.

The span grows ~1 day/day; the store is sized for the end-of-scenario footprint
(~1.3 GB and rising ~50 MB/day), which is why neither read path ever loads it
whole.

## The write side: `update_store`

A slow build run (`derive --tier slow`) calls `_field_store.update_store`, which
fetches every UTC day in `[tmin, tmax]` that is **missing or not yet `final`**,
one `copernicusmarine.subset` call per day (bounded memory, ~50 MB each), and
returns the freshly rewritten manifest.

**Per-day and incremental, not a whole-window refetch.** The predecessor fetched
one `forecast_window.nc` covering the whole window on *every* slow run. Once the
window spans the whole cruise that no longer fits the build's tight deadline —
every run would re-pull the entire, ever-growing span. The per-day store instead
pulls only what is missing or non-final: ~11 recent+forecast days per steady-state
run (~550 MB), well inside the cron budget, and the initial ~25-day backfill
spreads resumably across runs.

**Newest first.** Days are fetched in descending date order, so even a killed or
deadline-truncated run leaves the **recent + forecast** span — the part the app
actually reads — already covered, with the deep past filling in over later runs.

**The `final` rule (the rollover assumption).** A day is marked `final` — never
refetched short of `--refetch-all` — once two things hold: the fetch actually
returned the day's full 24 hourly steps (`_day_is_complete`; a short/partial
return stays non-final so a still-filling day is retried, not locked in), **and**
its whole span is `FINAL_MARGIN_H` (default 12 h) behind the fetch wall clock.
This encodes the working assumption that **CMEMS revises nothing behind the
current analysis edge**: a day old enough to be safely behind that edge will
never change, so re-pulling it is wasted work. Final day files are immutable, which
is also what lets the render step's absolute-named frames cache on the gateway
([currents.md](currents.md)).

**The escape hatch.** `--refetch-all` (equivalently: wipe the store) forces a
complete re-pull — the guard if the deferred rollover validation (a repo issue:
compare a stored `final` day against a fresh fetch) ever shows revisions behind
the edge, or against an unannounced CMEMS reprocessing.

**Config-change guard.** If the manifest's recorded `dataset_id`/`bbox` differ
from the current config while day files exist, `update_store` refuses (rather
than letting the read path silently NaN-pad a grid mismatch) unless
`--refetch-all` is set, which drops the stale entries and re-pulls under the new
config.

Each day's fetch is best-effort: a single failure is logged and skipped (the day
stays missing/non-final, retried next run), and because the manifest is rewritten
after every day that *does* complete, neither a caught per-day failure nor an
outright kill loses more than that one day's progress.

## The read side: two paths

Both read paths open the day files touching a requested `[t0, t1]` **plus one
hourly step outside each end** (integration needs the bracket; the legacy
single-fetch used `coordinates_selection_method="outside"` for the same reason),
verify hourly continuity across the span, and name the missing range if there is
a gap. They differ in how much they hold in memory.

### `load_window` — the batch path

`load_window` brackets, concatenates, deduplicates, and **loads the whole span
into one in-RAM `xr.Dataset`**, shaped exactly like the legacy whole-window
fetch's output (`uo`, `vo`; `time`/`latitude`/`longitude`) so its consumers are
drop-in. It is used where the span is inherently narrow:

- the slow build's **render** step (the ~few-day window behind the
  speed/vorticity/flow frames),
- the **inertial** decomposition's narrow ~24 h slice,
- the **parcels** cross-check API (`_api_parcels.py`, its own ±12 h window).

### `StoreField` — the streaming path

The deployment API's runs cover the **whole cruise span** ([deployment.md](deployment.md),
API v2), where loading every touched day whole would put the entire field back in
API RAM — exactly what the pod's 4 Gi limit forbids. `StoreField` is the
streaming alternative: a drop-in for `_forecast._Field` (same
`lons`/`lats`/`times`/`u`/`v`/`velocity()` contract, so the scalar and vectorized
RK4 run **unmodified** against it) whose `u`/`v` are not one big array but thin
views over a **bounded LRU of opened day arrays**, loaded on demand and evicted
behind the batch's monotone time cursor. A run spanning the whole store holds only
a handful of day files at once (~200 MB at the default cap), however long the run.

Because `_batch_advect` never resyncs seeds to a shared clock — each seed advances
by the same per-step `dt` from its own `start`, so the active seeds' absolute
times differ by exactly their original start spread — a batch whose drops start on
far-apart calendar days needs that many days resident *for the whole run*.
`day_cache_cap_for_starts` sizes the LRU cap to the actual seed-start spread, so the
cache never thrashes. That spread **is** bounded, at two levels (SEC-1): the API
rejects (422) a run whose in-window seed starts span more than
`_api._MAX_START_SPREAD_DAYS` calendar days, and `day_cache_cap_for_starts` clamps to
`_MAX_DAY_CACHE_CAP` as a hard backstop, so no single run can pin more than ~10 days
(~500 MB) resident — a wider spread degrades to bounded cache thrash, never an OOM.
A real deployment staggers water-entry over hours, so the guard only fires on a
pathological one-seed-per-store-day placement; a page-load batch forecasting every
deployed drifter at once still fits, since those last fixes cluster near the store's
recent edge rather than scattering one-per-day across the whole cruise.

### The API's field index

The forecast API turns the store into a servable `(lo, hi)` span via
`_build_field_index`: it reads every present day file's actual `time` coordinate
(cheap — the lazy backend never touches `uo`/`vo`) and returns the **maximal
hour-contiguous run containing now** (or the run closest to now when now itself
isn't yet covered — a fetch gap or an in-progress backfill). Reading true time
coordinates rather than assuming a full day per manifest entry means a
still-filling forecast-edge day narrows the servable span at its real edge
instead of failing a later `StoreField` build on an internal gap. The index is
rebuilt only when the manifest's **mtime** changes — one `stat` per request, the
same one-stat shape the single-file predecessor used — and its bounds are what
`GET /api/forecast/limits` advertises as the loaded `window`.
