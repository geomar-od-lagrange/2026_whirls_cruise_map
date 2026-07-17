"""Alternative forecast API that advects with **OceanParcels v4** instead of the
hand-rolled RK4 in :mod:`whirls_cruise_map._forecast`.

``pixi run serve-parcels`` runs this: a FastAPI app (uvicorn, port 8002) exposing a
``GET /api/forecast?lat=&lon=&start=`` endpoint returning one GeoJSON ``Feature``
(``kind`` ``"poc-forecast-parcels"``). This is the **single-point** contract the RK4
service once exposed; :mod:`._api` has since moved to a batch ``POST /api/forecast``
(a sequence of seeds), so the two no longer share an endpoint shape — the point of the
oracle is engine-vs-engine, not API parity. It exists so we can advect the identical
CMEMS current window through a mature, community-accepted Lagrangian integrator and
cite it as an independent reference when reasoning about the RK4 engine's time-stepping
and correctness. ``tmp_parcels_compare/compare.py`` runs both over the same field and
reports their agreement and cost.

It reads its own +/-12 h window from the incremental per-day field store
(:func:`._field_store.load_window`, the same store the RK4 service's
:mod:`._api` reads from) so both engines integrate the same field over the
same span, and it anchors ``t0`` with the same helper
(:func:`._forecast._anchor_t0`) so a click resolves to the same start instant on
either backend. Build the store once with a ``derive --tier slow`` run before
comparing.

Parcels v4 mapping (this is the reference we cite for time-stepping):

- **FieldSet.** The window (``uo``/``vo`` in m/s on ``time``/``latitude``/
  ``longitude``, land = NaN, surface) gets a size-1 ``depth`` axis added, is passed
  through :func:`parcels.convert.copernicusmarine_to_sgrid` (renames the CF axes to
  ``lon``/``lat``/``depth``/``time`` and attaches SGRID A-grid metadata), then
  :meth:`parcels.FieldSet.from_sgrid_conventions` with ``mesh="spherical"``. Built
  once (thread-safe) and reused; a request only builds a fresh 1-particle
  :class:`parcels.ParticleSet`.
- **Geographic conversion.** With a spherical mesh, parcels' velocity interpolator
  (``XLinear_Velocity``) converts m/s → deg/s with ``deg2m = 1852*60 = 111120`` and
  a ``cos(lat)`` zonal factor. Our RK4 uses ``R·π/180 = 111194.9`` (R = 6.371e6 m).
  That ~0.067 % constant is the dominant, systematic source of divergence between
  the two engines (a few m over a ~20 km / 12 h path); everything else — bilinear in
  space, linear in time, RK4 with a 300 s step — matches by construction.
- **Advection + output.** ``AdvectionRK4`` with ``dt = 300 s`` (== RK4's sub-step),
  run in **15-min chunks** so we read the trajectory straight off ``pset.lon``/
  ``pset.lat`` after each chunk (a vertex every 15 min; the 3/6/9/12 h marks are the
  ``h*4``-th vertices). This in-memory read is a lighter equivalent of parcels'
  file mechanism (``ParticleFile`` → Parquet → :func:`parcels.read_particlefile`),
  avoiding per-request disk I/O.
- **Land / NaN / out-of-bounds.** Parcels samples the raw NaN-land field bilinearly;
  a NaN corner or an off-grid / out-of-time sample flips the particle to an error
  state and the kernel loop **raises** (``FieldInterpolationError`` /
  ``FieldOutOfBoundError`` / ``OutsideTimeInterval`` / …) *without* advancing that
  step. We catch that and truncate the polyline at the last completed 15-min vertex
  — the same "stop at the coast / field edge / window end" behaviour the RK4 engine
  gets from its ``velocity()`` returning ``None``. (A production parcels setup would
  instead fill land with 0 and use boundary kernels; parcels' native land
  convention is zero-velocity, not NaN.)

Like :mod:`._api`, the static map is independent: on a field-fetch/login failure
the endpoint returns 503 and the map still serves.
"""
from __future__ import annotations

import threading
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import parcels

from . import _api, _field_store, _forecast, _time

# --- config (mirrors ._api; only the engine and port differ) -----------------

_PORT = 8002  # static=:8000, RK4 API=:8001, this parcels API=:8002

# Kept here (not in ._api, which no longer needs a fixed horizon/marks — the v2
# batch API takes both from the request) so the two engines still compare on the
# same cadence.
_HORIZON_H = 12.0
_MARK_HOURS = (3, 6, 9, 12)
_WINDOW_BACK_H = 12.0                 # the oracle's own +/-12 h field window
_WINDOW_FWD_H = 12.0
_DT_S = 300                           # AdvectionRK4 step == _forecast.STEP_MIN * 60
_VERTEX_MIN = int(_forecast.VERTEX_MIN)  # emit a vertex every 15 min
_COORD_NDIGITS = _forecast._COORD_NDIGITS

