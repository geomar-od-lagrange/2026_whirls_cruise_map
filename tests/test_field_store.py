"""The incremental per-day field store (_field_store): backfill day selection
and newest-first fetch order, resumability across runs (final days skipped,
non-final days and --refetch-all refetched), the final-margin rollover rule,
a mid-backfill per-day failure leaving a resumable manifest, and the
load_window read side (cross-file concatenation + bracketing + gap
detection). Entirely network-free: fetch_day/time_range/now are injected
throughout, per _field_store's own design for testability.

Also covers StoreField (plans/done/034-deployment-focused-app.md, workstream B,
stage 1): the streaming, store-backed drop-in for _forecast._Field that
_batch_advect consumes. These tests build a small multi-day synthetic store
via update_store and pin StoreField against the existing in-RAM _Field over
the exact same day files — same non-network, injected-fetcher pattern as the
rest of this module.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from whirls_cruise_map import _currents, _field_store, _forecast

_LATS = np.linspace(-55.0, -15.0, 5)
_LONS = np.linspace(-10.0, 35.0, 6)


def _day_dataset(day: date, value: float = 1.0) -> xr.Dataset:
    """24 hourly steps for `day` ([00:00 .. 23:00]), uo/vo constant at `value`
    (distinguishes which day's file a slice came from) as float64 — the store
    write path is what casts to float32, not the fetch."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    times = np.array(
        [np.datetime64((start + timedelta(hours=h)).replace(tzinfo=None), "ns") for h in range(24)]
    )
    shape = (24, _LATS.size, _LONS.size)
    uo = np.full(shape, float(value), dtype=np.float64)
    vo = np.full(shape, -float(value), dtype=np.float64)
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), uo),
            "vo": (("time", "latitude", "longitude"), vo),
        },
        coords={"time": times, "latitude": _LATS, "longitude": _LONS},
    )


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- fresh backfill: day selection + newest-first order -----------------------

def test_fresh_backfill_newest_first(tmp_path):
    calls = []

    def fetch_day(day):
        calls.append(day)
        return _day_dataset(day)

    tmin, now = _utc(2026, 6, 28), _utc(2026, 7, 2, 6)
    manifest = _field_store.update_store(
        tmp_path, tmin=tmin, now=now, fetch_day=fetch_day, time_range=lambda: (tmin, now)
    )

    expected_days = [date(2026, 6, 28) + timedelta(days=i) for i in range(5)]  # partial last day
    assert calls == list(reversed(expected_days))  # newest first
    assert set(manifest["days"]) == {d.isoformat() for d in expected_days}
    assert manifest["dataset_id"] == _currents.WINDOW_DATASET_ID
    assert manifest["bbox"] == _currents.BBOX
    for d in expected_days:
        assert (tmp_path / manifest["days"][d.isoformat()]["file"]).exists()
    # The partial, still-open last day is not final; the rest are (well past
    # FINAL_MARGIN_H behind `now`).
    assert manifest["days"][date(2026, 7, 2).isoformat()]["final"] is False
    assert manifest["days"][date(2026, 6, 28).isoformat()]["final"] is True


def test_default_tmin_comes_from_currents_constant(tmp_path):
    now = _utc(2026, 6, 29)
    manifest = _field_store.update_store(
        tmp_path,
        now=now,
        fetch_day=lambda day: _day_dataset(day),
        time_range=lambda: (_utc(2026, 6, 28), now),
    )
    assert manifest["tmin"] == _currents.FIELD_TMIN
    assert date(2026, 6, 28).isoformat() in manifest["days"]


# --- resumability across runs --------------------------------------------------

def test_second_run_skips_final_days_and_refetches_nonfinal(tmp_path):
    tmin, available_max = _utc(2026, 6, 28), _utc(2026, 7, 2)
    now = _utc(2026, 7, 2, 18)  # last day (07-02) stays non-final; the rest finalize

    first_calls = []
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        fetch_day=lambda day: (first_calls.append(day), _day_dataset(day))[1],
        time_range=lambda: (tmin, available_max),
    )
    assert len(first_calls) == 5

    second_calls = []
    manifest = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        fetch_day=lambda day: (second_calls.append(day), _day_dataset(day))[1],
        time_range=lambda: (tmin, available_max),
    )
    # Only the still-non-final last day is refetched; the four final days skip.
    assert second_calls == [date(2026, 7, 2)]
    assert manifest["days"][date(2026, 7, 2).isoformat()]["final"] is False
    assert manifest["days"][date(2026, 6, 28).isoformat()]["final"] is True


