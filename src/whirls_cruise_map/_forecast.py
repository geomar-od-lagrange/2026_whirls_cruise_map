"""Current-advection forecast and hindcast: advect a passive particle through the
frozen CMEMS surface-current field from each instrument's latest fix — the
drifters and the gliders (XSPAR buoy, seagliders) alike — forward for the
forecast, backward for the hindcast. Gliders maneuver actively, so their
advection is a passive-drift what-if (surface current only), meaningful for their
drift phases rather than a track prediction.

This is a **streamline of the present field**: starting at the instrument head we
integrate ``dx/dt = u(x, y)``, ``dy/dt = v(x, y)`` with RK4 to ±6 h and draw the
path, marking the 1 / 3 / 6 h positions on each side. It is the quantitative
version of what the animated flow trails show qualitatively — same field, but the
*true* ``uo``/``vo`` (m/s, native grid), so distances are physically meaningful.
The hindcast integrates the same field backward (negative step); it is a
current-only back-trajectory, **not** the drifter's observed past track (that is
the trajectory line from :func:`whirls_cruise_map._geojson.tracks_geojson`).

It is **not** a calibrated drifter prediction:

- **Frozen field.** One CMEMS snapshot is held fixed (the dataset is 6-hourly).
  1 h and 3 h sit comfortably inside one field step; the 6 h mark spans ~one full
  step and is the edge of what a frozen field supports — hence a marked horizon.
- **Surface current only.** ``uo``/``vo`` are the modelled surface current; no
  windage / Stokes drift (undrogued) or deeper-layer sampling (drogued). So this
  is an indicative passive-tracer track, not the drifter's predicted path.

We integrate over the raw field — land kept as ``NaN`` (see
:func:`whirls_cruise_map._currents.fetch_field`), not the trails' land-filled,
magnitude-compressed ``currents.json`` — so the line carries correct speeds and
the integrator *stops* at the coast instead of being dragged across it. See
``docs/forecast.md``.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import xarray as xr

from . import _currents

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
    """Bilinear sampler over the frozen current field.

    Holds ``uo``/``vo`` on an ascending lat/lon grid with land kept as ``NaN``.
    :meth:`velocity` returns ``None`` outside the grid or where any of the four
    surrounding cells is land, which is what lets the integrator stop at the
    coast and at the field edge.
    """

    def __init__(self, field: xr.Dataset):
        f = field.sortby("latitude").sortby("longitude")  # both ascending
        self.lons = f["longitude"].values.astype(float)
        self.lats = f["latitude"].values.astype(float)
        self.u = f["uo"].transpose("latitude", "longitude").values
        self.v = f["vo"].transpose("latitude", "longitude").values

    def velocity(self, lon: float, lat: float) -> tuple[float, float] | None:
        """Bilinear ``(uo, vo)`` in m/s at ``(lon, lat)``; ``None`` off-grid or on
        a cell with any land (``NaN``) corner."""
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

        def bilin(a) -> float:
            corners = (a[iy, ix], a[iy, ix + 1], a[iy + 1, ix], a[iy + 1, ix + 1])
            total = 0.0
            for wi, ci in zip(w, corners):
                if math.isnan(ci):
                    return math.nan  # any land corner -> undefined here
                total += wi * ci
            return total

        u = bilin(self.u)
        v = bilin(self.v)
        if math.isnan(u) or math.isnan(v):
            return None
        return u, v


def _deriv(field: _Field, lon: float, lat: float) -> tuple[float, float] | None:
    """``(dlon/dt, dlat/dt)`` in deg/s at ``(lon, lat)``, or ``None`` on
    land/edge. ``dlat = v/R``, ``dlon = u/(R cos lat)``, scaled to degrees."""
    vel = field.velocity(lon, lat)
    if vel is None:
        return None
    u, v = vel
    dlat = v / _EARTH_RADIUS_M * (180.0 / math.pi)
    dlon = u / (_EARTH_RADIUS_M * math.cos(math.radians(lat))) * (180.0 / math.pi)
    return dlon, dlat


def _rk4_step(
    field: _Field, lon: float, lat: float, dt: float
) -> tuple[float, float] | None:
    """One RK4 step of ``dt`` seconds, or ``None`` if any stage samples land/edge
    (so the caller truncates the path at the last good point)."""
    k1 = _deriv(field, lon, lat)
    if k1 is None:
        return None
    k2 = _deriv(field, lon + 0.5 * dt * k1[0], lat + 0.5 * dt * k1[1])
    if k2 is None:
        return None
    k3 = _deriv(field, lon + 0.5 * dt * k2[0], lat + 0.5 * dt * k2[1])
    if k3 is None:
        return None
    k4 = _deriv(field, lon + dt * k3[0], lat + dt * k3[1])
    if k4 is None:
        return None
    lon_n = lon + dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
    lat_n = lat + dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    return lon_n, lat_n


def _integrate(
    field: _Field, lon0: float, lat0: float, direction: int = 1
) -> tuple[list[list[float]], list[dict]]:
    """Advect from ``(lon0, lat0)`` to :data:`HORIZON_H`, ``direction`` +1 forward
    (forecast) or -1 backward (hindcast), returning the polyline ``coords`` (a
    vertex every :data:`VERTEX_MIN`, starting at the head) and the ``marks``
    actually reached (``{hours, lon, lat}`` at each :data:`MARK_HOURS`, ``hours``
    signed by ``direction``). Stops early at the coast/edge; marks beyond the
    truncation are omitted."""
    dt = direction * STEP_MIN * 60.0
    n_steps = round(HORIZON_H * 60.0 / STEP_MIN)
    vertex_every = round(VERTEX_MIN / STEP_MIN)
    mark_at = {round(h * 60.0 / STEP_MIN): direction * h for h in MARK_HOURS}

    coords = [[round(lon0, _COORD_NDIGITS), round(lat0, _COORD_NDIGITS)]]
    marks: list[dict] = []
    lon, lat = lon0, lat0
    for step in range(1, n_steps + 1):
        nxt = _rk4_step(field, lon, lat, dt)
        if nxt is None:
            break  # hit the coast or the field edge — truncate here
        lon, lat = nxt
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
    valid = _currents.valid_time(field)

    features = []
    for props, lon, lat in _drifter_heads(tracks) + _glider_heads(gliders):
        coords, marks = _integrate(sampler, lon, lat, direction)
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
    """Backward current-advection hindcast to -6 h: where the present frozen field
    would have carried a particle into each instrument head. A current-only
    back-trajectory, not the observed track. See :func:`_advection_geojson`."""
    return _advection_geojson(field, tracks, gliders or [], direction=-1)