# The exact set of exceptions the parcels kernel loop raises when a particle
# samples land (NaN), leaves the grid, or steps outside the time window. Catching
# these lets us truncate the track just like the RK4 engine stops at a NaN corner.
_PARCELS_SAMPLING_ERRORS = tuple(parcels.AllParcelsErrorCodes)

# --- field / FieldSet (built once, reused) -----------------------------------

_fieldset_lock = threading.Lock()
_fieldset: parcels.FieldSet | None = None
_times_epoch: np.ndarray | None = None  # window times as float epoch seconds


def _build_fieldset(window: xr.Dataset) -> tuple[parcels.FieldSet, np.ndarray]:
    """Build a spherical-mesh :class:`parcels.FieldSet` from the CMEMS window and
    return it with the window's times as float epoch seconds (for anchoring/bounds).

    The window carries no depth axis (the surface slice is squeezed out on fetch);
    parcels wants a vertical axis, so we add a size-1 surface ``depth``. Land stays
    ``NaN`` so parcels stops at the coast exactly where the raw field does — the
    same field the RK4 engine integrates."""
    ds = window.expand_dims(depth=[0.0])
    ds["depth"].attrs.update(axis="Z", positive="down", units="m")
    # copernicusmarine_to_sgrid keys off the CF axis metadata; the cached file
    # already carries units/axis on lat/lon/time, but set defensively.
    ds["latitude"].attrs.setdefault("units", "degrees_north")
    ds["longitude"].attrs.setdefault("units", "degrees_east")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # parcels' alpha/no-depth notices
        ds_sgrid = parcels.convert.copernicusmarine_to_sgrid(
            fields={"U": ds["uo"], "V": ds["vo"]}
        )
        fieldset = parcels.FieldSet.from_sgrid_conventions(ds_sgrid, mesh="spherical")

    times_epoch = window["time"].values.astype("datetime64[s]").astype(np.float64)
    return fieldset, times_epoch


def _get_fieldset() -> tuple[parcels.FieldSet, np.ndarray]:
    """The in-memory parcels FieldSet, built once (thread-safe) from the oracle's
    own +/-12 h window read off the incremental field store (the same store the
    RK4 service reads from, but its own fetch — this module never held a shared
    window with ``._api``)."""
    global _fieldset, _times_epoch
    with _fieldset_lock:
        if _fieldset is None:
            print("building parcels FieldSet from the CMEMS field store (first request)…")
            now = datetime.now(timezone.utc)
            window = _field_store.load_window(
                t0=now - timedelta(hours=_WINDOW_BACK_H),
                t1=now + timedelta(hours=_WINDOW_FWD_H),
            )
            _fieldset, _times_epoch = _build_fieldset(window)
            print("parcels FieldSet ready")
        return _fieldset, _times_epoch


# --- advection ---------------------------------------------------------------

def _advect(
    fieldset: parcels.FieldSet, lon: float, lat: float, t0_epoch: float
) -> tuple[list[list[float]], list[dict]]:
    """Advect one particle from ``(lon, lat)`` at epoch-second ``t0_epoch`` forward
    ``_HORIZON_H`` with ``AdvectionRK4`` (``dt = _DT_S``), returning the polyline
    ``coords`` (a ``[lon, lat]`` vertex every ``_VERTEX_MIN`` starting at the head)
    and the ``marks`` reached (``{hours, lon, lat}`` at each ``_MARK_HOURS``).

    Runs in 15-min chunks and reads positions straight off the ParticleSet after
    each chunk. Stops at the coast / field edge / window end: if a chunk raises a
    parcels sampling error the last (partial) step is not applied, so we simply
    break and keep the vertices from the fully-completed chunks — matching the RK4
    engine's truncation. Marks beyond the truncation are omitted."""
    t0 = np.datetime64(int(round(t0_epoch)), "s")
    pset = parcels.ParticleSet(
        fieldset=fieldset, pclass=parcels.Particle, lon=[lon], lat=[lat], z=[0.0], time=[t0]
    )

    coords = [[round(float(pset.lon[0]), _COORD_NDIGITS), round(float(pset.lat[0]), _COORD_NDIGITS)]]
    n_chunks = round(_HORIZON_H * 60.0 / _VERTEX_MIN)
    mark_at = {h * 60 // _VERTEX_MIN: h for h in _MARK_HOURS}
    marks: list[dict] = []

    dt = np.timedelta64(_DT_S, "s")
    chunk = np.timedelta64(_VERTEX_MIN, "m")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for step in range(1, n_chunks + 1):
            try:
                pset.execute([parcels.kernels.AdvectionRK4], dt=dt, runtime=chunk,
                             verbose_progress=False)
            except _PARCELS_SAMPLING_ERRORS:
                break  # hit the coast / field edge / window end — truncate here
            rlon = round(float(pset.lon[0]), _COORD_NDIGITS)
            rlat = round(float(pset.lat[0]), _COORD_NDIGITS)
            if not (np.isfinite(rlon) and np.isfinite(rlat)):
                break
            coords.append([rlon, rlat])
            if step in mark_at:
                marks.append({"hours": mark_at[step], "lon": rlon, "lat": rlat})
    return coords, marks


def _parcels_feature(
    fieldset: parcels.FieldSet, props: dict, lon: float, lat: float, t0: float, valid: str
) -> dict | None:
    """One advection ``LineString`` Feature (parcels engine) from ``(lon, lat)`` at
    ``t0``, or ``None`` if the head is on land / off-grid (a ``<2``-vertex line).
    Mirrors the RK4 engine's single-point advection (:func:`._forecast._integrate`)
    so both engines emit the same shape."""
    coords, marks = _advect(fieldset, lon, lat, t0)
    if len(coords) < 2:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {**props, "valid_time": valid, "marks": marks},
    }