def test_refetch_all_refetches_final_days(tmp_path):
    tmin, available_max = _utc(2026, 6, 28), _utc(2026, 6, 30)
    now = _utc(2026, 7, 5)  # all 3 days safely final

    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        fetch_day=lambda day: _day_dataset(day),
        time_range=lambda: (tmin, available_max),
    )

    calls = []
    manifest = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        refetch_all=True,
        fetch_day=lambda day: (calls.append(day), _day_dataset(day))[1],
        time_range=lambda: (tmin, available_max),
    )
    assert len(calls) == 3
    assert all(d["final"] for d in manifest["days"].values())


# --- final-margin boundary -----------------------------------------------------

def test_final_margin_boundary(tmp_path):
    day = date(2026, 7, 1)
    day_end = _utc(2026, 7, 2)
    threshold = day_end + timedelta(hours=_field_store.FINAL_MARGIN_H)

    manifest_at = _field_store.update_store(
        tmp_path / "at",
        tmin=_utc(2026, 7, 1),
        now=threshold,  # exactly at the margin -> final
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (_utc(2026, 7, 1), _utc(2026, 7, 1)),
    )
    assert manifest_at["days"][day.isoformat()]["final"] is True

    manifest_before = _field_store.update_store(
        tmp_path / "before",
        tmin=_utc(2026, 7, 1),
        now=threshold - timedelta(seconds=1),  # one second inside -> not yet final
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (_utc(2026, 7, 1), _utc(2026, 7, 1)),
    )
    assert manifest_before["days"][day.isoformat()]["final"] is False


# --- interrupted backfill -------------------------------------------------------

def test_failed_day_leaves_resumable_manifest(tmp_path, caplog):
    tmin, available_max = _utc(2026, 6, 28), _utc(2026, 6, 30)
    now = _utc(2026, 7, 5)
    failing_day = date(2026, 6, 29)

    def flaky_fetch_day(day):
        if day == failing_day:
            raise RuntimeError("simulated CMEMS blip")
        return _day_dataset(day)

    with caplog.at_level(logging.WARNING):
        manifest = _field_store.update_store(
            tmp_path,
            tmin=tmin,
            now=now,
            fetch_day=flaky_fetch_day,
            time_range=lambda: (tmin, available_max),
        )
    assert any(str(failing_day) in rec.message for rec in caplog.records)

    # The failing day is absent (missing/non-final); the other two days on
    # either side of it in the newest-first order still completed.
    assert set(manifest["days"]) == {"2026-06-28", "2026-06-30"}
    on_disk = json.loads((tmp_path / "field_manifest.json").read_text())
    assert on_disk == manifest  # atomic per-day write left a valid manifest

    # Resumable: a later run (no more blips) fills in the gap.
    manifest2 = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        fetch_day=lambda day: _day_dataset(day),
        time_range=lambda: (tmin, available_max),
    )
    assert set(manifest2["days"]) == {"2026-06-28", "2026-06-29", "2026-06-30"}


def test_partial_day_never_marked_final(tmp_path):
    """A fetch that returns fewer than 24 hourly steps (no exception) must
    stay non-final regardless of how far behind `now` the day is, so it's
    retried rather than locked in as a silently incomplete day."""
    day = date(2026, 6, 28)

    def partial_fetch_day(d):
        ds = _day_dataset(d)
        return ds.isel(time=slice(0, 6))  # only 6 of 24 hours

    manifest = _field_store.update_store(
        tmp_path,
        tmin=_utc(2026, 6, 28),
        now=_utc(2026, 7, 5),  # well past FINAL_MARGIN_H behind the day
        fetch_day=partial_fetch_day,
        time_range=lambda: (_utc(2026, 6, 28), _utc(2026, 6, 28)),
    )
    assert manifest["days"][day.isoformat()]["final"] is False

    # Retried on the next run since it's still non-final.
    calls = []
    _field_store.update_store(
        tmp_path,
        tmin=_utc(2026, 6, 28),
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: (calls.append(d), _day_dataset(d))[1],
        time_range=lambda: (_utc(2026, 6, 28), _utc(2026, 6, 28)),
    )
    assert calls == [day]


# --- config-change guard --------------------------------------------------------

