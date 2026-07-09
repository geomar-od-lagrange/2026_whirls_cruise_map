"""Tests for the interactive batch-forecast API (``_api``).

Synthetic — no network, no CMEMS. A constant eastward current makes RK4 exact,
so the tests pin the *bookkeeping* the batch endpoint is responsible for: the
synced-t0 dot schedule and the absolute-hour relabel that colours the array. In
particular they guard the relabel-by-value invariant — a drop whose staggered
water-entry lands just before a mark multiple must NOT shift the colour/label of
its remaining dots (the bug a position-based ``zip`` relabel had).
"""
from __future__ import annotations

import os
import threading

import numpy as np
import pytest
import xarray as xr
from pydantic import ValidationError
from pytest import approx

from whirls_cruise_map import _api, _forecast

# A run start well inside a wide, land-free window; a constant 0.5 m/s eastward
# current (vo = 0), so a particle drifts ~0.78° east over 48 h — comfortably
# inside the grid, and RK4 reproduces it exactly (no truncation, no inertial loop).
T0 = np.datetime64("2026-07-03T00:00:00", "s")
U_EAST = 0.5


def _constant_window(u_east: float = U_EAST) -> xr.Dataset:
    """A land-free hourly window (-6 .. +60 h) of constant eastward current."""
    lats = -35.0 + 0.25 * np.arange(12)  # ~ -35 .. -32.25
    lons = 10.0 + 0.25 * np.arange(24)   # ~ 10 .. 15.75, room for 48 h of drift
    times = T0 + (np.arange(-6, 61)).astype("timedelta64[h]")  # -6 .. +60 h
    shape = (times.size, lats.size, lons.size)
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), np.full(shape, u_east)),
            "vo": (("time", "latitude", "longitude"), np.zeros(shape)),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


def _constant_field() -> _forecast._Field:
    return _forecast._Field(_constant_window())


def _iso(offset_h: float) -> str:
    return _api._iso(float(T0.astype(np.float64)) + offset_h * 3600.0)


def _marks_hours(feature: dict) -> list[float]:
    return [m["hours"] for m in feature["properties"]["marks"]]


def test_seed_marks_are_elapsed_from_entry_after_the_offset():
    # No stagger: every 3 h mark up to the horizon, as elapsed hours from entry.
    assert _api._seed_marks(0.0, 48.0, 3.0) == tuple(float(k) for k in range(3, 49, 3))
    # A 2.97 h offset admits the 3 h mark (strict >) as a 0.03 h elapsed mark — the
    # one that rounds to integrator step 0 and so must not desync the rest.
    rel = _api._seed_marks(2.97, 48.0, 3.0)
    assert rel[0] == approx(0.03)  # unrounded here; the relabel rounds to the grid
    assert len(rel) == 16  # 3,6,…,48 h absolute all fall after 2.97 h


def test_synced_marks_stay_on_the_absolute_grid_across_a_staggered_array(monkeypatch):
    """The relabel-by-value guard. Drop B enters at +2.97 h, so its first admitted
    mark (+3 h absolute) is only 0.03 h after entry and the integrator drops it. Its
    remaining dots must still carry their TRUE absolute hours (6,9,…,48), not be
    shifted down one step (6→3, 9→6, …) as a position-based relabel would do."""
    field = _constant_field()
    monkeypatch.setattr(_api, "_get_sampler", lambda: field)

    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start=_iso(0.0)),     # A: run start, offset 0
        _api.Seed(lon=10.5, lat=-33.0, start=_iso(2.97)),    # B: offset 2.97 h
    ]
    out = _api._batch_forecast(seeds, horizon_h=48.0, mark_step_h=3.0)

    assert out["properties"]["forecasts"] == 2
    assert out["properties"]["skipped"] == 0
    a, b = out["features"]

    # A (no stagger): the full 3,6,…,48 h grid.
    assert _marks_hours(a) == [float(k) for k in range(3, 49, 3)]

    # B: the +3 h dot is absent (too close to entry to place), but every remaining
    # dot sits exactly on the absolute grid — no fractional leak, no downshift.
    hb = _marks_hours(b)
    assert hb == [float(k) for k in range(6, 49, 3)]
    assert 3.0 not in hb
    assert all(h % 3 == 0 for h in hb)  # exact multiples, i.e. relabelled by value


