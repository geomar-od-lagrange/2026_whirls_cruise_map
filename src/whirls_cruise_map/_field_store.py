"""Incremental per-day store for the hourly CMEMS current field.

Replaces a single ``forecast_window.nc`` fetched whole on every slow run with
a resumable per-day file store: one ``uv_YYYY-MM-DD.nc`` per UTC day plus a manifest
tracking which days are on disk and which are ``final`` (behind CMEMS's
revision edge, so they never need refetching short of ``--refetch-all``). A
whole-window fetch doesn't fit the slow build's tight deadline once the field
spans the whole cruise (``plans/done/034-deployment-focused-app.md``, workstream
A) — every run would re-pull the entire, ever-growing span. The per-day
layout instead pulls only what's missing or not yet final, and a killed
backfill resumes on the next run rather than starting over, because the
manifest is rewritten atomically after *every* completed day rather than once
at the end — whatever hit disk before a kill is exactly what the next run
sees as already done.

Two entry points:

- :func:`update_store` — the write side a slow build run calls: fetch every
  missing or non-final UTC day in ``[tmin, tmax]`` (``tmax`` from the CMEMS
  catalogue, or a conservative fallback), **newest first** — so even a
  partial run already covers the recent+forecast span the app reads, with
  the deep past filling in over later runs.
- :func:`load_window` — the read side: open the day files spanning an
  arbitrary ``[t0, t1]``, bracket one hourly step outside each end,
  concatenate, and verify hourly continuity — returning a Dataset shaped
  exactly like the legacy whole-window fetch's output (``uo``, ``vo``;
  ``time``/``latitude``/``longitude``) so downstream consumers
  (forecast/hindcast, the inertial decomposition, the forecast API) are
  drop-in.

Both take an explicit ``store_dir`` (default from ``WHIRLS_FIELD_CACHE``, else
a repo-local ``cache/field/``) rather than reading a module-level path, so
tests point at a ``tmp_path`` directly instead of monkeypatching an env var.
``_api.py``'s ``_resolve_store_dir`` just delegates to :func:`_resolve_store_dir`
below for the same resolution.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import copernicusmarine
import numpy as np
import xarray as xr

from . import _currents, _forecast
from ._retry import with_retry

_log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORE_DIR = _REPO_ROOT / "cache" / "field"
_MANIFEST_NAME = "field_manifest.json"

# A day is `final` (never refetched short of --refetch-all) once its whole
# span is this far behind the fetch-time wall clock — the rollover working
# assumption (CMEMS revises nothing behind the current analysis edge; see
# plans/done/034-deployment-focused-app.md) with a safety margin against the edge
# being fuzzier in practice than advertised.
FINAL_MARGIN_H = 12


# --- store path / manifest ---------------------------------------------------

def _resolve_store_dir(store_dir: Path | str | None) -> Path:
    """``store_dir`` if given, else ``WHIRLS_FIELD_CACHE``, else the repo-local
    default. Taken as a parameter on every call rather than a module global
    (both entry points already thread ``store_dir`` through), so a test picks
    a ``tmp_path`` directly instead of monkeypatching the env var."""
    if store_dir is not None:
        return Path(store_dir)
    return Path(os.environ.get("WHIRLS_FIELD_CACHE", str(_DEFAULT_STORE_DIR)))


def _manifest_path(store: Path) -> Path:
    return store / _MANIFEST_NAME


def _day_filename(day: date) -> str:
    return f"uv_{day.isoformat()}.nc"


def _load_manifest(store: Path) -> dict:
    path = _manifest_path(store)
    if not path.exists():
        return {
            "dataset_id": _currents.WINDOW_DATASET_ID,
            "bbox": _currents.BBOX,
            "tmin": None,
            "updated": None,
            "days": {},
        }
    with path.open() as f:
        return json.load(f)


def _write_manifest(store: Path, manifest: dict) -> None:
    """Atomic ``*.tmp`` + :func:`os.replace`, called after every completed day
    (not just once at the end) — the mechanism that makes a killed backfill
    resumable: whatever hit disk before the kill is exactly what the next run
    sees as already done."""
    store.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(store)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_day_file(path: Path, ds: xr.Dataset) -> None:
    """Cast ``uo``/``vo`` to float32 and persist atomically (``*.tmp`` +
    :func:`os.replace`, the same atomic-write convention every build artifact uses),
    so a concurrent :func:`load_window` reader never opens a half-written day
    file."""
    ds = ds.astype({"uo": np.float32, "vo": np.float32})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        ds.to_netcdf(tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _day_is_complete(ds: xr.Dataset, day: date) -> bool:
    """True if ``ds`` carries the day's full 24 hourly steps (``[day 00:00 ..
    day 23:00]``, exactly). A short/partial return (CMEMS gave back fewer
    steps without raising — e.g. a just-published day still filling in) must
    not be eligible for ``final`` regardless of how far behind wall-clock the
    day is, or the gap is locked in forever short of ``--refetch-all``."""
    expected = np.array(
        [
            _to_dt64(datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=h))
            for h in range(24)
        ]
    )
    times = np.asarray(ds["time"].values)
    if times.shape[0] != 24:
        return False
    return bool(np.array_equal(np.sort(times), expected))


def _date_range(start: date, end: date) -> list[date]:
    """Every UTC date from ``start`` through ``end`` inclusive, ascending
    (empty if ``end < start``)."""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _to_utc(when: datetime) -> datetime:
    """Naive-or-aware ``when`` -> tz-aware UTC (naive is taken to already mean
    UTC, the convention ``_currents``/``_api`` share)."""
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _to_dt64(when: datetime) -> np.datetime64:
    return np.datetime64(_to_utc(when).replace(tzinfo=None), "ns")


def _iso(when: datetime) -> str:
    return _to_utc(when).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return _to_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


# --- default field time range (CMEMS catalogue reach) ------------------------

def _describe_time_range(dataset_id: str = _currents.WINDOW_DATASET_ID) -> tuple[datetime, datetime]:
    """The dataset's time-axis reach ``(min, max)`` per the CMEMS catalogue,
    read off the first ``t``-axis coordinate found for ``dataset_id`` (every
    ARCO service/variable shares the same axis, so any one answers the
    reach). Raises on any catalogue-shape surprise or network/auth failure;
    :func:`_default_time_range` is the sole caller and treats that as
    "unavailable", not fatal."""
    catalogue = copernicusmarine.describe(dataset_id=dataset_id)
    for product in catalogue.products:
        for dataset in product.datasets:
            if dataset.dataset_id != dataset_id:
                continue
            for version in dataset.versions:
                for part in version.parts:
                    for service in part.services:
                        for variable in service.variables:
                            for coord in variable.coordinates:
                                if coord.axis == "t" and coord.minimum_value is not None:
                                    lo = datetime.fromtimestamp(
                                        float(coord.minimum_value) / 1000.0, tz=timezone.utc
                                    )
                                    hi = datetime.fromtimestamp(
                                        float(coord.maximum_value) / 1000.0, tz=timezone.utc
                                    )
                                    return lo, hi
    raise RuntimeError(f"no time coordinate in the {dataset_id} catalogue entry")


def _default_time_range(tmin: datetime, now: datetime) -> tuple[datetime, datetime]:
    """:func:`update_store`'s default ``time_range``: the CMEMS catalogue's
    advertised reach for the window product, falling back to ``(tmin, now +
    10 days)`` — a deliberately conservative guess at the forecast edge — on
    any failure (network, auth, an unexpected catalogue shape)."""
    try:
        return _describe_time_range()
    except Exception as exc:
        _log.warning(
            "field store: could not read the %s catalogue reach (%s); "
            "falling back to [tmin, now+10d]",
            _currents.WINDOW_DATASET_ID,
            exc,
        )
        return tmin, now + timedelta(days=10)


# --- fetch one day ------------------------------------------------------------

def _default_fetch_day(day: date) -> xr.Dataset:
    """One exact UTC day ``[00:00, next day 00:00)`` of hourly ``uo``/``vo``
    over ``_currents.BBOX`` — the store's per-day unit. Mirrors
    :func:`_currents.fetch_shading_window`'s ``copernicusmarine.subset`` call
    shape (same dataset/variables/bbox/depth keys, tempdir + retry +
    depth-drop) but day-exact rather than ``outside``-bracketed: consecutive
    day files must not overlap (:func:`load_window` does the cross-file
    bracketing instead), so this requests exactly the day's 24 grid hours."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(hours=23)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "day.nc"
        with_retry(
            lambda: copernicusmarine.subset(
                dataset_id=_currents.WINDOW_DATASET_ID,
                variables=["uo", "vo"],
                minimum_longitude=_currents.BBOX["lon_min"],
                maximum_longitude=_currents.BBOX["lon_max"],
                minimum_latitude=_currents.BBOX["lat_min"],
                maximum_latitude=_currents.BBOX["lat_max"],
                minimum_depth=0.49,
                maximum_depth=0.5,
                start_datetime=start,
                end_datetime=end,
                output_filename=str(out),
                overwrite=True,
            ),
            attempts=_currents._ATTEMPTS,
            backoff=_currents._BACKOFF,
            label=f"CMEMS subset of {_currents.WINDOW_DATASET_ID} for {day.isoformat()}",
        )
        with xr.open_dataset(out) as ds:
            ds = ds.load()
    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds


# --- update_store (write side) -----------------------------------------------

def update_store(
    store_dir: Path | str | None = None,
    *,
    tmin: datetime | None = None,
    now: datetime | None = None,
    refetch_all: bool = False,
    fetch_day: Callable[[date], xr.Dataset] | None = None,
    time_range: Callable[[], tuple[datetime, datetime]] | None = None,
) -> dict:
    """Fetch every missing or non-``final`` UTC day in ``[tmin, tmax]`` into
    the per-day store, newest first, and return the (freshly rewritten)
    manifest.

    ``tmax`` comes from ``time_range()`` (default: the CMEMS catalogue reach
    for ``_currents.WINDOW_DATASET_ID``, degrading to ``(tmin, now + 10 d)`` —
    see :func:`_default_time_range`). A day is fetched when its file is
    missing, the manifest marks it non-``final``, or ``refetch_all`` is set;
    a day becomes ``final`` once ``fetch_day`` actually returned its full 24
    hourly steps (:func:`_day_is_complete`) *and* its span is
    ``FINAL_MARGIN_H`` behind ``now`` at fetch time (the rollover assumption —
    see the module docstring and the plan's full-refetch escape hatch); a
    short/partial return stays non-``final`` regardless of elapsed time, so
    it's retried next run rather than locked in as done. **Newest first**: a
    killed run still leaves the recent+forecast span the app reads already
    covered, with the deep past filling in on later runs.

    A single day's fetch failure is logged and skipped — the day stays
    missing/non-final and is retried next run; the manifest is rewritten
    atomically after every day that *does* complete, so neither a caught
    per-day failure nor an outright killed process loses more than that one
    day's progress.

    ``now``/``fetch_day``/``time_range`` are injectable so tests need no
    network or wall-clock dependency.
    """
    store = _resolve_store_dir(store_dir)
    now = _to_utc(now) if now is not None else datetime.now(timezone.utc)
    tmin = _to_utc(tmin) if tmin is not None else _parse_iso(_currents.FIELD_TMIN)
    fetch_day = fetch_day or _default_fetch_day
    if time_range is None:
        available_min, available_max = _default_time_range(tmin, now)
    else:
        available_min, available_max = time_range()
    available_min, available_max = _to_utc(available_min), _to_utc(available_max)

    manifest = _load_manifest(store)
    days_meta: dict = manifest.setdefault("days", {})

    # Refuse to relabel day files fetched under a different dataset_id/bbox as
    # matching the current config: load_window's xr.concat would silently
    # NaN-pad a grid mismatch (default join="outer") instead of erroring, so
    # the guard has to sit here, before the manifest is overwritten. Only
    # matters once day files are actually on disk (a fresh/empty manifest has
    # nothing to conflict with).
    prev_dataset_id = manifest.get("dataset_id")
    prev_bbox = manifest.get("bbox")
    config_changed = bool(days_meta) and (
        (prev_dataset_id is not None and prev_dataset_id != _currents.WINDOW_DATASET_ID)
        or (prev_bbox is not None and prev_bbox != _currents.BBOX)
    )
    if config_changed:
        if not refetch_all:
            raise RuntimeError(
                f"field store at {store} was built with dataset_id={prev_dataset_id!r} "
                f"bbox={prev_bbox!r}, but the current config is "
                f"dataset_id={_currents.WINDOW_DATASET_ID!r} bbox={_currents.BBOX!r}; "
                "refusing to mix incompatible day files into one window — rerun with "
                "refetch_all=True (--refetch-all) to discard the stale entries and "
                "refetch under the new config"
            )
        # --refetch-all + a config change: the stale entries (however far
        # outside the current [tmin, tmax] they reach) can never be reused, so
        # drop them all rather than just the ones the loop below happens to
        # refetch.
        days_meta.clear()

    manifest["dataset_id"] = _currents.WINDOW_DATASET_ID
    manifest["bbox"] = _currents.BBOX
    manifest["tmin"] = _iso(tmin)

    # Drop entries whose files no longer exist (a wiped/partial store, or a
    # manually removed day file) so the manifest never claims stale coverage.
    for key in [k for k in days_meta if not (store / days_meta[k]["file"]).exists()]:
        del days_meta[key]

    start_date = max(tmin, available_min).date()
    end_date = available_max.date()  # partial last day included
    all_days = _date_range(start_date, end_date)

    def _needs_fetch(day: date) -> bool:
        if refetch_all:
            return True
        entry = days_meta.get(day.isoformat())
        return entry is None or not entry.get("final", False)

    to_fetch = sorted((d for d in all_days if _needs_fetch(d)), reverse=True)

    for day in to_fetch:
        try:
            ds = fetch_day(day)
        except Exception as exc:
            _log.warning("field store: fetch for %s failed: %s", day, exc)
            continue
        day_end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
        final = _day_is_complete(ds, day) and day_end + timedelta(hours=FINAL_MARGIN_H) <= now
        filename = _day_filename(day)
        _write_day_file(store / filename, ds)
        days_meta[day.isoformat()] = {
            "file": filename,
            "final": final,
            "fetched": _iso(datetime.now(timezone.utc)),
        }
        manifest["updated"] = _iso(datetime.now(timezone.utc))
        _write_manifest(store, manifest)

    manifest["updated"] = _iso(datetime.now(timezone.utc))
    _write_manifest(store, manifest)
    return manifest


