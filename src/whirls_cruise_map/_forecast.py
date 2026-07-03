"""Current-advection forecast and hindcast: advect a passive particle through the
**time-dependent** CMEMS surface-current field from each instrument's latest fix —
the drifters and the gliders (XSPAR buoy, seagliders) alike — forward for the
forecast, backward for the hindcast. Gliders maneuver actively, so their
advection is a passive-drift what-if (surface current only), meaningful for their
drift phases rather than a track prediction.

Starting at the instrument head we integrate ``dx/dt = u(x, y, t)``,
``dy/dt = v(x, y, t)`` with RK4 to ±6 h, advancing a clock alongside the position
so the particle is pushed by the current *at each moment* — an hourly field window
(:func:`whirls_cruise_map._currents.fetch_field_window`), bilinear in space and
linear in time. Because the model already carries the near-inertial oscillation at
these latitudes, the path curls into the inertial loop the drifters show rather
than the straight streamline a single frozen snapshot would give. The hindcast
integrates the same window backward (negative step); it is a current-only
back-trajectory, **not** the drifter's observed past track (that is the trajectory
line from :func:`whirls_cruise_map._geojson.tracks_geojson`).

It is **not** a calibrated drifter prediction:

- **Surface current only.** ``uo``/``vo`` are the modelled surface current; no
  windage / Stokes drift (undrogued) or deeper-layer sampling (drogued). So this
  is an indicative passive-tracer track, not the drifter's predicted path.
- **Model near-inertial amplitude.** The inertial loop is only as strong as the
  model's; free-running global models can under-represent wind-driven near-inertial
  energy (see ``plans/012-near-inertial-forecast.md``, Phase 0).

We integrate over the raw field — land kept as ``NaN``, not the trails' land-filled,
magnitude-compressed ``currents.json`` — so the line carries correct speeds and the
integrator *stops* at the coast (or the window edge) instead of being dragged
across it. See ``docs/forecast.md``.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xarray as xr

_EARTH_RADIUS_M = 6_371_000.0

# Integrate this far forward, with this RK4 sub-step and this polyline-vertex
# spacing (minutes). The marks (hours) and vertex spacing must divide the step
# evenly so each mark lands exactly on an emitted vertex; at ~0.5 m/s a particle
# moves ~1 grid cell in 6 h, so the scheme is not delicate.
HORIZON_H = 6.0
STEP_MIN = 5.0
VERTEX_MIN = 15.0
MARK_HOURS = (1, 3, 6)

_COORD_NDIGITS = 5  # ~1 m; the 6 h displacement is ~10 km, so this is ample


class _Field:
    """Bilinear-in-space, linear-in-time sampler over the current field window.

    Holds ``uo``/``vo`` on an ascending lat/lon grid across a stack of hourly time
    slices, land kept as ``NaN``. :meth:`velocity` returns ``None`` outside the
    grid, outside the time window, or where any of the four surrounding cells (at
    either bracketing time) is land — which is what lets the integrator stop at the
    coast and at the field/window edge.

    ``times`` are stored as float seconds since the Unix epoch so the stepper can
    pass an absolute clock time and get the current *at that moment*, tracing the
    inertial loop the model carries rather than a single frozen streamline.
    """

    def __init__(self, field: xr.Dataset):
        f = field.sortby("latitude").sortby("longitude")  # both ascending
        f = f.transpose("time", "latitude", "longitude")
        self.lons = f["longitude"].values.astype(float)
        self.lats = f["latitude"].values.astype(float)
        self.u = f["uo"].values  # (time, lat, lon)
        self.v = f["vo"].values
        self.times = f["time"].values.astype("datetime64[s]").astype(np.float64)

    def _bilin(self, plane: np.ndarray, ix: int, iy: int, w: tuple) -> float:
        """Bilinear value of one 2-D ``plane`` at pre-solved cell/weights; ``NaN``
        if any corner is land."""
        corners = (plane[iy, ix], plane[iy, ix + 1], plane[iy + 1, ix], plane[iy + 1, ix + 1])
        total = 0.0
        for wi, ci in zip(w, corners):
            if math.isnan(ci):
                return math.nan  # any land corner -> undefined here
            total += wi * ci
        return total

    def velocity(self, lon: float, lat: float, t: float) -> tuple[float, float] | None:
        """Bilinear-in-space, linear-in-time ``(uo, vo)`` in m/s at ``(lon, lat)``
        and epoch-second ``t``; ``None`` off-grid, outside the time window, or on a
        cell with any land (``NaN``) corner at either bracketing time."""
        if t < self.times[0] or t > self.times[-1]:
            return None  # outside the fetched window -> truncate
        ix = int(np.searchsorted(self.lons, lon, side="right")) - 1
        iy = int(np.searchsorted(self.lats, lat, side="right")) - 1
        if not (0 <= ix < self.lons.size - 1) or not (0 <= iy < self.lats.size - 1):
            return None
        x0, x1 = self.lons[ix], self.lons[ix + 1]
        y0, y1 = self.lats[iy], self.lats[iy + 1]
        tx = (lon - x0) / (x1 - x0)
        ty = (lat - y0) / (y1 - y0)
        # Corner weights for (x0,y0), (x1,y0), (x0,y1), (x1,y1).
        w = ((1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty)

        jt = int(np.searchsorted(self.times, t, side="right")) - 1
        jt = min(max(jt, 0), self.times.size - 2)
        t0, t1 = self.times[jt], self.times[jt + 1]
        wt = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)

        u0 = self._bilin(self.u[jt], ix, iy, w)
        u1 = self._bilin(self.u[jt + 1], ix, iy, w)
        v0 = self._bilin(self.v[jt], ix, iy, w)
        v1 = self._bilin(self.v[jt + 1], ix, iy, w)
        if math.isnan(u0) or math.isnan(u1) or math.isnan(v0) or math.isnan(v1):
            return None
        return (1 - wt) * u0 + wt * u1, (1 - wt) * v0 + wt * v1


def _deriv(
    field: _Field, lon: float, lat: float, t: float
) -> tuple[float, float] | None:
    """``(dlon/dt, dlat/dt)`` in deg/s at ``(lon, lat)`` and epoch-second ``t``, or
    ``None`` on land/edge. ``dlat = v/R``, ``dlon = u/(R cos lat)``, scaled to
    degrees."""
    vel = field.velocity(lon, lat, t)
    if vel is None:
        return None
    u, v = vel
    dlat = v / _EARTH_RADIUS_M * (180.0 / math.pi)
    dlon = u / (_EARTH_RADIUS_M * math.cos(math.radians(lat))) * (180.0 / math.pi)
    return dlon, dlat


def _rk4_step(
    field: _Field, lon: float, lat: float, t: float, dt: float
) -> tuple[float, float] | None:
    """One RK4 step of ``dt`` seconds from clock time ``t`` (stages sample the
    field at ``t``, ``t+dt/2``, ``t+dt``), or ``None`` if any stage samples
    land/edge (so the caller truncates the path at the last good point)."""
    k1 = _deriv(field, lon, lat, t)
    if k1 is None:
        return None
    k2 = _deriv(field, lon + 0.5 * dt * k1[0], lat + 0.5 * dt * k1[1], t + 0.5 * dt)
    if k2 is None:
        return None
    k3 = _deriv(field, lon + 0.5 * dt * k2[0], lat + 0.5 * dt * k2[1], t + 0.5 * dt)
    if k3 is None:
        return None
    k4 = _deriv(field, lon + dt * k3[0], lat + dt * k3[1], t + dt)
    if k4 is None:
        return None
    lon_n = lon + dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
    lat_n = lat + dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    return lon_n, lat_n


def _integrate(
    field: _Field, lon0: float, lat0: float, t0: float, direction: int = 1
) -> tuple[list[list[float]], list[dict]]:
    """Advect from ``(lon0, lat0)`` at clock time ``t0`` (epoch seconds) to
    :data:`HORIZON_H`, ``direction`` +1 forward (forecast) or -1 backward
    (hindcast), returning the polyline ``coords`` (a vertex every :data:`VERTEX_MIN`,
    starting at the head) and the ``marks`` actually reached (``{hours, lon, lat}``
    at each :data:`MARK_HOURS`, ``hours`` signed by ``direction``). The clock
    advances with the integration, so the particle is pushed by the current at each
    moment. Stops early at the coast/edge/window end; marks beyond the truncation
    are omitted."""
    dt = direction * STEP_MIN * 60.0
    n_steps = round(HORIZON_H * 60.0 / STEP_MIN)
    vertex_every = round(VERTEX_MIN / STEP_MIN)
    mark_at = {round(h * 60.0 / STEP_MIN): direction * h for h in MARK_HOURS}

    coords = [[round(lon0, _COORD_NDIGITS), round(lat0, _COORD_NDIGITS)]]
    marks: list[dict] = []
    lon, lat, t = lon0, lat0, t0
    for step in range(1, n_steps + 1):
        nxt = _rk4_step(field, lon, lat, t, dt)
        if nxt is None:
            break  # hit the coast, field edge, or window end — truncate here
        lon, lat = nxt
        t += dt
        rlon, rlat = round(lon, _COORD_NDIGITS), round(lat, _COORD_NDIGITS)
        if step % vertex_every == 0:
            coords.append([rlon, rlat])
        if step in mark_at:  # mark steps are multiples of vertex_every (same point)
            marks.append({"hours": mark_at[step], "lon": rlon, "lat": rlat})
    return coords, marks


def _drifter_heads(tracks: pd.DataFrame) -> list[tuple[dict, float, float]]:
    """``(properties, lon, lat)`` for each drifter's latest fix. ``properties``
    carries ``D_number`` and ``batch`` (the *latest* fix's batch — the same key
    the marker and trajectory use, so the advection line toggles with them)."""
    heads = []
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        last = list(group.itertuples(index=False))[-1]
        heads.append(
            ({"D_number": d_number, "batch": last.batch},
             float(last.Longitude), float(last.Latitude))
        )
    return heads


def _glider_heads(gliders: list) -> list[tuple[dict, float, float]]:
    """``(properties, lon, lat)`` for each glider platform's latest fix (see
    :mod:`._gliders`). ``batch`` is the platform ``type`` (``xspar`` /
    ``seaglider``) — the same key its marker and track use, so the advection line
    rides the same instrument row. Gliders maneuver, so this is a passive-drift
    what-if (the surface current only), useful for their drift phases."""
    heads = []
    for p in gliders:
        _, lat, lon = p.fixes[-1]
        heads.append(({"id": p.id, "batch": p.type}, float(lon), float(lat)))
    return heads


def _advection_geojson(
    field: xr.Dataset, tracks: pd.DataFrame, gliders: list, direction: int
) -> dict:
    """FeatureCollection of one advection ``LineString`` per instrument (drifters
    and gliders), from its latest fix through ``field`` — ``direction`` +1 forward
    (forecast) or -1 backward (hindcast).

    Every instrument with a valid latest fix gets one (advection needs only a
    position). One whose head is already on land/off-grid yields no usable line
    (``<2`` vertices) and is skipped. Coordinates are ``[lon, lat]``. Properties:
    the head identity (``D_number`` for drifters, ``id`` for gliders), ``batch``
    (the instrument key its marker/track toggle under), ``valid_time``, and
    ``marks`` — the ``{hours, lon, lat}`` the integration reached (``hours`` signed
    by ``direction``).
    """
    sampler = _Field(field)
    # Anchor t=0 to the window time nearest now (the forecast's "present"); the
    # ~sub-hour gap to wall-clock now is immaterial. valid_time reports it.
    now = np.datetime64(
        datetime.now(timezone.utc).replace(tzinfo=None), "s"
    ).astype(np.float64)
    t0 = float(sampler.times[int(np.argmin(np.abs(sampler.times - now)))])
    valid = np.datetime_as_string(np.datetime64(int(round(t0)), "s"), unit="s") + "Z"

    features = []
    for props, lon, lat in _drifter_heads(tracks) + _glider_heads(gliders):
        coords, marks = _integrate(sampler, lon, lat, t0, direction)
        if len(coords) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {**props, "valid_time": valid, "marks": marks},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def forecast_geojson(
    field: xr.Dataset, tracks: pd.DataFrame, gliders: list | None = None
) -> dict:
    """Forward current-advection forecast to +6 h. See :func:`_advection_geojson`."""
    return _advection_geojson(field, tracks, gliders or [], direction=1)


def hindcast_geojson(
    field: xr.Dataset, tracks: pd.DataFrame, gliders: list | None = None
) -> dict:
    """Backward current-advection hindcast to -6 h: where the time-dependent field
    would have carried a particle into each instrument head over the past 6 h. A
    current-only back-trajectory, not the observed track. See
    :func:`_advection_geojson`."""
    return _advection_geojson(field, tracks, gliders or [], direction=-1)
