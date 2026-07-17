"""Unit tests for whirls_cruise_map._forecast that exercise the module directly
(no API layer, no store): the vectorized batch's backward direction pinned
bit-identical to the scalar backward reference, and the adaptive vertex
cadence helper.
"""
from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from whirls_cruise_map import _forecast

T0 = np.datetime64("2026-07-03T00:00:00", "s")


def _varying_window_with_land() -> xr.Dataset:
    """A non-constant hourly window (spatially + temporally varying) with a
    rectangular NaN land patch, wide enough in time to run both forward and
    backward — the same non-trivial-field shape test_forecast_api.py's
    vectorized-vs-scalar pinning test uses."""
    lats = -35.0 + 0.1 * np.arange(40)   # -35 .. -31.1
    lons = 10.0 + 0.1 * np.arange(60)    # 10 .. 15.9
    times = T0 + np.arange(-60, 61).astype("timedelta64[h]")
    lon2d, lat2d = np.meshgrid(lons, lats)
    u = np.empty((times.size, lats.size, lons.size))
    v = np.empty_like(u)
    for it in range(times.size):
        ph = it * 0.15
        u[it] = 0.4 * np.sin(np.radians((lon2d - 10.0) * 20.0) + ph) + 0.25
        v[it] = 0.3 * np.cos(np.radians((lat2d + 35.0) * 20.0) - ph)
    u[:, 15:25, 40:50] = np.nan
    v[:, 15:25, 40:50] = np.nan
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), u),
            "vo": (("time", "latitude", "longitude"), v),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


def _guard_seeds(field: _forecast._Field, n: int, seed: int):
    """``(lon0, lat0, t0)`` staggered seeds in ocean cells at the field's middle
    time step — some close enough to the land patch to truncate there, mirroring
    ``test_forecast_api._guard_seeds`` but returning arrays directly (no
    ``_api.Seed`` wrapping, since this test drives ``_batch_advect`` and
    ``_integrate`` directly)."""
    mid = field.u.shape[0] // 2
    ocean = np.isfinite(field.u[mid])
    cell_ok = ocean[:-1, :-1] & ocean[:-1, 1:] & ocean[1:, :-1] & ocean[1:, 1:]
    idx = np.argwhere(cell_ok)
    rng = np.random.default_rng(seed)
    base = float(field.times[len(field.times) // 2])
    lon0 = np.empty(n)
    lat0 = np.empty(n)
    t0 = np.empty(n)
    for j in range(n):
        iy, ix = idx[rng.integers(len(idx))]
        fx, fy = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
        lon0[j] = field.lons[ix] + fx * (field.lons[ix + 1] - field.lons[ix])
        lat0[j] = field.lats[iy] + fy * (field.lats[iy + 1] - field.lats[iy])
        t0[j] = base + 37.0 * j  # stagger starts, mirroring the API pinning test
    return lon0, lat0, t0


# --- batch-backward pinned against the scalar backward reference ---------------


@pytest.mark.parametrize("horizon_h", [6.0, 24.0])
def test_batch_backward_matches_scalar_backward(horizon_h):
    """Extends the vectorized-vs-scalar pinning test (test_forecast_api.py) to
    the newly-signed direction: a backward batch run must agree with the scalar
    ``_integrate(..., direction=-1)`` reference to the last decimal, over a
    field with land (forcing coast truncation) and staggered starts (some
    truncating at different steps)."""
    field = _forecast._Field(_varying_window_with_land())
    lon0, lat0, t0 = _guard_seeds(field, 60, seed=11)
    n_steps = np.full(lon0.shape, round(horizon_h * 60.0 / _forecast.STEP_MIN), dtype=int)

    positions, completed = _forecast._batch_advect(
        field, lon0, lat0, t0, n_steps, direction=-1
    )

    vertex_every = round(_forecast.VERTEX_MIN / _forecast.STEP_MIN)
    nd = _forecast._COORD_NDIGITS
    truncated_any = False
    for i in range(len(lon0)):
        coords, _marks = _forecast._integrate(
            field, lon0[i], lat0[i], t0[i], -1, horizon_h=horizon_h, mark_hours=()
        )
        cs = int(completed[i])
        batch_coords = [
            [round(float(positions[i, s, 0]), nd), round(float(positions[i, s, 1]), nd)]
            for s in range(0, cs + 1, vertex_every)
        ]
        assert batch_coords == coords
        if cs < n_steps[i]:
            truncated_any = True

    # The land patch must actually bite for at least one staggered seed, or this
    # guard is vacuous (every seed running the full horizon unobstructed).
    assert truncated_any


# --- vertex-cadence storage (FC-1) --------------------------------------------


def test_batch_advect_vertex_cadence_matches_strided_full_storage():
    """FC-1: storing only every ``vertex_every``-th sub-step yields exactly the coords a
    full-substep run read at that stride, in a buffer ``vertex_every``-fold smaller. The
    integration still steps at the fine cadence, so the stored vertices are identical to
    the strided full trajectory — and ``completed`` comes back as a vertex index."""
    field = _forecast._Field(_varying_window_with_land())
    lon0, lat0, t0 = _guard_seeds(field, 40, seed=7)
    n_steps = np.full(lon0.shape, 120, dtype=int)  # 120 fine sub-steps
    ve = 3

    p_full, c_full = _forecast._batch_advect(field, lon0, lat0, t0, n_steps, vertex_every=1)
    p_vtx, c_vtx = _forecast._batch_advect(field, lon0, lat0, t0, n_steps, vertex_every=ve)

    # The vertex buffer is ~ve-fold smaller along the step axis.
    assert p_full.shape[1] == 121
    assert p_vtx.shape[1] == (p_full.shape[1] - 1) // ve + 1  # 41
    # `completed` is reported as a vertex index (the fine index floored by ve).
    np.testing.assert_array_equal(c_vtx, c_full // ve)
    # Every stored vertex equals the full run's position at that sub-step — bit-exact.
    for v in range(p_vtx.shape[1]):
        np.testing.assert_array_equal(p_vtx[:, v], p_full[:, v * ve])
    # The land patch must actually truncate at least one seed, or the completed-index
    # relationship above is vacuous (every seed running the full 120 steps).
    assert (c_full < 120).any()


# --- adaptive vertex cadence ---------------------------------------------------


def test_cadence_stays_at_base_for_48h():
    assert _forecast._vertex_cadence_min(48.0) == _forecast.VERTEX_MIN


@pytest.mark.parametrize("horizon_h", [0.5, 1.0, 6.0, 48.0, 100.0, 240.0, 600.0, 1000.0])
def test_cadence_is_always_a_multiple_of_base_and_bounds_vertex_count(horizon_h):
    cadence = _forecast._vertex_cadence_min(horizon_h)
    assert cadence % _forecast.VERTEX_MIN == 0
    n_vertices = int(horizon_h * 60.0 // cadence) + 1
    assert n_vertices <= 400


def test_cadence_widens_for_a_long_horizon():
    # A 600 h horizon at the base 15 min cadence would be 2401 vertices; the
    # helper must widen well past the base to fit the ~400-vertex budget.
    cadence = _forecast._vertex_cadence_min(600.0)
    assert cadence > _forecast.VERTEX_MIN