# --- load_window (read side) --------------------------------------------------

def _check_hourly_continuity(times: np.ndarray, t0: datetime, t1: datetime) -> None:
    """Raise :class:`ValueError` naming the missing range unless ``times``
    (sorted, deduplicated) covers ``[t0, t1]`` with no gap wider than an
    hour."""
    t0v, t1v = _to_dt64(t0), _to_dt64(t1)
    if times[0] > t0v:
        raise ValueError(
            f"field store has no data before {np.datetime_as_string(times[0], unit='s')}Z "
            f"(need from {t0.isoformat()})"
        )
    if times[-1] < t1v:
        raise ValueError(
            f"field store has no data after {np.datetime_as_string(times[-1], unit='s')}Z "
            f"(need through {t1.isoformat()})"
        )
    gaps = np.where(np.diff(times) != np.timedelta64(1, "h"))[0]
    if gaps.size:
        i = int(gaps[0])
        raise ValueError(
            f"field store gap between {np.datetime_as_string(times[i], unit='s')}Z and "
            f"{np.datetime_as_string(times[i + 1], unit='s')}Z"
        )


def _day_entries_in_span(
    store: Path, days_meta: dict, lo_day: date, hi_day: date
) -> list[tuple[date, dict]]:
    """``(day, entry)`` for every day in ``[lo_day, hi_day]`` that both has a
    manifest entry and whose file still exists on disk, ascending — the
    day-selection step :func:`load_window` and :class:`StoreField` share. A day
    absent from the manifest (a gap, or outside the store's coverage) or whose
    file went missing is simply omitted here; a resulting gap surfaces later as a
    continuity failure (:func:`_check_hourly_continuity`) rather than raising in
    this step."""
    out = []
    for day in _date_range(lo_day, hi_day):
        entry = days_meta.get(day.isoformat())
        if entry is not None and (store / entry["file"]).exists():
            out.append((day, entry))
    return out


