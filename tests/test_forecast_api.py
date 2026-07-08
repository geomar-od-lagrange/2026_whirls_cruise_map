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
        # High-2: an uncapped seed list pins the single sync worker (CPU exhaustion).
        {"seeds": _ONE_SEED * 501},
        # extra="forbid": unknown fields are rejected, not silently ignored.
        {"foo": 1},
    ],
)
def test_forecast_request_rejects_resource_exhaustion_payloads(kwargs):
    kwargs.setdefault("seeds", _ONE_SEED)
    with pytest.raises(ValidationError):
        _api.ForecastRequest(**kwargs)


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
