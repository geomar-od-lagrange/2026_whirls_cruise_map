"""Tests for the interactive batch-forecast API (``_api``).

Synthetic — no network, no CMEMS. A constant eastward current makes RK4 exact,
so the tests pin the *bookkeeping* the batch endpoint is responsible for: the
synced-t0 dot schedule and the absolute-hour relabel that colours the array. In
particular they guard the relabel-by-value invariant — a drop whose staggered
water-entry lands just before a mark multiple must NOT shift the colour/label of
its remaining dots (the bug a position-based ``zip`` relabel had).
"""
from __future__ import annotations

import numpy as np
import xarray as xr
from pytest import approx

from whirls_cruise_map import _api, _forecast

# A run start well inside a wide, land-free window; a constant 0.5 m/s eastward
# current (vo = 0), so a particle drifts ~0.78° east over 48 h — comfortably
# inside the grid, and RK4 reproduces it exactly (no truncation, no inertial loop).
T0 = np.datetime64("2026-07-03T00:00:00", "s")
U_EAST = 0.5


def _constant_field() -> _forecast._Field:
    lats = -35.0 + 0.25 * np.arange(12)  # ~ -35 .. -32.25
    lons = 10.0 + 0.25 * np.arange(24)   # ~ 10 .. 15.75, room for 48 h of drift
    times = T0 + (np.arange(-6, 61)).astype("timedelta64[h]")  # -6 .. +60 h
    shape = (times.size, lats.size, lons.size)
    window = xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), np.full(shape, U_EAST)),
            "vo": (("time", "latitude", "longitude"), np.zeros(shape)),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )
    return _forecast._Field(window)


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
