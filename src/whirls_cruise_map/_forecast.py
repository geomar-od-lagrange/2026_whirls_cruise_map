"""Current-advection forecast and hindcast: advect a passive particle through the
**time-dependent** CMEMS surface-current field from each instrument's latest fix —
the drifters and the glider-group platforms (XSPAR buoy, seagliders, floats)
alike — forward for the forecast, backward for the hindcast. These non-drifter
platforms don't move purely with the surface current (gliders maneuver, floats
park and profile at depth), so their advection is a passive-drift what-if
(surface current only), meaningful for their drift phases rather than a track
prediction.

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
magnitude-compressed ``currents_+NNh.json`` frames — so the line carries correct speeds and the
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
    field: _Field,
    lon0: float,
    lat0: float,
    t0: float,
    direction: int = 1,
    *,
    horizon_h: float = HORIZON_H,
    step_min: float = STEP_MIN,
    vertex_min: float = VERTEX_MIN,
    mark_hours: tuple = MARK_HOURS,
) -> tuple[list[list[float]], list[dict]]:
    """Advect from ``(lon0, lat0)`` at clock time ``t0`` (epoch seconds) to
    ``horizon_h``, ``direction`` +1 forward (forecast) or -1 backward (hindcast),
    returning the polyline ``coords`` (a vertex every ``vertex_min``, starting at
    the head) and the ``marks`` actually reached (``{hours, lon, lat}`` at each
    ``mark_hours``, ``hours`` signed by ``direction``). The clock advances with the
    integration, so the particle is pushed by the current at each moment. Stops
    early at the coast/edge/window end; marks beyond the truncation are omitted.

    The four cadence knobs default to the module constants (the build's ±6 h,
    1/3/6 h forecast); the interactive deployment API passes its own (e.g. a +48 h
    horizon, marks every 3 h). ``mark_hours`` and ``vertex_min`` must divide ``step_min``
    evenly so each mark lands on an emitted vertex."""
    dt = direction * step_min * 60.0
    n_steps = round(horizon_h * 60.0 / step_min)
    vertex_every = round(vertex_min / step_min)
    mark_at = {round(h * 60.0 / step_min): direction * h for h in mark_hours}

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


# --- vectorized batch advection ----------------------------------------------
#
# The scalar path above advects one particle at a time — fine for the build's few
# instrument heads, but the deployment API advects up to a couple thousand seeds
# per request, where the pure-Python per-seed loop dominates walltime (~23 ms/seed
# → ~46 s at the 2000-seed cap, against a 60 s gateway timeout). The functions
# below do the *same* RK4 over the *same* field for a whole batch at once, in
# vectorized numpy: all seeds advance together in step-index lockstep, each stage
# sampling the field for every still-active seed in one gather. This is ~40× faster
# (n=2000 in ~1.2 s) and, by construction, **bit-identical** to the scalar path —
# the arithmetic order (corner-sum, time-lerp, RK4 combine, cos(radians(lat))) and
# the land/edge/window rules mirror ``_Field.velocity`` + ``_deriv`` exactly, so a
# batch and a per-seed run agree to the last ULP (guarded by a test). Forward-only:
# the build's backward hindcast keeps the scalar path.


def _vec_deriv(
    field: _Field, lon: np.ndarray, lat: np.ndarray, t: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized twin of :func:`_deriv`: ``(dlon/dt, dlat/dt)`` in deg/s for the
    seed arrays ``lon``/``lat``/``t`` (epoch seconds), ``NaN`` wherever the scalar
    :meth:`_Field.velocity`/:func:`_deriv` would return ``None`` — off-grid, outside
    the time window, or on a cell with any land (``NaN``) corner at either bracketing
    time. Reuses ``field``'s grid arrays in place (no copy).

    Mirrors the scalar sampler exactly so a batch advects bit-identically: bilinear
    corner order ``w00·c00 + w10·c10 + w01·c01 + w11·c11``, linear time lerp
    ``(1-wt)·lo + wt·hi``, and the land rule falling out of NaN propagation
    (``w·NaN = NaN``, so any land corner voids the sample, as ``_bilin`` does). Any
    future change to :meth:`_Field.velocity` must be mirrored here — a test pins the
    two together."""
    lons, lats, times = field.lons, field.lats, field.times
    nlon, nlat, nt = lons.size, lats.size, times.size

    # Sanitize non-finite inputs to in-range dummies so searchsorted/gather never
    # fault; the ``valid`` mask below (and NaN propagation) discards them anyway.
    finite = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(t)
    lon_s = np.where(finite, lon, lons[0])
    lat_s = np.where(finite, lat, lats[0])
    t_s = np.where(finite, t, times[0])

    ix = np.searchsorted(lons, lon_s, side="right") - 1
    iy = np.searchsorted(lats, lat_s, side="right") - 1
    valid = (
        finite
        & (ix >= 0) & (ix < nlon - 1)
        & (iy >= 0) & (iy < nlat - 1)
        & (t >= times[0]) & (t <= times[-1])
    )
    ixc = np.clip(ix, 0, nlon - 2)
    iyc = np.clip(iy, 0, nlat - 2)

    x0 = lons[ixc]
    y0 = lats[iyc]
    tx = (lon_s - x0) / (lons[ixc + 1] - x0)
    ty = (lat_s - y0) / (lats[iyc + 1] - y0)
    w00 = (1 - tx) * (1 - ty)
    w10 = tx * (1 - ty)
    w01 = (1 - tx) * ty
    w11 = tx * ty

    jt = np.clip(np.searchsorted(times, t_s, side="right") - 1, 0, nt - 2)
    t0 = times[jt]
    t1 = times[jt + 1]
    wt = np.where(t1 == t0, 0.0, (t_s - t0) / (t1 - t0))

    def _sample(plane: np.ndarray) -> np.ndarray:
        # Bilinear-in-space then linear-in-time over one component (u or v). Corners
        # gathered per bracketing time slice; a land NaN corner propagates to NaN.
        def bilin(jj: np.ndarray) -> np.ndarray:
            c00 = plane[jj, iyc, ixc]
            c10 = plane[jj, iyc, ixc + 1]
            c01 = plane[jj, iyc + 1, ixc]
            c11 = plane[jj, iyc + 1, ixc + 1]
            return w00 * c00 + w10 * c10 + w01 * c01 + w11 * c11

        return (1 - wt) * bilin(jt) + wt * bilin(jt + 1)

    u = _sample(field.u)
    v = _sample(field.v)
    bad = ~valid | ~np.isfinite(u) | ~np.isfinite(v)
    dlat = v / _EARTH_RADIUS_M * (180.0 / math.pi)
    dlon = u / (_EARTH_RADIUS_M * np.cos(np.radians(lat_s))) * (180.0 / math.pi)
    return np.where(bad, np.nan, dlon), np.where(bad, np.nan, dlat)