_ONE_SEED = [{"lon": 10.5, "lat": -34.0, "start": "2026-07-03T00:00:00Z"}]


def test_forecast_request_accepts_a_normal_deployment():
    req = _api.ForecastRequest(seeds=_ONE_SEED)
    assert req.horizon_h == _api._DEFAULT_HORIZON_H
    assert req.mark_step_h == _api._DEFAULT_MARK_STEP_H
    assert len(req.seeds) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        # High-1: a large horizon + tiny step would make _seed_marks materialise a
        # multi-GB tuple → OOM-kill the pod. Both knobs are now bounded.
        {"horizon_h": 1e9, "mark_step_h": 3.0},
        {"horizon_h": 1e12, "mark_step_h": 1e-6},
        {"mark_step_h": 0.0},          # also the ZeroDivisionError path
        {"horizon_h": float("inf")},
        {"horizon_h": float("nan")},
        # High-2: an uncapped seed list pins the single sync worker (CPU exhaustion);
        # one past the cap is rejected (the cap is _MAX_SEEDS, not a literal here).
        {"seeds": _ONE_SEED * (_api._MAX_SEEDS + 1)},
        # extra="forbid": unknown fields are rejected, not silently ignored.
        {"foo": 1},
    ],
)
def test_forecast_request_rejects_resource_exhaustion_payloads(kwargs):
    kwargs.setdefault("seeds", _ONE_SEED)
    with pytest.raises(ValidationError):
        _api.ForecastRequest(**kwargs)


def test_forecast_request_accepts_up_to_the_seed_cap():
    # The cap has one source of truth (_MAX_SEEDS): exactly the cap validates, one past
    # it is rejected. This is the same bound /api/forecast/limits advertises so the
    # client can pre-reject an over-cap deployment before POSTing.
    _api.ForecastRequest(seeds=_ONE_SEED * _api._MAX_SEEDS)  # no raise at the cap
    with pytest.raises(ValidationError):
        _api.ForecastRequest(seeds=_ONE_SEED * (_api._MAX_SEEDS + 1))


def test_limits_endpoint_echoes_the_seed_cap():
    # The client fetches this instead of hardcoding the cap, so it must report the very
    # constant the request model enforces (single source of truth).
    assert _api.limits() == {"max_seeds": _api._MAX_SEEDS}


def test_forecast_request_bounds_the_derived_mark_count():
    # The widest allowed request still caps _seed_marks at ~960 marks per seed.
    req = _api.ForecastRequest(seeds=_ONE_SEED, horizon_h=240, mark_step_h=0.25)
    assert int(req.horizon_h // req.mark_step_h) == 960


def test_out_of_window_seeds_are_skipped_not_errored(monkeypatch):
    field = _constant_field()
    monkeypatch.setattr(_api, "_get_sampler", lambda: field)
    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start=_iso(0.0)),      # in window
        _api.Seed(lon=10.5, lat=-34.0, start=_iso(500.0)),    # far past window end
    ]
    out = _api._batch_forecast(seeds, horizon_h=48.0, mark_step_h=3.0)
    assert out["properties"]["forecasts"] == 1
    assert out["properties"]["skipped"] == 1
    assert out["properties"]["n_seeds"] == 2


# --- window loading: PVC file + reload-on-mtime ------------------------------


@pytest.fixture
def _fresh_sampler():
    """Reset the module-level cached sampler around a test (it persists globally)."""
    _api._sampler = None
    _api._sampler_mtime = None
    yield
    _api._sampler = None
    _api._sampler_mtime = None


def test_get_sampler_reloads_only_when_the_window_mtime_changes(
    tmp_path, monkeypatch, _fresh_sampler
):
    """The production shape: the API reads the cron-written window and rebuilds the
    sampler only when the file's mtime changes — so a fresh cron write is picked up
    within one request (no restart), while repeated requests reuse the cached field."""
    path = tmp_path / "forecast_window.nc"
    _constant_window(u_east=0.5).to_netcdf(path)
    monkeypatch.setattr(_api, "_WINDOW_PATH", path)

    s1 = _api._get_sampler()
    assert s1.u[0, 0, 0] == approx(0.5)
    assert _api._get_sampler() is s1  # unchanged mtime → no rebuild, same instance

    # A fresh cron write (new field, newer mtime) is reloaded without a restart.
    _constant_window(u_east=0.9).to_netcdf(path)
    bumped = path.stat().st_mtime + 10
    os.utime(path, (bumped, bumped))
    s2 = _api._get_sampler()
    assert s2 is not s1
    assert s2.u[0, 0, 0] == approx(0.9)


