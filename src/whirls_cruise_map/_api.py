"""PoC forecast API for the interactive deployment planner.

`pixi run serve-api` runs this: a FastAPI app (uvicorn, port 8001) exposing one
``POST /api/forecast`` endpoint. It is deliberately **separate from the static
map** (``pixi run serve`` — a plain http.server over ``site/``, the same bytes
GitLab Pages serves) so the two are independent endpoints: the static half can
fall back to Pages / an nginx pod with no backend, and only the forecast callback
needs this live service. The client resolves the API base at load
(``site/map/app.js``): same-origin by default (which is what the plan-017 gateway
gives, serving ``/map`` and ``/api`` under one host), auto-targeting :8001 in the
two-port dev flow. There is no client-controlled override — the deployed map only
ever talks to its own origin.

The endpoint is a **pure batch advector**: the client (in "Deploy" mode) clicks a
multi-segment path, resamples it into equally-spaced drifter drops, computes each
drop's staggered water-entry time from a ship-speed knob, and POSTs the resulting
sequence of ``(lon, lat, start)`` **seeds**. The API advects each seed through the
CMEMS hourly current window and returns one GeoJSON ``LineString`` per seed — the
*same* RK4 integrator the build uses for the drifter/glider forecast
(:mod:`whirls_cruise_map._forecast`), just seeded by the request. All the pattern
geometry (where the drops go, when each enters the water) lives in the client;
the field stays in server memory and only the answer ships (a few kB per seed,
gzipped on the wire by the in-app GZip middleware, far below shipping the field
to the browser — the route this PoC prototypes).

**Synced-t0 dots.** Every seed is integrated to a **common wall-clock end** (the
run start + ``horizon_h``, 48 h) and dotted at absolute run-relative times
(``run_start + k·mark_step_h``), not at k hours after its *own* entry. So one dot
colour is a single instant across the whole array — the array's shape at that t0,
the reference time a deformation / flow-map estimate is anchored to (Haller). A
drop that enters the water after mark k simply carries no dot at k, and later
drops carry shorter tracks (they all stop at the same end).

This is a laptop PoC, but its field handling is already the production shape:
the API reads the current window from a **shared cache the slow build cron
writes** (``plans/017-whirlsview-openshift.md``, the ``/analysis`` path), never
fetching CMEMS itself — so the pod needs **no credentials and no egress**.

Field lifecycle: the slow build cron persists one hourly window to
``site/map/data/_cache/forecast_window.nc`` (an unserved ``_cache/`` subtree; the
same window it already fetches for ``forecast.geojson``/``hindcast.geojson``, sized
forward to ``horizon + slow-cadence`` so a cadence-old cache still spans a full 48 h
run — see :data:`._currents.FORECAST_WINDOW_FWD_H`). The API loads that file into a
:class:`._forecast._Field` and **rebuilds it whenever the file's mtime changes**
(one ``stat`` per request; a rebuild only on a fresh cron write), so a long-lived
pod picks up each new window within one request, no restart. The path is overridable
via ``WHIRLS_FORECAST_WINDOW``. A missing or unreadable file → 503; the static map
still serves.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from . import _currents, _forecast

# --- config ------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PORT = 8001  # separate from the static map's :8000 (see module docstring)

# The slow build cron writes the hourly window here (an unserved _cache/ subtree
# under the map's data dir); the API reads it and never fetches CMEMS itself.
# WHIRLS_FORECAST_WINDOW points it at the shared PVC path in the deployment.
_DEFAULT_WINDOW_PATH = _REPO_ROOT / "site" / "map" / "data" / "_cache" / "forecast_window.nc"
_WINDOW_PATH = Path(os.environ.get("WHIRLS_FORECAST_WINDOW", str(_DEFAULT_WINDOW_PATH)))

# Batch-forecast cadence: integrate every seed +48 h from the run start, dotting
# each at absolute run-relative 3 h steps (see the module docstring's synced-t0
# note). These are the request defaults; the client passes its own knobs. The
# horizon is shared with the cron's window sizing (a single source, so a bump here
# can't outrun the persisted window — see :data:`._currents.FORECAST_WINDOW_FWD_H`).
_DEFAULT_HORIZON_H = float(_currents.FORECAST_HORIZON_H)
_DEFAULT_MARK_STEP_H = 3.0

# Per-request seed cap: how many drops the batch endpoint advects in one POST. The
# vectorized advection runs the whole cap in ~1–2 s, so this is *not* a latency bound
# (the full cap is far inside the gateway's 60 s timeout) — it caps the unauthenticated
# worst case: the CPU of one big POST and the transient trajectory array the batch
# materialises (~18 MB at 2000 seeds × 48 h, ~92 MB at the 240 h horizon cap; it scales
# with cap × horizon). This constant
# is the **single source of truth** for the cap: the request model enforces it and the
# ``GET /api/forecast/limits`` endpoint advertises it, so the deploy-tool client fetches
# the number to pre-validate against rather than hardcoding its own copy.
_MAX_SEEDS = 2000

# The parcels validation oracle (:mod:`._api_parcels`) imports these for its own
# single-point +12 h forecast — its cadence, kept here so the two engines compare
# on the same horizon/marks. Not used by the batch endpoint below.
_HORIZON_H = 12.0
_MARK_HOURS = (3, 6, 9, 12)

# --- field (load from the PVC cache, reload on mtime change) ------------------

_field_lock = threading.Lock()
_sampler: _forecast._Field | None = None
_sampler_mtime: float | None = None


def _load_window() -> xr.Dataset:
    """The hourly current window the slow cron persisted to the PVC. Raises
    :class:`FileNotFoundError` if the cron has not written it yet (→ 503 upstream).
    The API never fetches CMEMS — the cron owns the credentials and egress."""
    with xr.open_dataset(_WINDOW_PATH) as ds:
        return ds.load()


def _get_sampler() -> _forecast._Field:
    """The in-memory field sampler, rebuilt from the persisted window whenever its
    mtime changes (thread-safe). One ``stat`` per request; a rebuild only on a fresh
    cron write — so a pod picks up a new window within one request, no restart. A
    missing/unreadable file raises (→ 503), the current field-unavailable contract."""
    global _sampler, _sampler_mtime
    with _field_lock:
        mtime = _WINDOW_PATH.stat().st_mtime  # FileNotFoundError -> 503 upstream
        if _sampler is None or mtime != _sampler_mtime:
            _sampler = _forecast._Field(_load_window())
            _sampler_mtime = mtime
        return _sampler


def _iso(epoch_s: float) -> str:
    """Epoch seconds → ISO-8601 UTC (``Z``)."""
    return np.datetime_as_string(np.datetime64(int(round(epoch_s)), "s"), unit="s") + "Z"


def _parse_start(start: str) -> float:
    """Parse an ISO-8601 start time (``Z`` or offset) to epoch seconds — the clock
    convention :attr:`._forecast._Field.times` uses (naive-UTC seconds since 1970).
    Raises :class:`ValueError` on an unparseable string."""
    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return float(np.datetime64(dt, "s").astype(np.float64))


def _seed_marks(
    offset_h: float, horizon_h: float, step: float = _DEFAULT_MARK_STEP_H
) -> tuple[float, ...]:
    """One seed's dot schedule as **elapsed-from-entry** hours to hand the integrator:
    every absolute run-relative mark (``step, 2*step, …`` up to ``horizon_h``) that
    falls after this seed's ``offset_h`` water-entry, minus ``offset_h``. So mark *k*
    is the same wall-clock instant for every seed; the caller shifts each returned mark
    back to its absolute run-relative hour by adding ``offset_h`` (relabel by *value*,
    not by list position). A mark within ~half a sub-step of entry is simply not emitted
    by the integrator (it rounds to step 0) — leaving no dot there rather than shifting
    the colour/label of every later dot, which a position-based relabel would do."""
    return tuple(
        k * step - offset_h
        for k in range(1, int(horizon_h // step) + 1)
        if k * step > offset_h
    )


# --- request models ----------------------------------------------------------


class Seed(BaseModel):
    """One drifter drop the client asks a forecast for: a position and its
    **absolute** water-entry time (the client bakes the ship-speed stagger into
    ``start``, so the API needs no ship speed)."""

    lon: float
    lat: float
    start: str  # ISO-8601 water-entry time


class ForecastRequest(BaseModel):
    """A whole deployment's worth of seeds plus the two run-level cadence knobs.
    The run start is the earliest seed's ``start`` (drop #1's entry); every seed is
    integrated to ``run_start + horizon_h`` and dotted at ``mark_step_h`` steps.

    The endpoint is public and unauthenticated, so every field is bounded to keep a
    single ~100-byte request from exhausting the pod. ``horizon_h``/``mark_step_h``
    cap the per-seed dot schedule ``_seed_marks`` eagerly materialises (``horizon_h //
    mark_step_h`` <= ~960 marks; unbounded, a large ``horizon_h`` + tiny ``mark_step_h``
    allocates a multi-GB tuple → OOM). ``seeds`` caps the vectorized RK4 advection —
    its CPU and the transient trajectory array it materialises (~cap × horizon).
    ``allow_inf_nan`` rejects ``inf``/``nan``, and ``extra="forbid"`` rejects unknown
    fields (422, not silently ignored)."""

    model_config = {"extra": "forbid"}
    seeds: list[Seed] = Field(max_length=_MAX_SEEDS)
    horizon_h: float = Field(default=_DEFAULT_HORIZON_H, gt=0, le=240, allow_inf_nan=False)
    mark_step_h: float = Field(
        default=_DEFAULT_MARK_STEP_H, ge=0.25, le=48, allow_inf_nan=False
    )


# --- forecast ----------------------------------------------------------------


def _batch_forecast(seeds: list[Seed], horizon_h: float, mark_step_h: float) -> dict:
    """Advect every seed and return a ``FeatureCollection`` of the per-seed forecast
    ``LineString``s (drops + ship track stay client-side). The run start is the
    earliest seed time; each seed is integrated to the **common** wall-clock end
    (run start + ``horizon_h``) and dotted at absolute run-relative marks, so one dot
    colour is the whole array at one t0 (see the module docstring). A seed whose
    ``start`` is out of the field window, or at/after the common end (no track left),
    is skipped and counted — the plan still stands even when the field doesn't cover
    it. Raises :class:`ValueError` (→ 422) on no seeds or an unparseable ``start``.

    The whole batch advects at once through :func:`_forecast._batch_advect` (vectorized
    RK4 over all seeds in lockstep) — bit-identical to advecting each seed with the
    scalar integrator, but ~40× faster at the seed cap. This function then reads the
    coords and synced-t0 marks off the returned trajectory array."""
    if not seeds:
        raise ValueError("no seeds")
    sampler = _get_sampler()
    lo, hi = float(sampler.times[0]), float(sampler.times[-1])
    starts = np.array(
        [_parse_start(s.start) for s in seeds], dtype=np.float64  # ValueError -> 422
    )
    run_start = float(starts.min())
    offset_h = (starts - run_start) / 3600.0
    horizon_i = horizon_h - offset_h

    # A seed out of the field window, or at/after the common end (no track left), is
    # not advected (n_steps 0) and skipped in the emit loop below.
    alive0 = (starts >= lo) & (starts <= hi) & (horizon_i > 0)
    n_steps = np.where(
        alive0, np.round(horizon_i * 60.0 / _forecast.STEP_MIN).astype(int), 0
    )
    seed_lon = np.array([s.lon for s in seeds], dtype=np.float64)
    seed_lat = np.array([s.lat for s in seeds], dtype=np.float64)
    positions, completed = _forecast._batch_advect(
        sampler, seed_lon, seed_lat, starts, n_steps
    )

    vertex_every = round(_forecast.VERTEX_MIN / _forecast.STEP_MIN)
    nd = _forecast._COORD_NDIGITS
    features: list[dict] = []
    n_forecasts = 0
    n_skipped = 0
    for i in range(len(seeds)):
        if not alive0[i]:
            n_skipped += 1
            continue
        cs = int(completed[i])
        coords = [
            [round(float(positions[i, s, 0]), nd), round(float(positions[i, s, 1]), nd)]
            for s in range(0, cs + 1, vertex_every)
        ]
        if len(coords) < 2:
            n_skipped += 1  # head on land / off the field (truncated at step 0)
            continue
        off = offset_h[i]
        # Marks at the seed's synced-t0 schedule (elapsed-from-entry hours), each shifted
        # back to its absolute run-relative hour (``off + elapsed``) so every drop's dots
        # share one wall-clock grid — the client colours each synced dot by this. Relabel
        # by *value*: a mark step past the truncation (``> cs``) or at entry (``ms 0``)
        # simply leaves no dot, without shifting the colour/label of the rest. Key by
        # integrator step, mirroring the scalar ``_integrate``'s ``mark_at`` dict, so two
        # marks that round to the same step collapse to one (they don't at the request
        # model's ``mark_step_h >= 0.25``, which is >= 3 steps apart — but this keeps the
        # two paths identical regardless of that bound rather than silently relying on it).
        step_to_hour = {
            round(h * 60.0 / _forecast.STEP_MIN): h
            for h in _seed_marks(off, horizon_h, mark_step_h)
        }
        marks = [
            {
                "hours": round(step_to_hour[ms] + off, 3),
                "lon": round(float(positions[i, ms, 0]), nd),
                "lat": round(float(positions[i, ms, 1]), nd),
            }
            for ms in sorted(step_to_hour)
            if 1 <= ms <= cs
        ]
        valid = _forecast._anchor_t0(sampler, float(starts[i]))[1]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"role": "forecast", "index": i, "valid_time": valid, "marks": marks},
        })
        n_forecasts += 1

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "run_start": _iso(run_start),
            "horizon_h": horizon_h,
            "mark_step_h": mark_step_h,
            "n_seeds": len(seeds),
            "forecasts": n_forecasts,
            "skipped": n_skipped,
            "window": [_iso(lo), _iso(hi)],  # field span; seeds outside it skip
        },
    }


# --- app ---------------------------------------------------------------------

app = FastAPI(title="WHIRLS interactive forecast (PoC)")
# gzip every sizeable response in-app (Starlette's middleware) rather than at a
# gateway, so every deployment shape — the two-port dev flow, the plan-017 gateway,
# any future proxy — compresses, and the dev flow measures what production ships.
# The JSON is highly repetitive (a 1000-seed forecast is ~4.9 MB raw, ~1/3 gzipped);
# `minimum_size` keeps tiny responses like `/api/forecast/limits` and error bodies
# uncompressed, where the codec overhead beats the savings.
app.add_middleware(GZipMiddleware, minimum_size=1024)
# The only real deployment is same-origin (the plan-017 gateway serves /map and /api
# under one host), so it exercises no CORS at all. The sole cross-origin caller is the
# two-port dev flow — the static map on :8000 fetching this API on :8001 (see
# ``resolveApi`` in app.js) — so scope CORS to those localhost dev origins and to the
# two methods the client uses (the forecast POST + its Content-Type, and the GET the
# limits probe sends), not the wildcard a public endpoint shouldn't advertise.
_DEV_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.post("/api/forecast")
def forecast(req: ForecastRequest) -> dict:
    """Batch current-advection forecast: a sequence of ``(lon, lat, start)`` seeds
    in, one ``+horizon_h`` GeoJSON ``LineString`` per in-window seed out (synced-t0
    dots; see :func:`_batch_forecast`). A sync endpoint, so FastAPI runs it in the
    threadpool — reloading the window on a fresh cron write can block one request
    without stalling the static map. The whole batch advects in ~1–2 s even at the
    seed cap (vectorized RK4), comfortably inside the gateway's 60 s timeout."""
    try:
        return _batch_forecast(req.seeds, req.horizon_h, req.mark_step_h)
    except ValueError as exc:  # no seeds / unparseable start time
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # window missing/unreadable — the static map still serves
        raise HTTPException(status_code=503, detail=f"forecast field unavailable: {exc}")


@app.get("/api/forecast/limits")
def limits() -> dict:
    """The request bounds the deploy-tool client pre-validates against, so it can
    reject an over-cap deployment with an explicit message *before* POSTing — without
    hardcoding its own copy of the cap. Currently the seed cap (:data:`_MAX_SEEDS`),
    the one value that gates a whole-array placement; the horizon/mark-step bounds
    stay the request model's ``Field`` constraints. A plain GET, reached under the
    same CORS as the forecast POST (see the middleware note)."""
    return {"max_seeds": _MAX_SEEDS}


def main() -> None:
    import uvicorn

    print(f"forecast API on http://localhost:{_PORT}/api/forecast")
    print("serve the map separately: `pixi run serve` (static, :8000)")
    print(f"reads the window from {_WINDOW_PATH} (write it with a `derive` --tier slow run)")
    uvicorn.run(app, host="0.0.0.0", port=_PORT)


if __name__ == "__main__":
    main()