def test_update_store_refuses_bbox_change_without_refetch_all(tmp_path):
    tmin = available_max = _utc(2026, 6, 28)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )

    other_bbox = dict(_currents.BBOX, lon_min=_currents.BBOX["lon_min"] - 5.0)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_currents, "BBOX", other_bbox)
        with pytest.raises(RuntimeError, match="refusing to mix"):
            _field_store.update_store(
                tmp_path,
                tmin=tmin,
                now=_utc(2026, 7, 5),
                fetch_day=lambda d: _day_dataset(d),
                time_range=lambda: (tmin, available_max),
            )


def test_update_store_refetch_all_clears_stale_entries_on_config_change(tmp_path):
    tmin = available_max = _utc(2026, 6, 28)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )

    other_bbox = dict(_currents.BBOX, lon_min=_currents.BBOX["lon_min"] - 5.0)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_currents, "BBOX", other_bbox)
        manifest = _field_store.update_store(
            tmp_path,
            tmin=tmin,
            now=_utc(2026, 7, 5),
            refetch_all=True,
            fetch_day=lambda d: _day_dataset(d),
            time_range=lambda: (tmin, available_max),
        )
    assert manifest["bbox"] == other_bbox
    assert date(2026, 6, 28).isoformat() in manifest["days"]


def test_manifest_drops_entries_for_deleted_files(tmp_path):
    tmin = available_max = _utc(2026, 6, 28)
    manifest = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )
    day_file = tmp_path / manifest["days"][date(2026, 6, 28).isoformat()]["file"]
    day_file.unlink()

    manifest2 = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )
    # Refetched rather than left dangling once its file went missing.
    assert date(2026, 6, 28).isoformat() in manifest2["days"]
    assert day_file.exists()


# --- stored dtype ---------------------------------------------------------------

def test_day_file_stored_as_float32(tmp_path):
    tmin = available_max = _utc(2026, 6, 28)
    manifest = _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),  # float64 in
        time_range=lambda: (tmin, available_max),
    )
    path = tmp_path / manifest["days"][date(2026, 6, 28).isoformat()]["file"]
    with xr.open_dataset(path) as ds:
        assert ds["uo"].dtype == np.float32
        assert ds["vo"].dtype == np.float32


# --- load_window: concatenation, bracketing, slicing, gaps ----------------------

def test_load_window_concatenates_across_day_files_and_brackets(tmp_path):
    tmin, available_max = _utc(2026, 6, 28), _utc(2026, 6, 29)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d, value=d.day),  # value = day-of-month
        time_range=lambda: (tmin, available_max),
    )

    # t0/t1 sit inside the 06-28 file but near its edges, so the one-hour
    # bracket on each side reaches into the neighbouring day file.
    t0 = _utc(2026, 6, 28, 0, 30)
    t1 = _utc(2026, 6, 28, 23, 30)
    window = _field_store.load_window(tmp_path, t0=t0, t1=t1)

    times = window["time"].values
    assert times[0] == np.datetime64("2026-06-28T00:00:00", "ns")
    assert times[-1] == np.datetime64("2026-06-29T00:00:00", "ns")
    assert len(times) == 25  # 06-28's 24 hours + 06-29's bracket hour
    assert float(window["uo"].isel(time=0, latitude=0, longitude=0)) == 28.0
    assert float(window["uo"].isel(time=-1, latitude=0, longitude=0)) == 29.0


def test_load_window_gap_raises_value_error(tmp_path):
    tmin, available_max = _utc(2026, 6, 28), _utc(2026, 6, 30)
    missing_day = date(2026, 6, 29)

    def fetch_day(day):
        if day == missing_day:
            raise RuntimeError("simulated gap")
        return _day_dataset(day)

    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=fetch_day,
        time_range=lambda: (tmin, available_max),
    )

    with pytest.raises(ValueError, match="gap"):
        _field_store.load_window(
            tmp_path, t0=_utc(2026, 6, 28, 12), t1=_utc(2026, 6, 30, 12)
        )


def test_load_window_raises_no_data_before_with_partial_coverage(tmp_path):
    """`opened` is non-empty (the 06-28 file exists) but doesn't reach back
    far enough for the requested t0 — exercises the `times[0] > t0v` branch
    distinctly from the fully-empty-`opened` case."""
    tmin = available_max = _utc(2026, 6, 28)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )
    with pytest.raises(ValueError, match="no data before"):
        _field_store.load_window(
            tmp_path, t0=_utc(2026, 6, 27, 23), t1=_utc(2026, 6, 28, 12)
        )