def _vec_rk4_step(
    field: _Field, lon: np.ndarray, lat: np.ndarray, t: np.ndarray, dt: float
) -> tuple[np.ndarray, np.ndarray]:
    """One vectorized RK4 step of ``dt`` seconds for a whole batch (stages sample at
    ``t``, ``t+dt/2``, ``t+dt``); a seed's next position is ``NaN`` if any stage
    samples land/edge/window (the caller freezes it there, mirroring the scalar
    ``_rk4_step`` returning ``None``)."""
    k1x, k1y = _vec_deriv(field, lon, lat, t)
    k2x, k2y = _vec_deriv(field, lon + 0.5 * dt * k1x, lat + 0.5 * dt * k1y, t + 0.5 * dt)
    k3x, k3y = _vec_deriv(field, lon + 0.5 * dt * k2x, lat + 0.5 * dt * k2y, t + 0.5 * dt)
    k4x, k4y = _vec_deriv(field, lon + dt * k3x, lat + dt * k3y, t + dt)
    lon_n = lon + dt / 6.0 * (k1x + 2 * k2x + 2 * k3x + k4x)
    lat_n = lat + dt / 6.0 * (k1y + 2 * k2y + 2 * k3y + k4y)
    return lon_n, lat_n


def _batch_advect(
    field: _Field,
    lon0: np.ndarray,
    lat0: np.ndarray,
    t0: np.ndarray,
    n_steps: np.ndarray,
    *,
    step_min: float = STEP_MIN,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward vectorized RK4 for a whole batch of seeds, advanced in step-index
    lockstep. ``lon0``/``lat0``/``t0`` (epoch seconds) and ``n_steps`` are per-seed
    arrays; ``n_steps[i] == 0`` marks a seed not to advect (out of window / no track).

    Returns ``(P, completed)``: ``P`` is ``(N, Kmax+1, 2)`` full-precision positions
    with ``P[:, 0]`` the heads and each later row a sub-step (``step_min`` apart);
    ``completed[i]`` is seed ``i``'s last good step index. A seed freezes — its
    position held, its ``completed`` frozen — at its own ``n_steps`` or the first step
    any RK4 stage samples land/edge/window (``NaN``), i.e. exactly where the scalar
    :func:`_integrate` truncates with ``break``. The caller reads coords/marks off
    ``P`` up to ``completed`` (rounding, vertex cadence, mark steps) to build features.
    """
    n = lon0.size
    dt = step_min * 60.0  # forward only
    alive0 = n_steps > 0
    k_max = int(n_steps.max()) if alive0.any() else 0

    positions = np.empty((n, k_max + 1, 2), dtype=np.float64)
    positions[:, 0, 0] = lon0
    positions[:, 0, 1] = lat0

    lon = lon0.astype(np.float64, copy=True)
    lat = lat0.astype(np.float64, copy=True)
    t = t0.astype(np.float64, copy=True)
    active = alive0.copy()
    completed = np.zeros(n, dtype=int)

    for step in range(1, k_max + 1):
        stepping = active & (step <= n_steps)
        if not stepping.any():
            break  # every remaining seed has reached its horizon
        lon_n, lat_n = _vec_rk4_step(field, lon, lat, t, dt)
        ok = stepping & np.isfinite(lon_n) & np.isfinite(lat_n)
        lon = np.where(ok, lon_n, lon)
        lat = np.where(ok, lat_n, lat)
        t = np.where(ok, t + dt, t)
        completed = np.where(ok, step, completed)
        # Freeze a seed that failed this step (hit coast/edge/window) or just reached
        # its own horizon — mirroring the scalar path's break-and-stop.
        active = active & ~(stepping & ~ok) & ~(ok & (step == n_steps))
        positions[:, step, 0] = lon
        positions[:, step, 1] = lat

    return positions, completed


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
    """``(properties, lon, lat)`` for each glider-group platform's latest fix (see
    :mod:`._gliders`, includes floats). ``batch`` is the platform ``type``
    (``xspar`` / ``seaglider`` / ``float``) — the same key its marker and track
    use, so the advection line rides the same instrument row. These platforms
    don't drift purely with the surface current, so this is a passive-drift
    what-if (the surface current only), useful for their drift phases."""
    heads = []
    for p in gliders:
        _, lat, lon = p.fixes[-1]
        heads.append(({"id": p.id, "batch": p.type}, float(lon), float(lat)))
    return heads


def _anchor_t0(sampler: _Field, t0: float | None = None) -> tuple[float, str]:
    """The advection clock's t = 0 (epoch seconds) and its ISO-8601 ``valid_time``.

    Default (``t0=None``): the window time nearest wall-clock now — the forecast's
    "present"; the ~sub-hour gap to now is immaterial. The build's per-instrument
    advection uses this. The interactive API passes an explicit ``t0`` instead —
    e.g. the displayed CMEMS snapshot's time, so a clicked forecast starts at the
    same instant as the field shown on the map. The field interpolates linearly in
    time, so ``t0`` need not fall on a window grid time; a caller that accepts a
    user-supplied time should first check it lies within the window
    (``sampler.times[0]..[-1]``)."""
    if t0 is None:
        now = np.datetime64(
            datetime.now(timezone.utc).replace(tzinfo=None), "s"
        ).astype(np.float64)
        t0 = float(sampler.times[int(np.argmin(np.abs(sampler.times - now)))])
    valid = np.datetime_as_string(np.datetime64(int(round(t0)), "s"), unit="s") + "Z"
    return t0, valid


def _advection_feature(
    sampler: _Field,
    props: dict,
    lon: float,
    lat: float,
    t0: float,
    valid: str,
    direction: int = 1,
    *,
    horizon_h: float = HORIZON_H,
    mark_hours: tuple = MARK_HOURS,
) -> dict | None:
    """One advection ``LineString`` Feature from ``(lon, lat)`` at ``t0`` through
    ``sampler``, or ``None`` if the head is on land/off-grid (a ``<2``-vertex line).
    ``props`` is merged into the properties beside ``valid_time`` and the signed
    ``marks``. Shared by :func:`_advection_geojson` (instrument heads, module
    defaults) and the interactive point-forecast API (a clicked position, its own
    horizon/marks)."""
    coords, marks = _integrate(
        sampler, lon, lat, t0, direction, horizon_h=horizon_h, mark_hours=mark_hours
    )
    if len(coords) < 2:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {**props, "valid_time": valid, "marks": marks},
    }


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
    t0, valid = _anchor_t0(sampler)
    features = []
    for props, lon, lat in _drifter_heads(tracks) + _glider_heads(gliders):
        feature = _advection_feature(sampler, props, lon, lat, t0, valid, direction)
        if feature is not None:
            features.append(feature)
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
