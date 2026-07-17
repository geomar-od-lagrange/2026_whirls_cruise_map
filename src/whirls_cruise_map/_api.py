"""Deployment forecast API (v2): virtual-drifter runs seeded and directed by the
client, advected through the incremental per-day CMEMS current store.

`pixi run serve-api` runs this: a FastAPI app (uvicorn, port 8001) exposing
``POST /api/forecast`` and ``GET /api/forecast/limits``. It is deliberately
**separate from the static map** (``pixi run serve`` — a plain http.server over
``site/``, the same bytes GitLab Pages serves) so the two are independent
endpoints: the static half can fall back to Pages / an nginx pod with no
backend, and only the forecast callback needs this live service. The client
resolves the API base at load (``site/map/app.js``): same-origin by default
(the gateway serves ``/map`` and ``/api`` under one host), auto-targeting :8001
in the two-port dev flow. There is no client-controlled override — the
deployed map only ever talks to its own origin.

The endpoint is a **pure batch advector**: the client (in "Deploy" mode) clicks
a multi-segment path, resamples it into equally-spaced drifter drops, computes
each drop's staggered water-entry time from a ship-speed knob, and POSTs the
resulting sequence of ``(lon, lat, start)`` **seeds** plus a run-level
``direction`` and ``horizon_h``. The API advects every seed through the CMEMS
hourly field store — the *same* RK4 integrator the build uses for the
drifter/glider forecast (:mod:`whirls_cruise_map._forecast`), just seeded and
directed by the request — and returns one GeoJSON ``LineString`` per seed. All
the pattern geometry (where the drops go, when each enters the water) lives in
the client; the field stays in server memory and only the answer ships.

**Run semantics.** A run's anchor is the *earliest* seed start for a forward
run, the *latest* for a backward one; every seed integrates to the common
wall-clock end ``anchor + direction * horizon_h`` (a drop that enters later —
earlier for backward — carries a shorter track, since they all stop at the
same end). A seed whose start falls outside the field's currently loaded span,
or that has no track left before the common end, is skipped and counted, never
errored. Each returned track carries ``{start, cadence_s}`` (vertex ``i`` sits
at ``start + direction * i * cadence_s``) rather than the old fixed-hour
"marks" — the substrate for the at-time markers the deployment-focused client
draws (``plans/done/034-deployment-focused-app.md``, workstream B/D); the
per-instrument build forecast/hindcast keep their own ``marks`` machinery,
untouched.

**Field.** The API never fetches CMEMS itself — it reads the incremental
per-day store a slow build run writes (:mod:`whirls_cruise_map._field_store`),
resolved the same way the store module resolves it (``WHIRLS_FIELD_CACHE`` env,
else a repo-local ``cache/field/``). A small in-process index tracks the
store's currently available contiguous span (rebuilt only when the manifest's
mtime changes — one ``stat`` per request, same shape as the old single-file
mtime dance); each request then opens a fresh
:class:`whirls_cruise_map._field_store.StoreField` over just the span its run
needs, streaming day files through a bounded day cache so a run holds only a
handful of days resident however long it runs. What it does *not* bound by
itself is the seed-start *spread*: since :func:`_forecast._batch_advect` never
resyncs seeds to a shared wall clock, seeds started on far-apart calendar days
keep that many days resident at once. Two run-time guards cap that (SEC-1):
:data:`_MAX_START_SPREAD_DAYS` rejects a run whose in-window starts span too many
days, and :data:`_MAX_CONCURRENCY` gates how many runs hold a field at once, so
peak memory stays inside the API pod's 4 Gi limit. A missing or empty store →
503; the static map still serves.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, model_validator

from . import _field_store, _forecast, _time

# --- config ------------------------------------------------------------------

_PORT = 8001  # separate from the static map's :8000 (see module docstring)

# Per-request seed cap: how many drops the batch endpoint advects in one POST. The
# vectorized advection runs the whole cap in ~1-2 s, so this is *not* a latency bound
# on its own — combined with the seeds x hours budget below, it caps the worst-case
# compute + serialization the unauthenticated endpoint will do for one POST. This
# constant is the **single source of truth** for the cap: the request model enforces
# it and ``GET /api/forecast/limits`` advertises it, so the deploy-tool client
# fetches the number to pre-validate against rather than hardcoding its own copy.
_MAX_SEEDS = 2000

# Combined seeds x hours budget: worst-case compute scales roughly linearly in both,
# so the cap alone (2000 seeds) doesn't bound a long-horizon request. 2000 seeds x
# 48 h ~= 1.2 s measured; the budget is sized so the worst-case request (any
# seeds/horizon_h combination under the budget) stays comfortably inside the edge
# router's hard ~60 s timeout (~12 s worst case, generous margin for serialization
# and gzip). A request over budget gets a 422 naming the excess, not a slow 200 or a
# gateway-killed request.
_MAX_SEED_HOURS = 1_000_000

# SEC-1 memory bound. The seeds x hours budget above caps *compute*, but not resident
# *memory*: `_batch_advect` never resyncs seeds to a shared wall clock, so at every
# step the still-active seeds' absolute times differ by exactly their original start
# spread, and the streaming field must keep that many distinct calendar days resident
# at once (~50 MB/day). A cheap request (small budget) that places seeds one-per-store
# -day therefore pins the store's whole span in RAM and, run a few times concurrently,
# OOM-kills the pod. Two constants below are the whole memory contract, tied together
# by one budget. A run's field residency is capped not at the spread but at the LRU
# cap `_field_store.day_cache_cap_for_starts` derives from it — spread + 2 bracketing
# days, ceilinged at `_MAX_DAY_CACHE_CAP` (= `_MAX_START_SPREAD_DAYS` + 2 = 10). The
# semaphore multiplies BOTH that field residency and the transient trajectory buffer
# `_batch_advect` allocates (~180 MB at budget — the audit's FC-1, shrinks once that
# lands) by `_MAX_CONCURRENCY`:
#
#     (_MAX_START_SPREAD_DAYS + 2) x ~50 MB/day x _MAX_CONCURRENCY   field days
#   + ~180 MB trajectory buffer                 x _MAX_CONCURRENCY   (FC-1)
#   + ~0.7 Gi base process
#   = (8+2) x 50 MB x 3  +  180 MB x 3  +  0.7 Gi  ~=  1.5 + 0.5 + 0.7  ~=  2.7 Gi < 4 Gi
#
#   * `_MAX_START_SPREAD_DAYS` — reject (422) a run whose in-window seed starts span
#     more than this many calendar days, so one run's field residency is bounded
#     (~10 days x 50 MB ~= 500 MB, the +2 bracket included) regardless of how cheap
#     its compute is. `_field_store._MAX_DAY_CACHE_CAP` is the matching hard backstop
#     inside the cache itself, so even a request that somehow slips the guard can
#     never pin more than the ceiling.
#   * `_MAX_CONCURRENCY` — a semaphore around the memory-heavy advection so at most
#     this many runs hold a field resident at once.
#
# WHY 8, AND HOW TO RAISE IT. A real *observation* forecast staggers water-entry over
# hours, and an active cruise's drifters all report within a reporting cycle, so 8 days
# never binds in normal use. It is deliberately the ONE knob to turn if the app is
# repurposed for wider-spread *planning* runs — e.g. "here is a 20-day cruise track;
# lay 200 drifters equidistant in time and forecast each," which needs a ~20-day start
# spread. To support that: raise `_MAX_START_SPREAD_DAYS` to cover the widest planned
# spread AND drop `_MAX_CONCURRENCY` so the budget above still clears the pod limit
# (e.g. 20-day spread -> (20+2) x 50 MB x 2 + 180 MB x 2 + 0.7 Gi ~= 2.2 + 0.4 + 0.7
# ~= 3.3 Gi < 4 Gi), then bump the pod's memory limit if you need both wide and
# concurrent. Do not raise the spread alone.
# The deeper fix that removes the trade-off entirely — resync seeds to a shared wall
# clock in `_batch_advect` so residency tracks the *horizon window* not the start
# spread — is out of scope here (see the review's SEC-1 / FC-1 notes).
_MAX_START_SPREAD_DAYS = 8
_MAX_CONCURRENCY = 3
_run_semaphore = threading.BoundedSemaphore(_MAX_CONCURRENCY)

# In-app request-body ceiling (SEC-4). `seeds: Field(max_length=_MAX_SEEDS)` rejects an
# oversized array only *after* Starlette has buffered and JSON-parsed the whole body,
# so a multi-hundred-MB payload materialises GBs of transient objects before the 422.
# The largest legitimate body is ~_MAX_SEEDS seeds x ~90 bytes each ~= 180 KB; 512 KB
# leaves generous headroom while 413-ing anything that could only be an attack, before
# a byte is parsed. This is an in-app guard so the dev flow and any deployment shape are
# covered without relying on the out-of-repo nginx `client_max_body_size` default.
_MAX_BODY_BYTES = 512 * 1024

# Fixed 503 detail for a missing/empty/not-yet-built store (SEC-2/SEC-7). Never
# interpolate the exception: `FileNotFoundError` names the absolute store dir
# (WHIRLS_FIELD_CACHE / PVC path), which must not reach a public body.
_FIELD_UNAVAILABLE_DETAIL = "forecast field unavailable"

_DEFAULT_HORIZON_H = 48.0

# --- field store: resolve, index, and reload on manifest change --------------

_field_lock = threading.Lock()
_index: tuple[datetime, datetime] | None = None
_index_mtime: float | None = None


def _resolve_store_dir() -> Path:
    """The per-day field store's directory — the same ``WHIRLS_FIELD_CACHE``-env
    -> repo-local-default resolution :mod:`_field_store` uses internally, read
    fresh on every call (not cached) so a test can point it at a ``tmp_path``
    just by setting the env var, no module-reload dance."""
    return _field_store._resolve_store_dir(None)


def _dt64_to_utc(t: np.datetime64) -> datetime:
    return datetime.fromisoformat(str(t.astype("datetime64[s]"))).replace(tzinfo=timezone.utc)


def _build_field_index(
    store: Path, manifest: dict, *, now: datetime | None = None
) -> tuple[datetime, datetime] | None:
    """The store's currently servable ``(lo, hi)`` span: the maximal run of
    truly hour-contiguous time steps (no gap wider than an hour — the same bar
    :func:`_field_store._check_hourly_continuity` holds a run's ``StoreField``
    to) pooled across every on-disk day file, containing ``now`` (default the
    real wall clock; injectable so a test doesn't have to depend on it) — or,
    when ``now`` isn't itself covered (a fetch gap, or a backfill still in
    progress), the run closest to it. Reads every present day file's actual
    ``time`` coordinate (cheap — the lazy netCDF backend never touches
    ``uo``/``vo`` for this), so a present-but-partial day (fewer than its 24
    hours, e.g. the store's still-filling forecast edge) narrows the run at its
    true edge rather than a day-presence check assuming a full day and letting
    a later ``StoreField`` build fail on an internal gap instead. ``None`` if
    the store holds no day files at all (an empty/not-yet-built store, or a
    manifest naming files that no longer exist on disk)."""
    days_meta = manifest.get("days", {})
    paths = [store / entry["file"] for entry in days_meta.values() if (store / entry["file"]).exists()]
    if not paths:
        return None

    all_times = []
    for path in paths:
        with xr.open_dataset(path) as ds:
            all_times.append(np.asarray(ds["time"].values))
    times = np.unique(np.concatenate(all_times))  # sorted, deduplicated

    gaps = np.flatnonzero(np.diff(times) != np.timedelta64(1, "h"))
    bounds = [0, *(gaps + 1), times.size]
    runs = [(times[bounds[i]], times[bounds[i + 1] - 1]) for i in range(len(bounds) - 1)]

    now = now if now is not None else datetime.now(timezone.utc)
    now64 = np.datetime64(now.astimezone(timezone.utc).replace(tzinfo=None), "s")

    def _distance(run: tuple[np.datetime64, np.datetime64]):
        lo, hi = run
        if lo <= now64 <= hi:
            return np.timedelta64(-1, "s")
        return min(abs(lo - now64), abs(hi - now64))

    lo_t, hi_t = min(runs, key=_distance)
    return _dt64_to_utc(lo_t), _dt64_to_utc(hi_t)


def _get_field_index() -> tuple[datetime, datetime]:
    """The store's currently available ``(lo, hi)`` span, rebuilt only when the
    manifest's mtime changes (one ``stat`` per request, thread-safe) — the same
    one-stat-per-request shape the v1 single-file mtime dance used, now against
    the per-day store's manifest. Raises :class:`FileNotFoundError` when the
    store has no manifest at all; :func:`_build_field_index` returning ``None``
    (a manifest with no day files on disk) raises the same, so both map to the
    503 the endpoints below give for "field unavailable"."""
    global _index, _index_mtime
    with _field_lock:
        store = _resolve_store_dir()
        manifest_path = _field_store._manifest_path(store)
        mtime = manifest_path.stat().st_mtime  # FileNotFoundError -> 503 upstream
        if _index is None or mtime != _index_mtime:
            manifest = _field_store._load_manifest(store)
            idx = _build_field_index(store, manifest)
            if idx is None:
                raise FileNotFoundError(f"field store at {store} has no day files on disk")
            _index = idx
            _index_mtime = mtime
        return _index


# The epoch/ISO/parse helpers this endpoint's bookkeeping uses (``to_epoch``,
# ``from_epoch``, ``iso_z_from_epoch``, ``parse_iso_to_epoch``, ``now_iso``) live in the
# shared :mod:`._time` module — one home for the whole codebase's UTC clock convention
# (audit IDIOM-2 / API-3), so the parcels oracle no longer imports them from here either
# (API-4). ``_dt64_to_utc`` above stays: it is a numpy-datetime64 -> datetime helper
# specific to the field-index build, not part of that shared surface.


# --- request models ----------------------------------------------------------


class Seed(BaseModel):
    """One drifter drop the client asks a run for: a position and its
    **absolute** water-entry time (the client bakes the ship-speed stagger into
    ``start``, so the API needs no ship speed).

    ``lon``/``lat`` are bounded to the valid coordinate ranges and reject
    ``inf``/``nan`` (SEC-5): the endpoint is unauthenticated, and a non-finite or
    absurd coordinate is never a real deployment — it samples off-field, freezes
    the seed at step 0, and is silently skipped, so bounding it up front turns a
    latent unenforced invariant into an explicit 422."""

    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    start: str  # ISO-8601 water-entry time


class ForecastRequest(BaseModel):
    """A whole deployment's worth of seeds plus the run-level direction and
    horizon. The run anchor is the earliest seed's ``start`` for a forward run,
    the latest for a backward one; every seed integrates to
    ``anchor + direction * horizon_h``.

    The endpoint is public and unauthenticated, so every field is bounded: each
    :class:`Seed`'s ``lon``/``lat`` is range-checked and ``inf``/``nan``-rejected;
    ``seeds`` caps the vectorized RK4 advection's per-request CPU and the transient
    trajectory array it materialises; ``horizon_h`` is bounded to the widest a run
    could ever need (100 days) and rejects ``inf``/``nan``; and the combined
    ``len(seeds) * horizon_h`` budget (:data:`_MAX_SEED_HOURS`) bounds worst-case
    compute + serialization to stay well inside the edge router's ~60 s timeout even
    when neither knob alone would. ``extra="forbid"`` rejects unknown fields (422, not
    silently ignored). Resident *memory* is bounded separately, at run
    time: a run whose in-window seed starts span more than :data:`_MAX_START_SPREAD_DAYS`
    calendar days is rejected (422) so no cheap request can pin the whole field store in
    RAM (SEC-1)."""

    model_config = {"extra": "forbid"}
    seeds: list[Seed] = Field(max_length=_MAX_SEEDS)
    direction: Literal["forward", "backward"] = "forward"
    horizon_h: float = Field(default=_DEFAULT_HORIZON_H, gt=0, le=2400, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_seed_hours_budget(self) -> "ForecastRequest":
        budget = len(self.seeds) * self.horizon_h
        if budget > _MAX_SEED_HOURS:
            raise ValueError(
                f"seeds x horizon_h budget exceeded: {len(self.seeds)} seeds x "
                f"{self.horizon_h} h = {budget:.0f} seed-hours > {_MAX_SEED_HOURS} max"
            )
        return self


# --- run -----------------------------------------------------------------


def _batch_run(seeds: list[Seed], horizon_h: float, direction: Literal["forward", "backward"]) -> dict:
    """Advect every seed and return a ``FeatureCollection`` of the per-seed
    ``track`` ``LineString``s (drops + ship track stay client-side). The run
    anchor is the earliest seed's start for a forward run, the latest for a
    backward one; every seed integrates to the common wall-clock end ``anchor +
    direction * horizon_h``, so later (forward) / earlier (backward) drops carry
    shorter tracks — they all stop at the same end. A seed whose ``start`` falls
    outside the field's currently loaded span, or that has no track left before
    the common end, is skipped and counted. Raises :class:`ValueError` (-> 422)
    on no seeds or an unparseable ``start``; propagates whatever
    :func:`_get_field_index` raises on an empty/missing store (-> 503
    upstream).

    The whole batch advects at once through :func:`_forecast._batch_advect`
    (vectorized RK4 over all seeds in lockstep, signed by ``direction``) —
    bit-identical to advecting each seed with the scalar integrator, but ~40x
    faster at the seed cap."""
    if not seeds:
        raise ValueError("no seeds")
    sign = 1 if direction == "forward" else -1

    field_lo, field_hi = _get_field_index()
    field_lo_e, field_hi_e = _time.to_epoch(field_lo), _time.to_epoch(field_hi)
    now_iso = _time.now_iso()

    starts = np.array(
        [_time.parse_iso_to_epoch(s.start) for s in seeds], dtype=np.float64  # ValueError -> 422
    )
    anchor = float(starts.max()) if sign < 0 else float(starts.min())
    common_end = anchor + sign * horizon_h * 3600.0
    needed_lo, needed_hi = (common_end, anchor) if sign < 0 else (anchor, common_end)

    cadence_min = _forecast._vertex_cadence_min(horizon_h)
    cadence_s = cadence_min * 60.0
    vertex_every = round(cadence_min / _forecast.STEP_MIN)

    clipped_lo = max(needed_lo, field_lo_e)
    clipped_hi = min(needed_hi, field_hi_e)

    base_properties = {
        "run_start": _time.iso_z_from_epoch(anchor),
        "direction": direction,
        "horizon_h": horizon_h,
        "cadence_s": cadence_s,
        "n_seeds": len(seeds),
        "analysis_edge": now_iso,
    }

    if clipped_lo > clipped_hi:
        # No overlap at all between the run's needed span and what the store
        # currently has loaded: every seed is skipped, and there is no run-local
        # window to report, so fall back to the store's whole available span.
        return {
            "type": "FeatureCollection",
            "features": [],
            "properties": {
                **base_properties,
                "tracks": 0,
                "skipped": len(seeds),
                "window": [_time.iso_z_from_epoch(field_lo_e), _time.iso_z_from_epoch(field_hi_e)],
            },
        }

    offset_h = sign * (starts - anchor) / 3600.0  # elapsed hours from anchor, run-direction-signed
    horizon_i = horizon_h - offset_h  # this seed's own remaining run length
    alive0 = (starts >= clipped_lo) & (starts <= clipped_hi) & (horizon_i > 0)

    # Size the streaming field's day cache to the advected seeds' actual start spread
    # (out-of-window stragglers are skipped-and-counted, never sampled, so they don't
    # grow it). _batch_advect never resyncs seeds to a shared wall clock, so far-apart
    # starts keep that many distinct calendar days resident for the whole run
    # (~50 MB/day) — the cap tracks exactly that. Bound it (SEC-1): reject a run whose
    # in-window starts span more than _MAX_START_SPREAD_DAYS, so one run can never pin
    # more than that many days resident however cheap its compute; the semaphore below
    # then bounds how many such runs hold a field at once. A real deployment staggers
    # water-entry over hours, so this only ever fires on the pathological one-per-day
    # placement the guard exists to stop.
    alive_starts = starts[alive0]
    if alive_starts.size:
        spread_days = math.ceil(
            (float(alive_starts.max()) - float(alive_starts.min())) / 86400.0
        )
        if spread_days > _MAX_START_SPREAD_DAYS:
            raise ValueError(
                f"seed-start spread too wide: in-window seeds span {spread_days} "
                f"calendar days > {_MAX_START_SPREAD_DAYS} max (would pin that many "
                f"days of field resident at once); stagger water-entry over a narrower "
                f"window or split into separate deployments"
            )
        day_cache_cap = _field_store.day_cache_cap_for_starts(
            float(alive_starts.min()), float(alive_starts.max())
        )
    else:
        day_cache_cap = _field_store._DEFAULT_DAY_CACHE_CAP

    seed_lon = np.array([s.lon for s in seeds], dtype=np.float64)
    seed_lat = np.array([s.lat for s in seeds], dtype=np.float64)
    n_steps = np.where(
        alive0, np.round(horizon_i * 60.0 / _forecast.STEP_MIN).astype(int), 0
    )

    # Everything above is cheap validation/setup; only from here does a field get
    # opened and the trajectory buffer materialised, so the concurrency gate wraps
    # exactly the memory-heavy span (SEC-1). Cached responses never reach here — the
    # lru_cache in `_cached_batch_run` short-circuits before `_batch_run` is called —
    # so a page-load flood of the identical observed-forecast request doesn't queue on
    # the semaphore.
    with _run_semaphore:
        field = _field_store.StoreField(
            None, _time.from_epoch(clipped_lo), _time.from_epoch(clipped_hi), day_cache_cap=day_cache_cap
        )
        positions, completed = _forecast._batch_advect(
            field, seed_lon, seed_lat, starts, n_steps, direction=sign
        )

        nd = _forecast._COORD_NDIGITS
        features: list[dict] = []
        n_tracks = 0
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
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "role": "track",
                    "index": i,
                    "start": _time.iso_z_from_epoch(starts[i]),
                    "cadence_s": cadence_s,
                    "direction": direction,
                },
            })
            n_tracks += 1

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            **base_properties,
            "tracks": n_tracks,
            "skipped": n_skipped,
            "window": [_time.iso_z_from_epoch(clipped_lo), _time.iso_z_from_epoch(clipped_hi)],
        },
    }


# --- response cache ----------------------------------------------------------
#
# The map fires the observed-drifter forecast at page load for every client, and it is a
# byte-identical request at a given data version (seeds off latest.geojson, fixed
# horizon/direction). On the single API pod, recomputing it per client is wasted work, so
# memoize the run. The field-manifest mtime `_get_field_index` already stats each request
# (bumped on every slow build write) is the version key, so a store update invalidates the
# cache; a changed seed set / horizon / direction is a different key. Custom deploy-tool runs
# have unique bodies, so they miss and are served live. `analysis_edge` (the run's only
# wall-clock field) is not read by the client, so a frozen copy in a cached response is
# harmless. lru_cache is thread-safe and does not cache exceptions, so a 422/503 recomputes.
_FORECAST_CACHE_CAP = 32


def _field_version() -> float:
    """The field store's version token: the manifest mtime `_get_field_index` already stats
    each request (bumped on every slow build write). Raises `FileNotFoundError` on an
    empty/missing store, exactly as `_get_field_index` does (-> 503 upstream)."""
    _get_field_index()  # refreshes `_index_mtime` if the manifest changed; may raise
    assert _index_mtime is not None  # set by the call above on success
    return _index_mtime


@lru_cache(maxsize=_FORECAST_CACHE_CAP)
def _cached_batch_run(
    field_version: float, direction: str, horizon_h: float, seeds: tuple
) -> dict:
    """:func:`_batch_run` memoized on the field version + request. ``seeds`` is a hashable
    tuple of ``(lon, lat, start)`` triples (so the arguments are hashable); ``field_version``
    scopes every entry to the store state it was computed against."""
    return _batch_run(
        [Seed(lon=lon, lat=lat, start=start) for lon, lat, start in seeds],
        horizon_h,
        direction,
    )


# --- request-body size guard (SEC-4) -----------------------------------------


class _BodySizeLimitMiddleware:
    """Pure-ASGI middleware that 413s a request body over ``max_bytes`` *before* the app
    parses it (SEC-4). Rejects on ``Content-Length`` up front when the header is present
    (the common case — browsers/httpx/curl always send it for a sized body); otherwise it
    buffers the body itself, capped at the limit, so a chunked body with no declared
    length gets the same clean 413 and still never materialises past the cap. Buffering
    then replaying is affordable precisely because the cap is tiny (a full seed-cap
    request is ~180 KB), and it keeps the rejection uniform — a raise mid-read would be
    caught by FastAPI's body-parse guard and surface as a generic 400 instead."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await self._send_413(send)
                except ValueError:
                    pass
                break

        # Buffer the body (capped) so a length-less/chunked overflow is caught too, then
        # replay it to the app. `trailer` forwards a non-body message (e.g. a mid-stream
        # http.disconnect) untouched.
        chunks: list[bytes] = []
        trailer: dict | None = None
        seen = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailer = message
                break
            chunks.append(message.get("body", b""))
            seen += len(chunks[-1])
            if seen > self.max_bytes:
                return await self._send_413(send)
            if not message.get("more_body", False):
                break

        replay = [
            {"type": "http.request", "body": b"".join(chunks), "more_body": False},
            trailer if trailer is not None else {"type": "http.disconnect"},
        ]
        idx = 0

        async def buffered_receive():
            nonlocal idx
            if idx < len(replay):
                message = replay[idx]
                idx += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, buffered_receive, send)

    async def _send_413(self, send) -> None:
        import json

        body = json.dumps({"detail": "request body too large"}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


# --- app ---------------------------------------------------------------------

app = FastAPI(title="WHIRLS deployment forecast")
# gzip every sizeable response in-app (Starlette's middleware) rather than at a
# gateway, so every deployment shape — the two-port dev flow, the gateway, any
# future proxy — compresses, and the dev flow measures what production ships.
# `minimum_size` keeps tiny responses like `/api/forecast/limits` and error bodies
# uncompressed, where the codec overhead beats the savings.
app.add_middleware(GZipMiddleware, minimum_size=1024)
# The only real deployment is same-origin (the gateway serves /map and /api under
# one host), so it exercises no CORS at all. The sole cross-origin caller is the
# two-port dev flow — the static map on :8000 fetching this API on :8001 (see
# ``resolveApi`` in app.js) — so scope CORS to those localhost dev origins and to the
# two methods the client uses (the forecast POST + its Content-Type, and the GET the
# limits probe sends), not the wildcard a public endpoint shouldn't advertise.
_DEV_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
# The body-size guard sits *inside* CORS (added before it) so the 413 it emits still
# flows back out through CORSMiddleware and carries the CORS headers — otherwise a
# cross-origin caller (the two-port dev flow) would see the 413 as an opaque CORS error
# instead of the status/body. It is still outside GZip and the route, so it rejects an
# oversized body before anything parses it (SEC-4).
app.add_middleware(_BodySizeLimitMiddleware, max_bytes=_MAX_BODY_BYTES)
# Added last -> outermost, so every response (including the body-size 413) gets CORS
# headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.post("/api/forecast")
def forecast(req: ForecastRequest) -> dict:
    """Batch current-advection run: a sequence of ``(lon, lat, start)`` seeds plus
    a run-level ``direction``/``horizon_h`` in, one ``LineString`` per in-window
    seed out (see :func:`_batch_run`). A sync endpoint, so FastAPI runs it in the
    threadpool — reloading the field index on a fresh build write can block one
    request without stalling the static map. The whole batch advects in ~1-2 s
    even at the seed cap (vectorized RK4), comfortably inside the gateway's 60 s
    timeout."""
    try:
        seeds = tuple((s.lon, s.lat, s.start) for s in req.seeds)
        return _cached_batch_run(_field_version(), req.direction, req.horizon_h, seeds)
    except (FileNotFoundError, _field_store.FieldUnavailableError):
        # Store missing/empty/still-filling/gapped/corrupt — a transient operational
        # state, not a bug; the static map still serves. Fixed message: the exception can
        # name the store path (SEC-2/SEC-7). Caught *before* the ValueError branch below
        # because FieldUnavailableError subclasses ValueError. Any *other* exception is a
        # real 500, left to surface (and be logged) rather than masked as a 503.
        raise HTTPException(status_code=503, detail=_FIELD_UNAVAILABLE_DETAIL)
    except ValueError as exc:
        # Client input only: no seeds / unparseable start / seed-start spread too wide.
        # These messages echo the caller's own input (timestamps, counts), never
        # internal state, so they are safe to return.
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/forecast/limits")
def limits() -> dict:
    """The request bounds and field reach the deploy-tool client pre-validates
    placements against, so it can reject an out-of-window or over-cap deployment
    with an explicit message *before* POSTing. A plain GET, reached under the
    same CORS as the forecast POST (see the middleware note)."""
    try:
        field_lo, field_hi = _get_field_index()
    except (FileNotFoundError, _field_store.FieldUnavailableError):
        # store missing/empty/corrupt — the static map still serves (SEC-2/SEC-7)
        raise HTTPException(status_code=503, detail=_FIELD_UNAVAILABLE_DETAIL)
    now_iso = _time.now_iso()
    return {
        "max_seeds": _MAX_SEEDS,
        "max_seed_hours": _MAX_SEED_HOURS,
        "max_start_spread_days": _MAX_START_SPREAD_DAYS,
        "window": [_time.iso_z_from_epoch(_time.to_epoch(field_lo)), _time.iso_z_from_epoch(_time.to_epoch(field_hi))],
        "analysis_edge": now_iso,
    }


def main() -> None:
    import uvicorn

    print(f"forecast API on http://localhost:{_PORT}/api/forecast")
    print("serve the map separately: `pixi run serve` (static, :8000)")
    print("reads the incremental field store (see _field_store; write it with a `derive` --tier slow run)")
    uvicorn.run(app, host="0.0.0.0", port=_PORT)


if __name__ == "__main__":
    main()
