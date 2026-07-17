"""Time-dependent-field RK4 advection engine + clock anchoring, shared by the
interactive deployment API (:mod:`whirls_cruise_map._api`) and the field store.

Advect a passive particle through the **time-dependent** CMEMS surface-current
field: integrate ``dx/dt = u(x, y, t)``, ``dy/dt = v(x, y, t)`` with RK4, advancing
a clock alongside the position so the particle is pushed by the current *at each
moment* — the field sampled bilinearly in space and linearly in time over an hourly
window (:class:`_Field`, or its store-backed drop-in
:class:`whirls_cruise_map._field_store.StoreField`). Because the model already
carries the near-inertial oscillation at these latitudes, the path curls into the
inertial loop the drifters show rather than the straight streamline a single frozen
snapshot would give. A negative step integrates the same field backward.

:func:`_integrate` is the scalar per-seed path; :func:`_batch_advect` runs the same
RK4 over the same field for a whole batch at once in vectorized numpy, bit-identical
to the scalar path (the deployment API advects up to a couple thousand seeds per
request). :func:`_anchor_t0` resolves the advection clock's t = 0, and
:func:`_vertex_cadence_min` caps the emitted polyline vertex count on long horizons.

We integrate over the raw field — land kept as ``NaN`` — so the line carries correct
speeds and the integrator *stops* at the coast (or the window edge) instead of being
dragged across it.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import xarray as xr

from . import _geo, _time

# Integrate this far forward, with this RK4 sub-step and this polyline-vertex
# spacing (minutes). The marks (hours) and vertex spacing must divide the step
# evenly so each mark lands exactly on an emitted vertex; at ~0.5 m/s a particle
# moves ~1 grid cell in 6 h, so the scheme is not delicate.
HORIZON_H = 6.0
STEP_MIN = 5.0
VERTEX_MIN = 15.0
MARK_HOURS = (1, 3, 6)

# Display precision for every emitted coordinate (shared with the _geojson
# emitters): 4 dp is ~11 m — sub-pixel at the map's maxZoom 12 (~30 m/CSS-px at the
# working latitude), at the drifters' ~5–15 m GPS fix scatter, and three orders
# below the 1/12° CMEMS field driving the advection. 5 dp (~1.1 m, the source
# feed's own precision) buys nothing visible and costs ~1/3 of the gzipped
# forecast payload.
_COORD_NDIGITS = 4


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
    return _geo.uv_to_deg_per_s(u, v, lat)


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
# batch and a per-seed run agree to the last ULP (guarded by a test).
#
# ``direction`` (+1 forward, -1 backward) mirrors the scalar ``_integrate``'s ``dt``
# negation exactly (same sign, same place in the arithmetic — ``dt = direction *
# step_min * 60``): the interactive deployment API's backward runs walk the store
# in reverse and hand the same signed ``dt`` through, no other change. The build's
# per-instrument hindcast still uses the scalar path (unchanged, out of this
# stage's scope).


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
    dlon, dlat = _geo.uv_to_deg_per_s(u, v, lat_s)
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
    direction: int = 1,
    step_min: float = STEP_MIN,
    vertex_every: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized RK4 for a whole batch of seeds, advanced in step-index lockstep,
    ``direction`` +1 forward or -1 backward (mirroring the scalar ``_integrate``'s
    ``dt`` negation, ``_forecast.py`` ~line 191). ``lon0``/``lat0``/``t0`` (epoch
    seconds) and ``n_steps`` are per-seed arrays; ``n_steps[i] == 0`` marks a seed
    not to advect (out of window / no track). ``t0``'s clock runs forward for a
    forward batch and backward for a backward one — the caller (a store-backed
    field walking its day files in reverse, or a fixed in-RAM window) supplies
    ``field`` already covering whichever direction the seeds travel.

    Integration always advances at the fine ``step_min`` sub-step (RK4 accuracy), but
    only every ``vertex_every``-th sub-step is *stored* — the sole consumer reads the
    trajectory at that vertex cadence, so materialising every intermediate sub-step
    would allocate a buffer up to ``vertex_every``x larger than anything read (FC-1).
    The default ``vertex_every == 1`` stores every sub-step: the dense shape the scalar
    bit-identity checks compare against.

    Returns ``(P, completed)``: ``P`` is ``(N, V+1, 2)`` full-precision positions where
    ``V = Kmax // vertex_every``, ``P[:, 0]`` the heads and ``P[:, v]`` the position at
    sub-step ``v * vertex_every`` (``step_min`` apart, signed by ``direction``);
    ``completed[i]`` is seed ``i``'s last good *stored-vertex* index (== its last good
    sub-step index when ``vertex_every == 1``). A seed freezes — its position held, its
    ``completed`` frozen — at its own ``n_steps`` or the first sub-step any RK4 stage
    samples land/edge/window (``NaN``), i.e. exactly where the scalar :func:`_integrate`
    truncates with ``break``. The caller reads coords off ``P`` up to ``completed`` (one
    vertex per stored row) to build features.

    Each step samples the field for only the seeds still ``stepping`` (a boolean
    gather/scatter on the arrays, not a full-array recompute masked afterward) — a
    frozen seed's already-decided position never asks the field for its old time
    again. For an in-RAM field this is just an avoided flop; for a store-backed
    field (:class:`whirls_cruise_map._field_store.StoreField`) it matters more:
    that field's day cache assumes the batch's live working set spans only the
    couple of calendar days the still-advecting seeds are currently walking (see
    ``_field_store._DayArrayCache``), an assumption a full-array recompute would
    break the moment any seed freezes on a calendar day the rest have since moved
    past — permanently pinning that stale day into every later step's gather and
    thrashing a small cap. Batches over a store-backed field commonly run seeds to
    truncation at scattered times (a coastline near a seed's start, or a seed
    simply reaching its own horizon early), so this isn't a corner case.
    """
    n = lon0.size
    dt = direction * step_min * 60.0
    alive0 = n_steps > 0
    k_max = int(n_steps.max()) if alive0.any() else 0
    n_vertices = k_max // vertex_every  # stored rows after the head row

    positions = np.empty((n, n_vertices + 1, 2), dtype=np.float64)
    positions[:, 0, 0] = lon0
    positions[:, 0, 1] = lat0

    lon = lon0.astype(np.float64, copy=True)
    lat = lat0.astype(np.float64, copy=True)
    t = t0.astype(np.float64, copy=True)
    active = alive0.copy()
    completed = np.zeros(n, dtype=int)  # last good SUB-STEP index (fine-grained)

    for step in range(1, k_max + 1):
        stepping = active & (step <= n_steps)
        if not stepping.any():
            break  # every remaining seed has reached its horizon
        idx = np.flatnonzero(stepping)
        lon_n, lat_n = _vec_rk4_step(field, lon[idx], lat[idx], t[idx], dt)
        ok = np.isfinite(lon_n) & np.isfinite(lat_n)
        ok_idx = idx[ok]
        lon[ok_idx] = lon_n[ok]
        lat[ok_idx] = lat_n[ok]
        t[ok_idx] += dt
        completed[ok_idx] = step
        # Freeze a seed that failed this step (hit coast/edge/window) or just reached
        # its own horizon — mirroring the scalar path's break-and-stop.
        active[idx[~ok]] = False
        active[ok_idx[n_steps[ok_idx] == step]] = False
        # Store only at the vertex cadence (every seed's current position, frozen ones
        # held) — the intermediate sub-steps are integrated but never materialised.
        if step % vertex_every == 0:
            v = step // vertex_every
            positions[:, v, 0] = lon
            positions[:, v, 1] = lat

    return positions, completed // vertex_every