def load_window(store_dir: Path | str | None = None, *, t0: datetime, t1: datetime) -> xr.Dataset:
    """The store's ``uo``/``vo`` field over ``[t0, t1]``, bracketed one hourly
    step outside each end where available (integration needs bracketing — the
    same reason the legacy whole-window fetch used
    ``coordinates_selection_method="outside"`` in a single fetch, recreated
    here across day files).

    Opens every day file touching ``[t0, t1]`` plus its outer bracket hour,
    concatenates (sorted, deduplicated on ``time``), and checks hourly
    continuity across ``[t0, t1]`` — a gap (a missing day, or ``[t0, t1]``
    reaching outside the store's coverage) raises :class:`ValueError` naming
    the missing range. Returns a Dataset shaped exactly like the legacy
    whole-window fetch's output (``uo``, ``vo``; ``time``/``latitude``/
    ``longitude``) so downstream consumers are drop-in.
    """
    store = _resolve_store_dir(store_dir)
    manifest = _load_manifest(store)
    days_meta = manifest.get("days", {})

    lo_day = (t0 - timedelta(hours=1)).date()
    hi_day = (t1 + timedelta(hours=1)).date()

    opened: list[xr.Dataset] = []
    try:
        for _day, entry in _day_entries_in_span(store, days_meta, lo_day, hi_day):
            opened.append(xr.open_dataset(store / entry["file"]))
        if not opened:
            raise ValueError(
                f"field store has no day files covering [{t0.isoformat()}, {t1.isoformat()}]"
            )
        combined = xr.concat(opened, dim="time").drop_duplicates("time").sortby("time")
        combined = combined.load()
    finally:
        for ds in opened:
            ds.close()

    times = combined["time"].values
    _check_hourly_continuity(times, t0, t1)

    t0v, t1v = _to_dt64(t0), _to_dt64(t1)
    lo_idx = max(int(np.searchsorted(times, t0v, side="right")) - 1, 0)
    hi_idx = min(int(np.searchsorted(times, t1v, side="left")), len(times) - 1)
    return combined.isel(time=slice(lo_idx, hi_idx + 1))


