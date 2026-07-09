"""Tests for the interactive batch-forecast API (``_api``).

Synthetic — no network, no CMEMS. A constant eastward current makes RK4 exact,
so the tests pin the *bookkeeping* the batch endpoint is responsible for: the
synced-t0 dot schedule and the absolute-hour relabel that colours the array. In
particular they guard the relabel-by-value invariant — a drop whose staggered
water-entry lands just before a mark multiple must NOT shift the colour/label of
its remaining dots (the bug a position-based ``zip`` relabel had).

The whole batch advects through the **vectorized** RK4
(:func:`whirls_cruise_map._forecast._batch_advect`, all seeds in lockstep). A
dedicated test pins it **bit-identical** to advecting each seed with the scalar
integrator over a non-trivial field with land — so the fast path can never drift
from the reference physics without failing CI.
"""
from __future__ import annotations

import os

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


# --- vectorized == scalar (the bit-identity guard) ---------------------------
#
# The batch endpoint advects the whole request in one vectorized RK4 pass
# (_forecast._batch_advect). That is a performance rewrite of the per-seed scalar
# integrator the build still uses; the two MUST produce identical output or a
# deployment forecast silently diverges from the reference physics. This section
# pins them together over a non-trivial field (spatially + temporally varying, with
# a land patch that forces coast truncation) and staggered starts (incl. an
# out-of-window skip). A future change to _Field.velocity that isn't mirrored in
# _vec_deriv fails here.


def _varying_window_with_land() -> xr.Dataset:
    """A non-constant hourly window with a rectangular NaN land patch. The velocity
    varies in space, lat, and time so bilinear-space + linear-time + RK4 are all
    exercised (not the trivial constant-flow case); the land patch makes seeds that
    drift into it truncate at the coast, exactly as the real field does."""
    lats = -35.0 + 0.1 * np.arange(40)   # -35 .. -31.1
    lons = 10.0 + 0.1 * np.arange(60)    # 10 .. 15.9
    times = T0 + np.arange(-6, 61).astype("timedelta64[h]")
    lon2d, lat2d = np.meshgrid(lons, lats)  # (lat, lon)
    u = np.empty((times.size, lats.size, lons.size))
    v = np.empty_like(u)
    for it in range(times.size):
        ph = it * 0.15
        u[it] = 0.4 * np.sin(np.radians((lon2d - 10.0) * 20.0) + ph) + 0.25
        v[it] = 0.3 * np.cos(np.radians((lat2d + 35.0) * 20.0) - ph)
    # A land block seeds drift east into (u is net-eastward), forcing truncation.
    u[:, 15:25, 40:50] = np.nan
    v[:, 15:25, 40:50] = np.nan
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), u),
            "vo": (("time", "latitude", "longitude"), v),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


def _scalar_reference(seeds, horizon_h, mark_step_h, sampler) -> dict:
    """The pre-vectorization per-seed algorithm: loop the scalar integrator via
    ``_advection_feature`` and relabel each mark by value. Kept here (not in the
    product) as the oracle the vectorized ``_batch_forecast`` is pinned against."""
    lo, hi = float(sampler.times[0]), float(sampler.times[-1])
    starts = [_api._parse_start(s.start) for s in seeds]
    run_start = min(starts)
    features, n_forecasts, n_skipped = [], 0, 0
    for i, (seed, entry) in enumerate(zip(seeds, starts)):
        offset_h = (entry - run_start) / 3600.0
        horizon_i = horizon_h - offset_h
        if not (lo <= entry <= hi) or horizon_i <= 0:
            n_skipped += 1
            continue
        rel_marks = _api._seed_marks(offset_h, horizon_h, mark_step_h)
        t0, valid = _forecast._anchor_t0(sampler, entry)
        feature = _forecast._advection_feature(
            sampler, {"role": "forecast", "index": i}, seed.lon, seed.lat, t0, valid,
            1, horizon_h=horizon_i, mark_hours=rel_marks,
        )
        if feature is None:
            n_skipped += 1
            continue
        for mark in feature["properties"]["marks"]:
            mark["hours"] = round(mark["hours"] + offset_h, 3)
        features.append(feature)
        n_forecasts += 1
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "run_start": _api._iso(run_start),
            "horizon_h": horizon_h,
            "mark_step_h": mark_step_h,
            "n_seeds": len(seeds),
            "forecasts": n_forecasts,
            "skipped": n_skipped,
            "window": [_api._iso(lo), _api._iso(hi)],
        },
    }


def _guard_seeds(field: _forecast._Field, n: int, seed: int) -> list[_api.Seed]:
    """Staggered-start seeds: ocean cells (drift freely) + coast-adjacent cells
    (drift into the land patch and truncate) + one out-of-window start (skipped)."""
    mid = field.u.shape[0] // 2
    ocean = np.isfinite(field.u[mid])
    cell_ok = ocean[:-1, :-1] & ocean[:-1, 1:] & ocean[1:, :-1] & ocean[1:, 1:]
    idx = np.argwhere(cell_ok)
    rng = np.random.default_rng(seed)
    base = np.datetime64(int(field.times[0]), "s") + np.timedelta64(1, "h")
    seeds = []
    for j in range(n):
        iy, ix = idx[rng.integers(len(idx))]
        fx, fy = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
        lon = field.lons[ix] + fx * (field.lons[ix + 1] - field.lons[ix])
        lat = field.lats[iy] + fy * (field.lats[iy + 1] - field.lats[iy])
        start = str(base + np.timedelta64(37 * j, "s")) + "Z"
        seeds.append(_api.Seed(lon=float(lon), lat=float(lat), start=start))
    # one start before the window → must be skipped, not errored
    seeds.append(_api.Seed(lon=11.0, lat=-34.0, start=str(base - np.timedelta64(3, "h")) + "Z"))
    return seeds


@pytest.mark.parametrize("horizon_h, mark_step_h", [(48.0, 3.0), (72.0, 1.5), (24.0, 0.5)])
def test_vectorized_batch_matches_the_scalar_reference(monkeypatch, horizon_h, mark_step_h):
    """The whole vectorized FeatureCollection must equal the scalar per-seed reference
    to the last decimal — same forecast/skip counts, same truncated coords, same marks —
    over a varying field with land and staggered starts. This is what lets the build
    keep the scalar integrator while the API uses the fast batch path."""
    field = _forecast._Field(_varying_window_with_land())
    monkeypatch.setattr(_api, "_get_sampler", lambda: field)
    seeds = _guard_seeds(field, 80, seed=7)

    fast = _api._batch_forecast(seeds, horizon_h, mark_step_h)
    ref = _scalar_reference(seeds, horizon_h, mark_step_h, field)

    # Some seeds must actually truncate at the land patch (else the guard is vacuous),
    # and at least the one out-of-window seed must be skipped.
    assert ref["properties"]["skipped"] >= 1
    assert any(
        len(f["geometry"]["coordinates"]) < 1 + horizon_h * 60 // _forecast.VERTEX_MIN
        for f in ref["features"]
    )
    assert fast == ref  # deep equality: properties, coords, and marks all identical


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