def test_load_window_raises_no_data_after_with_partial_coverage(tmp_path):
    """`opened` is non-empty (the 06-28 file exists) but doesn't reach forward
    far enough for the requested t1 — exercises the `times[-1] < t1v` branch
    distinctly from the fully-empty-`opened` case."""
    tmin = available_max = _utc(2026, 6, 28)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )
    with pytest.raises(ValueError, match="no data after"):
        _field_store.load_window(
            tmp_path, t0=_utc(2026, 6, 28, 12), t1=_utc(2026, 6, 29, 1)
        )


def test_load_window_raises_outside_store_coverage(tmp_path):
    tmin = available_max = _utc(2026, 6, 28)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 5),
        fetch_day=lambda d: _day_dataset(d),
        time_range=lambda: (tmin, available_max),
    )
    with pytest.raises(ValueError):
        _field_store.load_window(
            tmp_path, t0=_utc(2026, 7, 1), t1=_utc(2026, 7, 2)
        )


# --- StoreField: store-backed drop-in for _forecast._Field ---------------------

_SF_LATS = -35.0 + 0.1 * np.arange(30)   # -35 .. -32.1
_SF_LONS = 10.0 + 0.1 * np.arange(50)    # 10 .. 14.9
_SF_REF = _utc(2026, 7, 1)               # phase reference, so the field is one
                                          # continuous function of absolute time
                                          # even though written day-by-day