# --- StoreField (read side, streaming) ----------------------------------------
#
# :func:`load_window` is the batch-friendly read path: bracket, concatenate, and
# load a whole span into one in-RAM Dataset — fine for the ~72 h windows the
# build and the PoC API read today, but the deployment API's runs cover the full
# cruise span (``plans/done/034-deployment-focused-app.md``, workstream B), where
# loading every touched day file whole would put the entire field back in API
# RAM — exactly what streaming exists to avoid (the API pod's 4 Gi limit).
# ``StoreField`` is the streaming alternative: a drop-in for
# ``_forecast._Field`` (the same ``lons``/``lats``/``times``/``u``/``v``/
# ``velocity()`` contract the scalar ``_Field.velocity`` and the vectorized
# ``_batch_advect`` consume) whose ``u``/``v`` are not one big array but a thin
# view over an LRU of opened day arrays, loaded on demand and evicted beyond a
# small cap — so a run spanning the whole store only ever holds a handful of day
# files (~200 MB at the default cap) in memory, however long the run.
#
# The per-step RK4 arithmetic itself (corner gather, time lerp, NaN-stop) is
# untouched — :meth:`StoreField.velocity`/:meth:`StoreField._bilin` and
# :class:`_StoreArray`'s two access patterns exist solely so the *same*
# ``_forecast`` code samples a store-backed field exactly as it samples an
# in-RAM one.

_DEFAULT_DAY_CACHE_CAP = 4  # ~200 MB at ~50 MB/day file (see the module docstring)