def test_get_sampler_raises_when_the_window_is_missing(tmp_path, monkeypatch, _fresh_sampler):
    """A missing window file → the 503 the endpoint maps field-unavailable to; the
    API never falls back to a CMEMS fetch (it has no credentials in the deployment)."""
    monkeypatch.setattr(_api, "_WINDOW_PATH", tmp_path / "not_written_yet.nc")
    with pytest.raises(FileNotFoundError):
        _api._get_sampler()


# --- result cache + single-flight (survive the 60 s gateway timeout) ---------


@pytest.fixture
def _fresh_cache():
    """Clear the process-local result cache around a test (it persists globally)."""
    with _api._cache_lock:
        _api._cache.clear()
    yield
    with _api._cache_lock:
        _api._cache.clear()


def _seed(offset_h: float = 0.0) -> _api.Seed:
    return _api.Seed(lon=10.5, lat=-34.0, start=_iso(offset_h))


def test_cached_forecast_computes_once_then_serves_the_cache(monkeypatch, _fresh_cache):
    """The retry-after-timeout contract: an identical request re-POSTed (same seeds,
    knobs, and field version) computes once and replays the cached FeatureCollection —
    it does not re-run the GIL-bound advection."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    calls = []
    monkeypatch.setattr(
        _api, "_batch_forecast", lambda *a: calls.append(a) or {"n": len(calls)}
    )
    seeds = [_seed()]

    first = _api._cached_forecast(seeds, 48.0, 3.0)
    second = _api._cached_forecast(seeds, 48.0, 3.0)
    assert len(calls) == 1  # computed once
    assert first is second  # the very same cached object, not a recompute


def test_cached_forecast_recomputes_when_the_field_version_changes(monkeypatch, _fresh_cache):
    """A fresh cron write bumps the window mtime, so the same seeds key differently and
    are recomputed — a new field never serves the old field's forecast."""
    version = [1.0]
    monkeypatch.setattr(_api, "_field_version", lambda: version[0])
    calls = []
    monkeypatch.setattr(_api, "_batch_forecast", lambda *a: calls.append(a) or {})
    seeds = [_seed()]

    _api._cached_forecast(seeds, 48.0, 3.0)
    version[0] = 2.0  # cron wrote a new window
    _api._cached_forecast(seeds, 48.0, 3.0)
    assert len(calls) == 2


def test_cached_forecast_recomputes_for_a_different_request(monkeypatch, _fresh_cache):
    """Different seeds (or knobs) key differently — the cache only replays an *identical*
    request, which is exactly what a client retry re-sends."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    calls = []
    monkeypatch.setattr(_api, "_batch_forecast", lambda *a: calls.append(a) or {})

    _api._cached_forecast([_seed(0.0)], 48.0, 3.0)
    _api._cached_forecast([_seed(1.0)], 48.0, 3.0)  # different seed time
    _api._cached_forecast([_seed(0.0)], 24.0, 3.0)  # different horizon
    assert len(calls) == 3


def test_cached_forecast_does_not_cache_failures(monkeypatch, _fresh_cache):
    """A transient failure (e.g. a 503 field-unavailable) must not be cached, or a retry
    would replay the error instead of recomputing once the field is back."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    boom = [True]

    def flaky(*_a):
        if boom[0]:
            raise ValueError("no field yet")
        return {"ok": True}

    monkeypatch.setattr(_api, "_batch_forecast", flaky)
    seeds = [_seed()]

    with pytest.raises(ValueError):
        _api._cached_forecast(seeds, 48.0, 3.0)
    assert not _api._cache  # the failed slot was removed, not cached

    boom[0] = False  # field is back
    assert _api._cached_forecast(seeds, 48.0, 3.0) == {"ok": True}  # retry recomputes