# --- adaptive vertex cadence ---------------------------------------------------
#
# The build's ±6 h instrument forecast always emits a vertex every ``VERTEX_MIN``
# (15 min) — short enough that the polyline never grows past a few dozen points.
# The interactive deployment API's long runs (up to the 240 h request cap, or a
# multi-day virtual deployment once workstream B lands) would not: 15 min vertices
# over 25 days is 2400 points, most of it wasted resolution the map can't render
# any differently. :func:`_vertex_cadence_min` widens the cadence just enough to
# cap the vertex count, staying at the fine 15 min cadence for anything short
# enough to afford it (48 h -> 193 vertices, comfortably under the cap already).


def _vertex_cadence_min(
    horizon_h: float, *, base_min: float = VERTEX_MIN, max_vertices: int = 400
) -> float:
    """The smallest multiple of ``base_min`` minutes such that a ``horizon_h``-long
    track sampled at that cadence carries at most ``max_vertices`` vertices (the
    head plus one per cadence step). Monotone in ``horizon_h``: short horizons stay
    at ``base_min`` (48 h -> 15 min, ``floor(48*60/15) + 1 = 193`` vertices); longer
    ones widen one ``base_min`` multiple at a time until the count fits. The result
    is always a multiple of ``base_min``, so a caller that also emits marks at
    multiples of this cadence keeps every mark landing exactly on an emitted vertex
    (the same divisibility invariant :func:`_integrate` relies on for its fixed
    ``vertex_min``)."""
    cadence = base_min
    while math.floor(horizon_h * 60.0 / cadence) + 1 > max_vertices:
        cadence += base_min
    return cadence


def _anchor_t0(sampler: _Field, t0: float | None = None) -> tuple[float, str]:
    """The advection clock's t = 0 (epoch seconds) and its ISO-8601 ``valid_time``.

    Default (``t0=None``): the window time nearest wall-clock now — the forecast's
    "present"; the ~sub-hour gap to now is immaterial. The interactive API passes an
    explicit ``t0`` instead —
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
    valid = _time.iso_z_from_epoch(t0)
    return t0, valid
