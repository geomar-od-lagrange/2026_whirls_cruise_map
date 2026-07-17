"""Tests for the v2 deployment-forecast API (``_api``): direction-aware batch
runs advected straight off the incremental per-day field store, the combined
seeds x hours request budget, the v2 request/response/limits shapes, and the
manifest-mtime-triggered reload.

Synthetic and network-free throughout: a small per-day store is built in
``tmp_path`` via ``_field_store.update_store`` (the same injected
``fetch_day``/``time_range`` pattern ``test_field_store.py`` uses), and the API
is pointed at it via the ``WHIRLS_FIELD_CACHE`` env var — the resolution
``_api._resolve_store_dir`` reads fresh on every call, so no module-reload
dance is needed. The store spans a fixed, wholly-synthetic 2026 date range, so
its field index — the maximal contiguous on-disk day run "containing today" —
always falls back to "the run closest to today" (there being only one run to
choose from); this is deliberate and keeps every test's field span
independent of the real wall-clock date the suite happens to run on.

The RK4 engine's own bit-identity guards (vectorized == scalar, forward and
backward, over a field with land) live in ``test_forecast.py`` and
``test_field_store.py`` (``StoreField`` == in-RAM ``_Field``); this file pins
the API's *bookkeeping* on top of that already-guarded engine: anchor/common-
end/clipping arithmetic, skip accounting, and the v2 wire shapes.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whirls_cruise_map import _api, _field_store, _forecast

# --- synthetic store builder ---------------------------------------------------

_LATS = -35.0 + 0.25 * np.arange(12)  # ~ -35 .. -32.25
_LONS = 10.0 + 0.25 * np.arange(24)   # ~ 10 .. 15.75, room for days of eastward drift
U_EAST = 0.5  # m/s, so RK4 is exact (no truncation, no inertial loop) and the
              # drift direction (east forward, west backward) is unambiguous


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _constant_day(day: date, u_east: float = U_EAST) -> xr.Dataset:
    """24 hourly steps of a land-free, constant eastward current for `day`."""
    start = _utc(day.year, day.month, day.day)
    times = np.array(
        [np.datetime64((start + timedelta(hours=h)).replace(tzinfo=None), "ns") for h in range(24)]
    )
    shape = (24, _LATS.size, _LONS.size)
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), np.full(shape, u_east)),
            "vo": (("time", "latitude", "longitude"), np.zeros(shape)),
        },
        coords={"time": times, "latitude": _LATS, "longitude": _LONS},
    )


def _build_store(store_dir, first: date, last: date, fetch_day=None) -> dict:
    """Write a real per-day store (via ``update_store``) covering ``[first,
    last]`` inclusive, comfortably behind ``FINAL_MARGIN_H`` so every day is
    ``final`` (no test here cares about the non-final/backfill-in-progress
    case — that is ``test_field_store.py``'s territory)."""
    tmin = _utc(first.year, first.month, first.day)
    available_max = _utc(last.year, last.month, last.day)
    now = available_max + timedelta(days=20)
    return _field_store.update_store(
        store_dir,
        tmin=tmin,
        now=now,
        fetch_day=fetch_day or _constant_day,
        time_range=lambda: (tmin, available_max),
    )


@pytest.fixture(autouse=True)
def _fresh_field_index():
    """The API's field-index cache and forecast response cache are module globals —
    reset both around every test (via the one `_reset_caches` entrypoint, API-2) so one
    test's store or cached run can never leak into the next's."""
    _api._reset_caches()
    yield
    _api._reset_caches()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A constant-eastward-current store spanning 2026-06-28 .. 2026-07-10,
    pointed at by WHIRLS_FIELD_CACHE."""
    _build_store(tmp_path, date(2026, 6, 28), date(2026, 7, 10))
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    return tmp_path


_STORE_LO = "2026-06-28T00:00:00Z"
_STORE_HI = "2026-07-10T23:00:00Z"  # last day's last hourly step


# --- request model validation --------------------------------------------------

_ONE_SEED = [{"lon": 10.5, "lat": -34.0, "start": "2026-07-03T00:00:00Z"}]


def test_forecast_request_accepts_a_normal_deployment():
    req = _api.ForecastRequest(seeds=_ONE_SEED)
    assert req.horizon_h == _api._DEFAULT_HORIZON_H
    assert req.direction == "forward"
    assert len(req.seeds) == 1


def test_forecast_request_accepts_backward_direction():
    req = _api.ForecastRequest(seeds=_ONE_SEED, direction="backward")
    assert req.direction == "backward"


def test_forecast_request_accepts_up_to_the_seed_cap():
    _api.ForecastRequest(seeds=_ONE_SEED * _api._MAX_SEEDS)  # no raise at the cap
    with pytest.raises(ValidationError):
        _api.ForecastRequest(seeds=_ONE_SEED * (_api._MAX_SEEDS + 1))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon_h": 0.0},                      # gt=0
        {"horizon_h": 2401.0},                   # le=2400
        {"horizon_h": float("inf")},
        {"horizon_h": float("nan")},
        {"direction": "sideways"},                # not a member of the Literal
        {"seeds": _ONE_SEED * (_api._MAX_SEEDS + 1)},  # one past the seed cap
        {"foo": 1},                               # extra="forbid"
        # The combined seeds x hours budget bites even when neither knob alone
        # would: 500 seeds and a 2400 h horizon each pass their own bound, but
        # together (1.2M seed-hours) exceed _MAX_SEED_HOURS.
        {"seeds": _ONE_SEED * 500, "horizon_h": 2400.0},
    ],
)
def test_forecast_request_rejects_resource_exhaustion_and_bad_inputs(kwargs):
    kwargs.setdefault("seeds", _ONE_SEED)
    with pytest.raises(ValidationError):
        _api.ForecastRequest(**kwargs)


@pytest.mark.parametrize(
    "seed",
    [
        {"lon": 181.0, "lat": -34.0, "start": "2026-07-03T00:00:00Z"},    # lon > 180
        {"lon": -181.0, "lat": -34.0, "start": "2026-07-03T00:00:00Z"},   # lon < -180
        {"lon": 10.5, "lat": 91.0, "start": "2026-07-03T00:00:00Z"},      # lat > 90
        {"lon": 10.5, "lat": -91.0, "start": "2026-07-03T00:00:00Z"},     # lat < -90
        {"lon": float("nan"), "lat": -34.0, "start": "2026-07-03T00:00:00Z"},
        {"lon": 10.5, "lat": float("inf"), "start": "2026-07-03T00:00:00Z"},
    ],
)
def test_seed_rejects_out_of_range_or_non_finite_coords(seed):
    """SEC-5: an unauthenticated endpoint must not accept a coordinate that could only
    be malformed or an attack — out-of-range lon/lat and inf/nan are 422 up front, not
    silently sampled off-field."""
    with pytest.raises(ValidationError):
        _api.ForecastRequest(seeds=[seed])


def test_seed_accepts_the_coordinate_extremes():
    _api.ForecastRequest(seeds=[{"lon": 180.0, "lat": 90.0, "start": "2026-07-03T00:00:00Z"}])
    _api.ForecastRequest(seeds=[{"lon": -180.0, "lat": -90.0, "start": "2026-07-03T00:00:00Z"}])


def test_forecast_request_budget_accepts_right_at_the_limit():
    n = 500
    horizon_h = _api._MAX_SEED_HOURS / n  # 2000 h, within the horizon_h le=2400 bound
    req = _api.ForecastRequest(seeds=_ONE_SEED * n, horizon_h=horizon_h)
    assert len(req.seeds) * req.horizon_h == pytest.approx(_api._MAX_SEED_HOURS)


# --- run semantics: forward ------------------------------------------------


def test_forward_run_v2_response_shape(store):
    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-03T00:00:00Z"),
        _api.Seed(lon=11.0, lat=-33.5, start="2026-07-03T02:00:00Z"),  # later drop
    ]
    out = _api._batch_run(seeds, horizon_h=48.0, direction="forward")

    props = out["properties"]
    assert props["run_start"] == "2026-07-03T00:00:00Z"  # earliest start = anchor
    assert props["direction"] == "forward"
    assert props["horizon_h"] == 48.0
    assert props["n_seeds"] == 2
    assert props["tracks"] == 2
    assert props["skipped"] == 0
    assert props["cadence_s"] == 15 * 60.0  # 48 h stays at the base 15-min cadence
    assert "analysis_edge" in props and props["analysis_edge"].endswith("Z")
    # window reports THIS RUN's actual loaded span (anchor .. anchor+horizon_h),
    # not the store's whole reach — see test_limits_v2_shape for that.
    assert props["window"] == ["2026-07-03T00:00:00Z", "2026-07-05T00:00:00Z"]

    a, b = out["features"]
    assert a["properties"] == {
        "role": "track", "index": 0, "start": "2026-07-03T00:00:00Z",
        "cadence_s": 900.0, "direction": "forward",
    }
    assert b["properties"]["start"] == "2026-07-03T02:00:00Z"
    # The later drop enters 2 h after the anchor, so its remaining budget (46 h)
    # yields a shorter track than the full-horizon drop.
    assert len(b["geometry"]["coordinates"]) < len(a["geometry"]["coordinates"])
    # Eastward current: every vertex after the head drifts east (lon increases).
    assert a["geometry"]["coordinates"][1][0] > a["geometry"]["coordinates"][0][0]


def test_later_forward_drop_with_no_track_left_is_skipped(store):
    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-03T00:00:00Z"),
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-05T00:00:00Z"),  # +48 h == horizon
    ]
    out = _api._batch_run(seeds, horizon_h=48.0, direction="forward")
    assert out["properties"]["tracks"] == 1
    assert out["properties"]["skipped"] == 1
    assert out["properties"]["n_seeds"] == 2


# --- run semantics: backward -------------------------------------------------


def test_backward_run_anchors_on_the_latest_start_and_drifts_backward(store):
    seeds = [
        _api.Seed(lon=13.0, lat=-34.0, start="2026-07-03T00:00:00Z"),  # earlier start
        _api.Seed(lon=13.0, lat=-34.0, start="2026-07-03T02:00:00Z"),  # anchor (latest)
    ]
    out = _api._batch_run(seeds, horizon_h=48.0, direction="backward")
    props = out["properties"]
    assert props["run_start"] == "2026-07-03T02:00:00Z"  # latest start = anchor
    assert props["direction"] == "backward"

    a, b = out["features"]
    assert a["properties"]["direction"] == "backward"
    # A: entered 2 h before the anchor, so only horizon_h - 2 h of backward budget
    # remains -> a shorter track than B (the anchor itself, full horizon_h).
    assert len(a["geometry"]["coordinates"]) < len(b["geometry"]["coordinates"])
    # Backward under a steady eastward current: the track runs west of the head
    # (the mirror image of the forward case), since each backward step subtracts
    # the eastward displacement.
    for f in (a, b):
        coords = f["geometry"]["coordinates"]
        assert coords[1][0] < coords[0][0]


def test_backward_drop_that_already_predates_the_common_end_is_skipped(store):
    seeds = [
        _api.Seed(lon=13.0, lat=-34.0, start="2026-07-05T00:00:00Z"),  # anchor
        _api.Seed(lon=13.0, lat=-34.0, start="2026-07-03T00:00:00Z"),  # 48 h earlier == horizon
    ]
    out = _api._batch_run(seeds, horizon_h=48.0, direction="backward")
    assert out["properties"]["tracks"] == 1
    assert out["properties"]["skipped"] == 1


# --- out-of-window seeds + clipping to the field's actual reach --------------


def test_out_of_window_seed_is_skipped_and_counted(store):
    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-03T00:00:00Z"),  # in window
        _api.Seed(lon=10.5, lat=-34.0, start="2099-01-01T00:00:00Z"),  # far outside
    ]
    out = _api._batch_run(seeds, horizon_h=24.0, direction="forward")
    assert out["properties"]["tracks"] == 1
    assert out["properties"]["skipped"] == 1
    assert out["properties"]["n_seeds"] == 2


def test_run_window_clips_to_the_store_reach_when_horizon_overshoots_it(store):
    """A run started near the store's forward edge with a horizon that would
    reach past it must have its ``window`` reported as the clipped span
    actually loaded (not the full requested span), and the track truncates at
    the field edge rather than erroring."""
    seeds = [_api.Seed(lon=10.5, lat=-34.0, start="2026-07-10T00:00:00Z")]
    out = _api._batch_run(seeds, horizon_h=48.0, direction="forward")  # would reach 07-12
    assert out["properties"]["window"] == ["2026-07-10T00:00:00Z", _STORE_HI]
    assert out["properties"]["tracks"] == 1


def test_run_entirely_outside_the_store_reach_skips_everything(store):
    seeds = [_api.Seed(lon=10.5, lat=-34.0, start="2030-01-01T00:00:00Z")]
    out = _api._batch_run(seeds, horizon_h=24.0, direction="forward")
    assert out["properties"]["tracks"] == 0
    assert out["properties"]["skipped"] == 1
    # No run-local window exists (nothing overlapped) -> falls back to the
    # store's whole available span.
    assert out["properties"]["window"] == [_STORE_LO, _STORE_HI]
    assert out["features"] == []


# --- seed-start spread: unbounded now that residency tracks the horizon window ---


def test_wide_seed_start_spread_runs_normally(store):
    """Since `_batch_advect` resyncs seeds to a shared wall clock, a batch whose drops
    start on far-apart calendar days no longer pins that many days resident and needs no
    spread cap (SEC-1) — even a 10-day spread runs like any other. Both seeds stay alive
    (long horizon), so the whole 10-day span is exercised, not skipped. The memory
    property itself (a wide spread stays within a small day cache) is pinned in
    ``test_field_store.test_store_field_wide_start_spread_stays_within_a_small_day_cache``."""
    wide = [
        _api.Seed(lon=10.5, lat=-34.0, start="2026-06-29T00:00:00Z"),
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-09T00:00:00Z"),  # 10-day spread
    ]
    out = _api._batch_run(wide, horizon_h=500.0, direction="forward")
    assert out["properties"]["tracks"] == 2
    assert out["properties"]["skipped"] == 0


def test_out_of_window_stragglers_are_skipped_not_advected(store):
    """A far-future straggler outside the store's loaded span is skipped-and-counted
    (never advected), while the in-window seeds run normally — independent of any spread
    consideration now that residency tracks the horizon window, not the spread."""
    seeds = [
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-01T00:00:00Z"),
        _api.Seed(lon=10.5, lat=-34.0, start="2026-07-03T00:00:00Z"),
        _api.Seed(lon=10.5, lat=-34.0, start="2099-01-01T00:00:00Z"),  # far outside, skipped
    ]
    out = _api._batch_run(seeds, horizon_h=72.0, direction="forward")
    assert out["properties"]["tracks"] == 2
    assert out["properties"]["skipped"] == 1


def test_no_seeds_raises_value_error(store):
    with pytest.raises(ValueError, match="no seeds"):
        _api._batch_run([], horizon_h=24.0, direction="forward")


def test_run_semaphore_bounds_concurrent_advection(store, monkeypatch):
    """SEC-1: at most ``_MAX_CONCURRENCY`` runs may hold a field resident at once, so
    concurrent memory-heavy requests can't stack past the pod limit. Deterministic (no
    sleeps): each advection blocks on an Event inside the semaphore while a counter
    records peak concurrency; once more threads than the cap are launched, the observed
    peak must equal — never exceed — the cap."""
    import threading

    cap = _api._MAX_CONCURRENCY
    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}
    entered = threading.Semaphore(0)  # counts how many threads reached the gated region
    release = threading.Event()
    real_advect = _forecast._batch_advect

    def gated(*args, **kwargs):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        entered.release()
        assert release.wait(timeout=10), "release never signalled"
        try:
            return real_advect(*args, **kwargs)
        finally:
            with lock:
                state["cur"] -= 1

    monkeypatch.setattr(_forecast, "_batch_advect", gated)

    seeds = [_api.Seed(lon=10.5, lat=-34.0, start="2026-07-03T00:00:00Z")]
    threads = [
        threading.Thread(target=_api._batch_run, args=(seeds, 24.0, "forward"), daemon=True)
        for _ in range(cap + 2)
    ]
    for t in threads:
        t.start()
    # Wait until exactly `cap` threads have entered the gated region; the extra two must
    # be blocked on the semaphore, unable to enter, so no further `entered` release comes.
    for _ in range(cap):
        assert entered.acquire(timeout=10), "a run failed to enter under the semaphore"
    assert not entered.acquire(timeout=0.5), "more than the cap entered concurrently"
    assert state["peak"] == cap

    release.set()  # let them all finish
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()


# --- limits v2 -----------------------------------------------------------------


def test_limits_v2_shape(store):
    out = _api.limits()
    assert out["max_seeds"] == _api._MAX_SEEDS
    assert out["max_seed_hours"] == _api._MAX_SEED_HOURS
    assert out["window"] == [_STORE_LO, _STORE_HI]
    assert out["analysis_edge"].endswith("Z")


def test_field_index_narrows_at_a_present_but_partial_mid_run_day(tmp_path):
    """A day file that exists on disk but holds fewer than its 24 hourly steps
    (present, not missing) must still break the field index's contiguous run at
    its true edge — a day-presence-only check would miss this and let a later
    ``StoreField`` build fail on an internal gap instead of the index correctly
    reporting a narrower reach up front. ``now`` is passed explicitly (rather
    than relying on the real wall clock) so the "run closest to now" tie-break
    deterministically picks the earlier of the two runs either side of the gap."""
    gappy_day = date(2026, 7, 2)

    def fetch_day(day):
        ds = _constant_day(day)
        return ds.isel(time=slice(0, 6)) if day == gappy_day else ds  # only 6/24 hours

    manifest = _build_store(tmp_path, date(2026, 6, 28), date(2026, 7, 5), fetch_day=fetch_day)
    lo, hi = _api._build_field_index(tmp_path, manifest, now=_utc(2026, 6, 29))
    # The reach must stop at the gappy day's actual last step, never claim the
    # full 24 h that day never had.
    assert lo == _utc(2026, 6, 28, 0)
    assert hi == _utc(2026, 7, 2, 5)


# --- manifest-mtime reload -------------------------------------------------------


def test_manifest_mtime_change_triggers_a_reload_of_the_field_index(tmp_path, monkeypatch):
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    _build_store(tmp_path, date(2026, 6, 28), date(2026, 7, 1))

    (lo1, hi1), _ = _api._get_field_index()
    assert hi1 == _utc(2026, 7, 1, 23)
    assert _api._get_field_index()[0] is _api._index  # span cached, no rebuild yet

    # A later build run extends the store's forward reach. Force a detectable
    # mtime bump (successive writes can otherwise land in the same filesystem
    # second), mirroring the v1 sampler-reload test's approach.
    _build_store(tmp_path, date(2026, 6, 28), date(2026, 7, 5))
    manifest_path = tmp_path / "field_manifest.json"
    bumped = manifest_path.stat().st_mtime + 10
    os.utime(manifest_path, (bumped, bumped))

    (lo2, hi2), _ = _api._get_field_index()
    assert lo2 == lo1  # unchanged low edge
    assert hi2 == _utc(2026, 7, 5, 23)  # the new span is visible with no restart


# --- 503 on a missing/empty store ------------------------------------------------


def test_get_field_index_raises_when_the_store_has_no_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))  # empty dir, no manifest
    with pytest.raises(FileNotFoundError):
        _api._get_field_index()


def test_forecast_endpoint_503s_with_a_fixed_message_that_does_not_leak_the_store_path(
    tmp_path, monkeypatch
):
    """SEC-2/SEC-7: an empty store 503s with a *fixed* detail — never the exception's
    text, which names the absolute store dir (WHIRLS_FIELD_CACHE / PVC path)."""
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    client = TestClient(_api.app)
    resp = client.post("/api/forecast", json={"seeds": _ONE_SEED})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail == _api._FIELD_UNAVAILABLE_DETAIL
    assert str(tmp_path) not in detail  # no filesystem path in the public body


def test_limits_endpoint_503s_with_a_fixed_message(tmp_path, monkeypatch):
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    client = TestClient(_api.app)
    resp = client.get("/api/forecast/limits")
    assert resp.status_code == 503
    assert resp.json()["detail"] == _api._FIELD_UNAVAILABLE_DETAIL
    assert str(tmp_path) not in resp.json()["detail"]


def test_forecast_503s_with_a_fixed_message_on_a_corrupt_manifest(store):
    """SEC-2/SEC-7: a truncated/corrupt manifest is a store-state fault (a JSONDecodeError,
    which is a ValueError subclass) — it must map to a fixed 503, not a 422 or an
    internals-leaking 500. `_load_manifest` raises FieldUnavailableError, caught before the
    422 branch."""
    (store / "field_manifest.json").write_text("{ this is not valid json ")
    client = TestClient(_api.app)
    resp = client.post("/api/forecast", json={"seeds": _ONE_SEED, "horizon_h": 48.0})
    assert resp.status_code == 503
    assert resp.json()["detail"] == _api._FIELD_UNAVAILABLE_DETAIL


def test_limits_503s_on_a_corrupt_manifest(store):
    """Same store-state fault must be a 503 on `limits()` too (which previously would have
    500'd on the uncaught JSONDecodeError) — one consistent status for one fault."""
    (store / "field_manifest.json").write_text("nonsense")
    client = TestClient(_api.app)
    resp = client.get("/api/forecast/limits")
    assert resp.status_code == 503
    assert resp.json()["detail"] == _api._FIELD_UNAVAILABLE_DETAIL


def test_413_carries_cors_headers_for_a_cross_origin_caller(store):
    """The body-size 413 sits inside CORSMiddleware, so a cross-origin caller (the
    two-port dev flow) sees the real 413 with CORS headers, not an opaque CORS error."""
    client = TestClient(_api.app)
    oversized = '{"seeds": [], "_pad": "' + "x" * (_api._MAX_BODY_BYTES + 1) + '"}'
    resp = client.post(
        "/api/forecast",
        content=oversized,
        headers={"Content-Type": "application/json", "Origin": "http://localhost:8000"},
    )
    assert resp.status_code == 413
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_a_real_bug_surfaces_as_500_not_a_masked_503(store, monkeypatch):
    """SEC-2: the catch-all → 503 masked genuine 500-class defects. A non-"field
    missing" exception in the run must now surface as a real 500 (logged server-side),
    not a transient "field unavailable" 503."""
    def boom(*args, **kwargs):
        raise RuntimeError("a genuine bug in the advection path")

    monkeypatch.setattr(_api, "_batch_run", boom)
    client = TestClient(_api.app, raise_server_exceptions=False)
    resp = client.post("/api/forecast", json={"seeds": _ONE_SEED, "horizon_h": 48.0})
    assert resp.status_code == 500


def test_oversized_request_body_is_413ed_before_parsing(tmp_path, monkeypatch):
    """SEC-4: a body over `_MAX_BODY_BYTES` is rejected with 413 before Starlette
    buffers and JSON-parses it — the app no longer relies on the out-of-repo nginx cap.
    The store is empty here on purpose: a 413 must fire before any field access, so the
    guard, not a 503/422, is what the client sees."""
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    client = TestClient(_api.app)
    # A syntactically-valid but oversized JSON body: pad with a huge ignored string so
    # the raw bytes exceed the cap well before the seed cap or `extra=forbid` would bite.
    oversized = '{"seeds": [], "_pad": "' + "x" * (_api._MAX_BODY_BYTES + 1) + '"}'
    resp = client.post(
        "/api/forecast",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_body_at_the_limit_is_not_413ed(store):
    """A normal-sized body passes the guard untouched (the 413 only fires past the cap)."""
    client = TestClient(_api.app)
    resp = client.post("/api/forecast", json={"seeds": _ONE_SEED, "horizon_h": 48.0})
    assert resp.status_code == 200


def test_oversized_chunked_body_without_content_length_is_413ed(tmp_path, monkeypatch):
    """SEC-4: a chunked body with no declared Content-Length can't be caught by the
    up-front header check, so the middleware's streamed-byte tally must catch it and
    still emit a clean 413 (the 413 is sent by the middleware, which sits inside
    Starlette's ServerErrorMiddleware, so it wins the response before any 500 is sent)."""
    monkeypatch.setenv("WHIRLS_FIELD_CACHE", str(tmp_path))
    client = TestClient(_api.app)

    def chunks():
        yield b'{"seeds": [], "_pad": "'
        sent = 0
        while sent <= _api._MAX_BODY_BYTES:
            block = b"x" * 64_000
            yield block
            sent += len(block)
        yield b'"}'

    resp = client.post(
        "/api/forecast", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413


# --- gzip on the wire ---------------------------------------------------------
#
# The at-sea link pays per byte, so the app compresses its own responses (in-app
# GZipMiddleware rather than gateway-side — every deployment shape gets it, and
# the dev flow measures what production ships). These pin the contract: a
# gzip-accepting client gets a gzip-encoded body that decodes to exactly the
# identity-encoding payload, and tiny responses (the limits probe) skip the
# codec overhead via minimum_size.


def test_forecast_response_is_gzipped_and_decodes_to_the_identity_payload(store):
    client = TestClient(_api.app)
    body = {"seeds": _ONE_SEED, "horizon_h": 48.0}  # a few kB of JSON, > minimum_size

    plain = client.post(
        "/api/forecast", json=body, headers={"Accept-Encoding": "identity"}
    )
    gz = client.post("/api/forecast", json=body, headers={"Accept-Encoding": "gzip"})

    assert plain.status_code == 200 and gz.status_code == 200
    assert "content-encoding" not in plain.headers
    assert gz.headers.get("content-encoding") == "gzip"
    # The client (httpx) decodes the gzip body transparently; the decoded payload
    # must equal the identity-encoding one field-for-field. Compared as parsed
    # JSON (not raw bytes): the two are separate requests, and `analysis_edge` is
    # wall-clock-`now`-at-response-time, so a second boundary crossed between the
    # two calls would otherwise make this test flaky on nothing gzip-related.
    plain_json, gz_json = plain.json(), gz.json()
    assert plain_json["properties"].pop("analysis_edge").endswith("Z")
    assert gz_json["properties"].pop("analysis_edge").endswith("Z")
    assert gz_json == plain_json


def test_limits_response_stays_uncompressed_below_minimum_size(store):
    client = TestClient(_api.app)
    resp = client.get("/api/forecast/limits", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.json() == {
        "max_seeds": _api._MAX_SEEDS,
        "max_seed_hours": _api._MAX_SEED_HOURS,
        "window": [_STORE_LO, _STORE_HI],
        "analysis_edge": resp.json()["analysis_edge"],
    }


def test_forecast_endpoint_422s_on_an_unparseable_start(store):
    client = TestClient(_api.app)
    resp = client.post(
        "/api/forecast", json={"seeds": [{"lon": 10.5, "lat": -34.0, "start": "not-a-time"}]}
    )
    assert resp.status_code == 422


# --- response cache: the observed forecast every client fires ------------------
#
# The map kicks the same observed-drifter forecast at page load for every client; on the
# single API pod it should compute once per data version, not once per client. These pin
# the memoization: identical requests hit the cache, distinct ones don't, and a field-store
# write invalidates.


def _count_batch_run(monkeypatch):
    """Wrap `_api._batch_run` with a call counter; returns the counter dict."""
    calls = {"n": 0}
    real = _api._batch_run

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(_api, "_batch_run", counting)
    return calls


def test_identical_forecast_is_computed_once_then_served_from_cache(store, monkeypatch):
    calls = _count_batch_run(monkeypatch)
    client = TestClient(_api.app)
    body = {"seeds": _ONE_SEED, "horizon_h": 48.0}

    r1 = client.post("/api/forecast", json=body)
    r2 = client.post("/api/forecast", json=body)

    assert r1.status_code == r2.status_code == 200
    assert calls["n"] == 1  # second request served from cache, no recompute
    assert r1.json()["features"] == r2.json()["features"]


def test_distinct_requests_are_cached_separately(store, monkeypatch):
    calls = _count_batch_run(monkeypatch)
    client = TestClient(_api.app)

    client.post("/api/forecast", json={"seeds": _ONE_SEED, "horizon_h": 48.0})
    client.post("/api/forecast", json={"seeds": _ONE_SEED, "horizon_h": 24.0})   # diff horizon
    client.post("/api/forecast",
                json={"seeds": _ONE_SEED, "horizon_h": 48.0, "direction": "backward"})  # diff dir
    client.post("/api/forecast",
                json={"seeds": _ONE_SEED * 2, "horizon_h": 48.0})  # diff seed set

    assert calls["n"] == 4  # each distinct request is its own cache key


def test_cache_invalidates_when_the_field_store_updates(store, monkeypatch):
    calls = _count_batch_run(monkeypatch)
    client = TestClient(_api.app)
    body = {"seeds": _ONE_SEED, "horizon_h": 48.0}

    client.post("/api/forecast", json=body)
    assert calls["n"] == 1

    # A build write bumps the manifest mtime (the version key). Force a detectable bump
    # (successive writes can land in the same filesystem second), as the reload test does.
    manifest_path = store / "field_manifest.json"
    bumped = manifest_path.stat().st_mtime + 10
    os.utime(manifest_path, (bumped, bumped))

    client.post("/api/forecast", json=body)
    assert calls["n"] == 2  # new field version -> new key -> recompute