def test_cached_forecast_coalesces_concurrent_identical_requests(monkeypatch, _fresh_cache):
    """Single-flight: a retry that arrives while the first compute is still running waits
    on the leader rather than firing a second GIL-contending advection — the work runs
    once and both callers get the same result."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def slow(*_a):
        calls.append(1)
        entered.set()
        release.wait(5)
        return {"shared": True}

    monkeypatch.setattr(_api, "_batch_forecast", slow)
    seeds = [_seed()]
    results: dict[str, dict] = {}

    def call(tag):
        results[tag] = _api._cached_forecast(seeds, 48.0, 3.0)

    leader = threading.Thread(target=call, args=("leader",))
    leader.start()
    assert entered.wait(5)  # leader is now inside the (blocked) compute, holding the slot

    follower = threading.Thread(target=call, args=("follower",))
    follower.start()
    follower.join(0.2)
    assert follower.is_alive()  # coalesced onto the leader — waiting, not recomputing

    release.set()
    leader.join(5)
    follower.join(5)
    assert len(calls) == 1  # advected once despite the concurrent retry
    assert results["leader"] is results["follower"]


def test_cache_is_bounded(monkeypatch, _fresh_cache):
    """Memory is bounded: distinct requests past the cap evict the oldest results (the
    field-version key also rotates stale entries out on each cron write)."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    monkeypatch.setattr(_api, "_batch_forecast", lambda *a: {})
    for i in range(_api._CACHE_MAX_ENTRIES + 5):
        _api._cached_forecast([_seed(float(i))], 48.0, 3.0)
    assert len(_api._cache) == _api._CACHE_MAX_ENTRIES


def test_evict_locked_keeps_pending_slots(_fresh_cache):
    """Eviction never sheds an in-flight (not-``done``) slot, even the oldest one: evicting
    a still-computing leader's slot would let its own retry miss and start a redundant
    second advection. Only completed results are shed to the cap."""
    with _api._cache_lock:
        pending = _api._Slot()  # oldest → the first popitem(last=False) would take
        _api._cache["pending"] = pending
        for i in range(_api._CACHE_MAX_ENTRIES + 3):
            done = _api._Slot()
            done.done.set()
            done.result = {}
            _api._cache[f"done-{i}"] = done
        _api._evict_locked()
    assert "pending" in _api._cache  # kept despite being the oldest entry
    assert len(_api._cache) == _api._CACHE_MAX_ENTRIES  # completed results shed to the cap


def test_follower_recomputes_when_leader_fails(monkeypatch, _fresh_cache):
    """A follower that coalesced onto a leader which then *fails* must recompute on its own
    — not inherit (and re-raise) the leader's exception. The leader's error was never the
    follower's; a transient field blip that cleared should let the follower succeed."""
    monkeypatch.setattr(_api, "_field_version", lambda: 1.0)
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def flaky(*_a):
        first = not calls
        calls.append(1)
        if first:  # the leader: block until released, then fail transiently
            entered.set()
            release.wait(5)
            raise ValueError("leader transient failure")
        return {"ok": True}  # the follower's own recompute succeeds

    monkeypatch.setattr(_api, "_batch_forecast", flaky)
    seeds = [_seed()]
    out: dict[str, object] = {}

    def leader():
        try:
            _api._cached_forecast(seeds, 48.0, 3.0)
        except Exception as exc:  # the leader propagates its OWN failure
            out["leader_error"] = exc

    def follower():
        out["follower"] = _api._cached_forecast(seeds, 48.0, 3.0)

    lt = threading.Thread(target=leader)
    lt.start()
    assert entered.wait(5)  # leader is inside the (blocked) compute, holding the slot

    ft = threading.Thread(target=follower)
    ft.start()
    ft.join(0.2)
    assert ft.is_alive()  # follower coalesced onto the leader — waiting, not recomputing

    release.set()  # leader now fails
    lt.join(5)
    ft.join(5)
    assert isinstance(out["leader_error"], ValueError)  # leader saw its own error
    assert out["follower"] == {"ok": True}  # follower recomputed, did not inherit the error
    assert len(calls) == 2  # one failed leader + one independent follower recompute
    # The failed leader's slot was removed; the follower's own success replaced it (and is
    # now cached for any further retry) — so exactly one, successful, entry remains.
    assert [s.result for s in _api._cache.values()] == [{"ok": True}]