# Hard ceiling on the per-request day-cache cap, whatever start spread a caller
# asks for. Each resident day is ~50 MB, so this bounds one run's field residency
# at ~500 MB; with the API's concurrency gate (:data:`_api._MAX_CONCURRENCY`) that
# keeps several concurrent forecast runs inside the pod's memory limit. It is the
# memory backstop for SEC-1: even if a request's seed-start spread slipped past the
# API's spread guard (:data:`_api._MAX_START_SPREAD_DAYS`), the cache can never pin
# more than this many day files at once — a wider spread degrades to cache thrash
# (bounded by the seeds x hours budget), never an OOM.
_MAX_DAY_CACHE_CAP = 10


def day_cache_cap_for_starts(
    min_start_epoch: float,
    max_start_epoch: float,
    *,
    default: int = _DEFAULT_DAY_CACHE_CAP,
    max_cap: int = _MAX_DAY_CACHE_CAP,
) -> int:
    """The day-cache cap a batch needs so its currently-active seeds' distinct
    calendar days all stay resident at once, given the earliest/latest seed
    ``start`` (epoch seconds) in the batch.

    ``_batch_advect`` never resyncs seeds to a shared wall clock: each seed's
    own ``t`` advances by the same per-step ``dt`` from its own ``start``, so
    at any step index the still-active seeds' absolute times differ by exactly
    their original start spread — a batch whose seeds start on far-apart
    calendar days needs that many days resident *for the whole run*, not just
    at the start. ``_DEFAULT_DAY_CACHE_CAP`` alone only covers the common case
    (seeds released close together); leaving it fixed regardless of the actual
    start spread makes every step thrash the cache — evicting and reopening
    day files it just evicted — the moment the spread exceeds the cap (see
    :class:`_DayArrayCache`). ``+ 2`` covers the bracketing pair (``jt``,
    ``jt+1``) at each end of the spread. The result is clamped to ``max_cap``
    (:data:`_MAX_DAY_CACHE_CAP`) so no single run can pin an unbounded number of
    day files resident — the SEC-1 memory backstop; the API rejects a spread wide
    enough to hit the clamp before it gets here (see
    :data:`_api._MAX_START_SPREAD_DAYS`), so in normal operation the clamp never
    binds."""
    spread_days = math.ceil(abs(max_start_epoch - min_start_epoch) / 86400.0)
    return min(max_cap, max(default, spread_days + 2))


class _DayArrayCache:
    """Bounded LRU of opened per-day ``(uo, vo)`` arrays (each ``(24, lat,
    lon)``, lat/lon ascending — the same orientation :class:`_forecast._Field`
    puts its own ``u``/``v`` in), keyed by an index into a fixed day list. A
    batch whose seeds all start close together in time has a working set of at
    most a couple of calendar days at once as its monotone cursor advances; a
    batch whose seeds start on far-apart calendar days needs a cap sized to
    that spread instead (see :func:`day_cache_cap_for_starts`) — the cap here
    bounds memory explicitly either way, evicting the least-recently-used day
    once ``cap`` is exceeded."""

    def __init__(self, store: Path, days: list[date], days_meta: dict, cap: int):
        # Floor of 2, not 1: the sampler's bracketing pair (jt, jt+1) spans at
        # most two consecutive calendar days, so cap >= 2 keeps both resident
        # and a run's misses stay at ~one per simulated day. cap == 1 would
        # alternate-evict the pair on every day-straddling gather — thousands
        # of full netCDF reopens per boundary hour, a pathology no caller
        # should be able to configure.
        if cap < 2:
            raise ValueError(f"day cache cap must be >= 2, got {cap}")
        self._store = store
        self._days = days
        self._days_meta = days_meta
        self._cap = cap
        self._cache: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def get(self, day_idx: int) -> tuple[np.ndarray, np.ndarray]:
        """The ``(uo, vo)`` arrays for ``self._days[day_idx]`` — loading and
        caching on first access, moving to most-recently-used on every access,
        and evicting the least-recently-used entry once the cache holds more
        than ``cap`` days."""
        cached = self._cache.get(day_idx)
        if cached is not None:
            self._cache.move_to_end(day_idx)
            return cached
        day = self._days[day_idx]
        entry = self._days_meta[day.isoformat()]
        path = self._store / entry["file"]
        with xr.open_dataset(path) as ds:
            ds = ds.sortby("latitude").sortby("longitude").transpose(
                "time", "latitude", "longitude"
            )
            arrays = (ds["uo"].values, ds["vo"].values)
        self._cache[day_idx] = arrays
        self._cache.move_to_end(day_idx)
        if len(self._cache) > self._cap:
            self._cache.popitem(last=False)  # evict least-recently-used
        return arrays