def _varying_day_dataset(day: date) -> xr.Dataset:
    """One synthetic day of hourly uo/vo, spatially and temporally varying (not
    the trivial constant-flow case), with a fixed land (NaN) patch — the same
    non-trivial-field shape test_forecast_api.py's pinning test uses, but split
    across day files and keyed to a shared absolute-time phase so the field is
    physically continuous across day boundaries."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    times = np.array(
        [np.datetime64((start + timedelta(hours=h)).replace(tzinfo=None), "ns") for h in range(24)]
    )
    lon2d, lat2d = np.meshgrid(_SF_LONS, _SF_LATS)
    u = np.empty((24, _SF_LATS.size, _SF_LONS.size))
    v = np.empty_like(u)
    for h in range(24):
        it = int((start + timedelta(hours=h) - _SF_REF).total_seconds() // 3600)
        ph = it * 0.1
        u[h] = 0.3 * np.sin(np.radians((lon2d - 10.0) * 15.0) + ph) + 0.15
        v[h] = 0.2 * np.cos(np.radians((lat2d + 35.0) * 15.0) - ph)
    u[:, 8:13, 20:25] = np.nan  # a fixed coastal patch every seed can drift into
    v[:, 8:13, 20:25] = np.nan
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), u.astype(np.float32)),
            "vo": (("time", "latitude", "longitude"), v.astype(np.float32)),
        },
        coords={"time": times, "latitude": _SF_LATS, "longitude": _SF_LONS},
    )


def _build_sf_store(tmp_path, first: date, last: date):
    tmin = _utc(first.year, first.month, first.day)
    now = _utc(last.year, last.month, last.day) + timedelta(days=5)  # well past final
    return _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=now,
        fetch_day=_varying_day_dataset,
        time_range=lambda: (tmin, _utc(last.year, last.month, last.day)),
    )


def _ocean_seeds(field: _forecast._Field, n: int, seed: int):
    """``(lon0, lat0)`` arrays of ``n`` seeds in cells free of the land patch at
    the field's middle time step — picked off the in-RAM comparator field, then
    reused identically for every field/config under comparison."""
    mid = field.u.shape[0] // 2
    ocean = np.isfinite(field.u[mid])
    cell_ok = ocean[:-1, :-1] & ocean[:-1, 1:] & ocean[1:, :-1] & ocean[1:, 1:]
    idx = np.argwhere(cell_ok)
    rng = np.random.default_rng(seed)
    lon0 = np.empty(n)
    lat0 = np.empty(n)
    for j in range(n):
        iy, ix = idx[rng.integers(len(idx))]
        fx, fy = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
        lon0[j] = field.lons[ix] + fx * (field.lons[ix + 1] - field.lons[ix])
        lat0[j] = field.lats[iy] + fy * (field.lats[iy + 1] - field.lats[iy])
    return lon0, lat0


@pytest.mark.parametrize("direction", [1, -1])
def test_store_field_matches_in_ram_field_bit_identical(tmp_path, direction):
    """StoreField and the in-RAM _Field, built from the exact same day files,
    must advect a whole batch to the last bit — not just approximately. This is
    what lets the interactive deployment engine swap the in-RAM field for the
    streaming one without changing the physics."""
    first, last = date(2026, 7, 1), date(2026, 7, 3)
    _build_sf_store(tmp_path, first, last)

    t_lo = _utc(2026, 7, 1)
    t_hi = _utc(2026, 7, 3, 23)
    in_ram = _forecast._Field(_field_store.load_window(tmp_path, t0=t_lo, t1=t_hi))
    store_field = _field_store.StoreField(tmp_path, t_lo, t_hi)

    lon0, lat0 = _ocean_seeds(in_ram, 30, seed=3)
    mid_t = float(in_ram.times[len(in_ram.times) // 2])
    t0 = mid_t + 37.0 * np.arange(len(lon0))  # staggered starts
    n_steps = np.full(lon0.shape, round(30 * 60.0 / _forecast.STEP_MIN), dtype=int)

    p_ram, c_ram = _forecast._batch_advect(in_ram, lon0, lat0, t0, n_steps, direction=direction)
    p_store, c_store = _forecast._batch_advect(
        store_field, lon0, lat0, t0, n_steps, direction=direction
    )

    assert np.array_equal(c_ram, c_store)
    assert np.array_equal(p_ram, p_store)


def test_store_field_lru_eviction_does_not_change_results(tmp_path):
    """A run spanning more calendar days than the day cache holds must produce
    the identical trajectory whether the cache evicts along the way or holds
    every day at once — the cap is a memory bound, not a correctness knob.
    cap=2 is the smallest legal cap (the bracketing pair must stay
    co-resident; see _DayArrayCache), so a 6-day run at cap=2 exercises real
    evictions without the cap-1 reopen pathology."""
    first, last = date(2026, 7, 1), date(2026, 7, 6)  # 6 days
    _build_sf_store(tmp_path, first, last)

    t_lo = _utc(2026, 7, 1)
    t_hi = _utc(2026, 7, 6, 23)
    in_ram = _forecast._Field(_field_store.load_window(tmp_path, t0=t_lo, t1=t_hi))
    lon0, lat0 = _ocean_seeds(in_ram, 15, seed=5)
    t0 = np.full(lon0.shape, float(in_ram.times[0]))
    n_steps = np.full(lon0.shape, round(120 * 60.0 / _forecast.STEP_MIN), dtype=int)  # 5-day run

    field_cap2 = _field_store.StoreField(tmp_path, t_lo, t_hi, day_cache_cap=2)
    field_capN = _field_store.StoreField(tmp_path, t_lo, t_hi, day_cache_cap=6)

    p2, c2 = _forecast._batch_advect(field_cap2, lon0, lat0, t0, n_steps)
    pN, cN = _forecast._batch_advect(field_capN, lon0, lat0, t0, n_steps)

    assert np.array_equal(c2, cN)
    assert np.array_equal(p2, pN)

    with pytest.raises(ValueError, match="cap must be >= 2"):
        _field_store.StoreField(tmp_path, t_lo, t_hi, day_cache_cap=1)


def test_store_array_rejects_unsupported_index_shapes(tmp_path):
    """FC-2: _StoreArray implements exactly the two access patterns _forecast uses — a
    scalar time index (a 2-D plane) and a 3-tuple corner gather — and rejects any other
    index shape loudly at the boundary rather than mis-dispatching deep in a batch run."""
    _build_sf_store(tmp_path, date(2026, 7, 1), date(2026, 7, 2))
    field = _field_store.StoreField(tmp_path, _utc(2026, 7, 1), _utc(2026, 7, 2, 23))
    assert field.u[0].ndim == 2  # the scalar-time plane pattern still works
    with pytest.raises(TypeError, match="scalar time index or a 3-tuple"):
        field.u[0:2]
    with pytest.raises(TypeError, match="3-tuple gather"):
        field.u[(0, 1)]


def test_field_unavailable_error_is_a_value_error_subclass():
    """FieldUnavailableError must subclass ValueError so every existing `except
    ValueError` caller keeps working, while the API can catch it specifically for a 503
    (SEC-2/SEC-7)."""
    assert issubclass(_field_store.FieldUnavailableError, ValueError)


def test_load_window_on_an_empty_store_raises_field_unavailable(tmp_path):
    """An empty store (no day files) is a store-state condition, so `load_window` raises
    FieldUnavailableError — which the forecast/parcels endpoints map to a 503, not a 422."""
    with pytest.raises(_field_store.FieldUnavailableError):
        _field_store.load_window(
            tmp_path,
            t0=datetime(2026, 7, 1, tzinfo=timezone.utc),
            t1=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )


def test_store_field_wide_start_spread_stays_within_a_small_day_cache(tmp_path, monkeypatch):
    """SEC-1 (wall-clock resync): a batch whose still-active seeds start on
    far-apart calendar days (here one seed per day across an 8-day span) must NOT
    force that many day files resident. Because ``_batch_advect`` releases seeds
    onto a shared wall clock, at any instant only the seeds on the current day are
    stepped, so the day cache sweeps the store monotonically and opens each day only
    a small bounded number of times — even at the *bare default* (small) cap. Before
    the resync this exact placement pinned all 8 days at once and thrashed any cap
    below the spread (it needed a cache sized to the spread by the now-removed
    ``day_cache_cap_for_starts`` — see git history). The trajectory still matches the
    in-RAM comparator field bit-for-bit, so the cap is a pure memory bound.

    A common-end horizon keeps all eight seeds alive across the whole sweep (the
    realistic shape: the API stops every seed at a shared wall-clock end), so the run
    genuinely walks all 8 calendar days, not a single-day corner."""
    first, last = date(2026, 7, 1), date(2026, 7, 8)  # 8 distinct days
    _build_sf_store(tmp_path, first, last)

    t_lo = _utc(2026, 7, 1)
    t_hi = _utc(2026, 7, 8, 23)
    in_ram = _forecast._Field(_field_store.load_window(tmp_path, t0=t_lo, t1=t_hi))

    lon0, lat0 = _ocean_seeds(in_ram, 8, seed=7)
    # One seed start per calendar day, spanning the whole 8-day store, all running to
    # a shared common end near the store's far edge (as the API's run semantics do).
    t0 = np.array([_utc(2026, 7, i).timestamp() for i in range(1, 9)])
    common_end = _utc(2026, 7, 8, 22).timestamp()
    n_steps = np.round((common_end - t0) / (_forecast.STEP_MIN * 60.0)).astype(int)

    open_calls = {"n": 0}
    real_open_dataset = xr.open_dataset

    def counting_open_dataset(*args, **kwargs):
        open_calls["n"] += 1
        return real_open_dataset(*args, **kwargs)

    monkeypatch.setattr(_field_store.xr, "open_dataset", counting_open_dataset)
    field = _field_store.StoreField(tmp_path, t_lo, t_hi)  # bare _DEFAULT_DAY_CACHE_CAP
    p, c = _forecast._batch_advect(field, lon0, lat0, t0, n_steps)
    opens = open_calls["n"]
    monkeypatch.setattr(_field_store.xr, "open_dataset", real_open_dataset)

    in_ram_p, in_ram_c = _forecast._batch_advect(in_ram, lon0, lat0, t0, n_steps)
    assert np.array_equal(c, in_ram_c)
    assert np.array_equal(p, in_ram_p)

    # No thrash: the constructor opens each of the 8 day files once for the time
    # coordinate, and the monotone sweep reopens each day only a few times as its
    # cursor crosses day boundaries — a small multiple of the day count, nowhere near
    # the per-step reopen storm the pre-resync lockstep produced at this small a cap
    # (which ran into the thousands over even a couple of steps).
    assert opens <= 4 * 8


def test_store_field_raises_on_gap_in_span(tmp_path):
    """A missing interior day (the manifest/backfill has a hole spanning the
    requested window) must raise, exactly as load_window does for the same
    span — StoreField must not silently skip the gap."""
    missing_day = date(2026, 7, 3)

    def fetch_day(day):
        if day == missing_day:
            raise RuntimeError("simulated gap")
        return _varying_day_dataset(day)

    tmin, available_max = _utc(2026, 7, 1), _utc(2026, 7, 5)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 10),
        fetch_day=fetch_day,
        time_range=lambda: (tmin, available_max),
    )

    with pytest.raises(ValueError, match="gap"):
        _field_store.StoreField(tmp_path, _utc(2026, 7, 1), _utc(2026, 7, 5, 23))


def test_store_field_raises_when_span_reaches_outside_coverage(tmp_path):
    tmin = available_max = _utc(2026, 7, 1)
    _field_store.update_store(
        tmp_path,
        tmin=tmin,
        now=_utc(2026, 7, 10),
        fetch_day=_varying_day_dataset,
        time_range=lambda: (tmin, available_max),
    )
    with pytest.raises(ValueError):
        _field_store.StoreField(tmp_path, _utc(2026, 7, 5), _utc(2026, 7, 6))