def _point_forecast(lat: float, lon: float, start: str | None = None) -> dict | None:
    """The +12 h parcels forecast Feature for a clicked ``(lat, lon)``, integrated
    from ``start`` (ISO-8601; default the window time nearest now). Mirrors the RK4
    engine's single-point advection (:func:`._forecast._integrate`), only swapping the
    integrator; ``None`` on land/off-grid, ``ValueError`` if ``start`` is unparseable
    or outside the window."""
    fieldset, times_epoch = _get_fieldset()
    t0_epoch = None
    if start:
        t0_epoch = _time.parse_iso_to_epoch(start)  # ValueError -> 422 in the endpoint
        lo, hi = float(times_epoch[0]), float(times_epoch[-1])
        if not (lo <= t0_epoch <= hi):
            raise ValueError(
                f"start {start} is outside the field window "
                f"[{_time.iso_z_from_epoch(lo)} .. {_time.iso_z_from_epoch(hi)}]"
            )
    # Reuse the RK4 anchor helper: it only needs a `.times` array of epoch seconds.
    from types import SimpleNamespace

    t0, valid = _forecast._anchor_t0(SimpleNamespace(times=times_epoch), t0_epoch)
    return _parcels_feature(fieldset, {"kind": "poc-forecast-parcels"}, lon, lat, t0, valid)


# --- app (the legacy single-point GET oracle; ._api is now a batch POST) ------

app = FastAPI(title="WHIRLS interactive forecast — parcels engine (PoC)")
# SEC-6: this oracle is a **local dev-only comparison tool — never deployed** and never
# fronted by the public gateway (only `._api`'s batch POST is). It serves only
# already-public forecast data and does bounded single-particle compute. Even so, keep
# it hardened app-side: scope CORS to the same localhost dev origins as `._api` (not the
# `*` wildcard a public endpoint shouldn't advertise) and bound the coordinate query
# params below, so nothing here diverges from the production endpoint's posture should it
# ever be exposed by accident.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_api._DEV_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.get("/api/forecast")
def forecast(
    lat: float = Query(..., ge=-90, le=90, description="deployment latitude, decimal degrees"),
    lon: float = Query(..., ge=-180, le=180, description="deployment longitude, decimal degrees"),
    start: str | None = Query(
        None, description="ISO-8601 start time (default: window time nearest now)"
    ),
) -> dict:
    """+12 h current-advection forecast (GeoJSON ``Feature``, ``kind`` =
    ``poc-forecast-parcels``) from a clicked position, advected with OceanParcels v4.
    The single-point GET contract the RK4 service once exposed (now a batch POST in
    :mod:`._api`); kept as the engine-comparison oracle over the same field. ``lat``/
    ``lon`` are range-checked (SEC-6)."""
    try:
        feature = _point_forecast(lat, lon, start)
    except (FileNotFoundError, _field_store.FieldUnavailableError):
        # Store missing/empty/gapped — `load_window` raises FieldUnavailableError here,
        # so catch it (before the ValueError branch it subclasses) and answer a fixed 503
        # (SEC-7). Any other failure is a real 500, left to surface and be logged rather
        # than masked as a transient 503 with internals interpolated in.
        raise HTTPException(status_code=503, detail=_api._FIELD_UNAVAILABLE_DETAIL)
    except ValueError as exc:  # unparseable / out-of-window start (timestamps only, safe)
        raise HTTPException(status_code=422, detail=str(exc))
    if feature is None:
        raise HTTPException(
            status_code=422, detail="no forecast: start point is on land or off the field"
        )
    return feature


def main() -> None:
    import uvicorn

    print(f"parcels forecast API on http://localhost:{_PORT}/api/forecast")
    print("the RK4 service is separate: `pixi run serve-api` (:8001)")
    print("the first request builds the parcels FieldSet from the cached window")
    uvicorn.run(app, host="0.0.0.0", port=_PORT)


if __name__ == "__main__":
    main()