class _StoreArray:
    """Indexable ``(time, lat, lon)`` view over a :class:`_DayArrayCache`,
    mirroring the two access patterns :mod:`_forecast` actually uses against
    ``_Field.u``/``.v``: ``arr[jt]`` (one absolute time-axis index) returns the
    2-D lat/lon plane the scalar ``_Field.velocity``/``_bilin`` indexes with
    ``[iy, ix]``; ``arr[jj, iyc, ixc]`` (three parallel integer arrays) gathers
    one corner value per seed, the shape ``_forecast._vec_deriv`` uses. Both are
    served from the day cache rather than one in-RAM array, dispatching each
    absolute time index to whichever day file holds it via the per-index
    ``day_labels``/``hour_labels`` :class:`StoreField` precomputes once at
    construction."""

    def __init__(
        self,
        cache: _DayArrayCache,
        day_labels: np.ndarray,
        hour_labels: np.ndarray,
        which: int,
    ):
        self._cache = cache
        self._day_labels = day_labels
        self._hour_labels = hour_labels
        self._which = which  # 0 = uo, 1 = vo

    def __getitem__(self, key):
        if isinstance(key, tuple):
            jj, iyc, ixc = key
            return self._gather(np.asarray(jj), np.asarray(iyc), np.asarray(ixc))
        return self._plane(int(key))

    def _plane(self, jt: int) -> np.ndarray:
        day_idx = int(self._day_labels[jt])
        hour = int(self._hour_labels[jt])
        return self._cache.get(day_idx)[self._which][hour]

    def _gather(self, jj: np.ndarray, iyc: np.ndarray, ixc: np.ndarray) -> np.ndarray:
        days = self._day_labels[jj]
        hours = self._hour_labels[jj]
        out = np.empty(jj.shape, dtype=np.float64)
        for day_idx in np.unique(days):
            mask = days == day_idx
            plane = self._cache.get(int(day_idx))[self._which]
            out[mask] = plane[hours[mask], iyc[mask], ixc[mask]]
        return out


class StoreField(_forecast._Field):
    """Store-backed drop-in for ``_forecast._Field`` over an explicit ``[t_lo,
    t_hi]`` span: subclasses ``_Field`` so the sampler (``velocity``/``_bilin``)
    and every batch-advection helper (``_vec_deriv`` et al.) run completely
    unmodified against a store-backed field — only ``__init__`` differs, and it
    never calls ``super().__init__()`` (that expects an in-RAM ``xr.Dataset``;
    this class builds its five attributes itself from the store instead). The
    inherited methods touch only ``lons``/``lats``/``times``/``u``/``v`` — the
    whole sampler contract (see ``_Field``'s docstring) — so setting those five
    attributes to the store-backed equivalents below is sufficient: ``u``/``v``
    are :class:`_StoreArray` views over a bounded :class:`_DayArrayCache` rather
    than one in-RAM array, so a run spanning the whole store holds only a
    handful of day files at a time (see the module docstring).

    Built once per run (not meant to be reused across concurrent runs — the day
    cache is sized for one run's monotone walk): opens every day file touching
    ``[t_lo, t_hi]`` (bracketed one hour outside each end, like
    :func:`load_window`) to read its ``time`` coordinate only (cheap — the lazy
    backend never touches ``uo``/``vo`` for this), builds the hourly time axis,
    and verifies hourly continuity with the same :func:`_check_hourly_continuity`
    :func:`load_window` uses. A gap (a missing interior day, or ``[t_lo, t_hi]``
    reaching outside the store's coverage) raises :class:`ValueError`, exactly as
    :func:`load_window` would for the same span.
    """

    def __init__(
        self,
        store_dir: Path | str | None,
        t_lo: datetime,
        t_hi: datetime,
        *,
        day_cache_cap: int = _DEFAULT_DAY_CACHE_CAP,
    ):
        store = _resolve_store_dir(store_dir)
        manifest = _load_manifest(store)
        days_meta = manifest.get("days", {})

        t_lo, t_hi = _to_utc(t_lo), _to_utc(t_hi)
        lo_day = (t_lo - timedelta(hours=1)).date()
        hi_day = (t_hi + timedelta(hours=1)).date()
        entries = _day_entries_in_span(store, days_meta, lo_day, hi_day)
        if not entries:
            raise ValueError(
                f"field store has no day files covering [{t_lo.isoformat()}, {t_hi.isoformat()}]"
            )

        days = [day for day, _entry in entries]
        day_times: list[np.ndarray] = []
        day_labels_parts: list[np.ndarray] = []
        hour_labels_parts: list[np.ndarray] = []
        grid_lons: np.ndarray | None = None
        grid_lats: np.ndarray | None = None
        for day_idx, (day, entry) in enumerate(entries):
            with xr.open_dataset(store / entry["file"]) as ds:
                ds = ds.sortby("latitude").sortby("longitude")
                times = np.asarray(ds["time"].values)  # coord only — uo/vo untouched
                if grid_lons is None:
                    grid_lons = ds["longitude"].values.astype(float)
                    grid_lats = ds["latitude"].values.astype(float)
            day_start = _to_dt64(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
            hours = np.round((times - day_start) / np.timedelta64(1, "h")).astype(int)
            if np.any((hours < 0) | (hours > 23)):
                raise ValueError(
                    f"field store day file for {day.isoformat()} has a time step "
                    "outside its own UTC day"
                )
            day_times.append(times)
            day_labels_parts.append(np.full(times.shape, day_idx, dtype=int))
            hour_labels_parts.append(hours)

        all_times = np.concatenate(day_times)
        day_labels = np.concatenate(day_labels_parts)
        hour_labels = np.concatenate(hour_labels_parts)

        # Sort + dedupe on time, mirroring load_window's drop_duplicates/sortby —
        # day files shouldn't overlap by construction, but this stays defensive
        # about it rather than assuming.
        order = np.argsort(all_times, kind="stable")
        all_times = all_times[order]
        day_labels = day_labels[order]
        hour_labels = hour_labels[order]
        if all_times.size > 1:
            keep = np.concatenate(([True], np.diff(all_times) != np.timedelta64(0)))
            all_times = all_times[keep]
            day_labels = day_labels[keep]
            hour_labels = hour_labels[keep]

        _check_hourly_continuity(all_times, t_lo, t_hi)

        # Bracket down to [t_lo, t_hi] plus one outer hour each side, mirroring
        # load_window's final slice — so the index arrays below (and the day
        # cache's working set) cover only what this run actually needs.
        t0v, t1v = _to_dt64(t_lo), _to_dt64(t_hi)
        lo_idx = max(int(np.searchsorted(all_times, t0v, side="right")) - 1, 0)
        hi_idx = min(int(np.searchsorted(all_times, t1v, side="left")), len(all_times) - 1)
        sl = slice(lo_idx, hi_idx + 1)
        all_times, day_labels, hour_labels = all_times[sl], day_labels[sl], hour_labels[sl]

        self.lons = grid_lons
        self.lats = grid_lats
        self.times = all_times.astype("datetime64[s]").astype(np.float64)

        cache = _DayArrayCache(store, days, days_meta, day_cache_cap)
        self.u = _StoreArray(cache, day_labels, hour_labels, which=0)
        self.v = _StoreArray(cache, day_labels, hour_labels, which=1)
        # No further attributes: velocity()/_bilin() are inherited from _Field
        # unmodified — they only ever index self.u[jt]/self.v[jt] (a 2-D plane,
        # served here by _StoreArray.__getitem__) and self.lons/.lats/.times, so
        # the base class's implementation is already correct against a
        # store-backed field.
