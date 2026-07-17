"""Shared geodesy primitives — one Earth radius, one haversine, one Coriolis
parameter, one m/s→deg/s conversion.

These were copy-pasted across :mod:`_forecast`, :mod:`_geojson`, :mod:`_deploy`,
:mod:`_vorticity`, and :mod:`_inertial` (the audit's IDIOM-1/3/4 and DER-4). Each
now has a single home here, so the constants can't drift apart and the
scalar/vectorized twins stay identical *because they are the same expression*, not
by coincidence.

Everything is spherical-Earth at :data:`EARTH_RADIUS_M` (the value the whole
codebase already used); the ~0.3–0.5 % ellipsoidal error is far below the CMEMS
1/12° grid resolution and the GPS-fix scatter these quantities feed.
"""
from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_M = 6_371_000.0  # spherical mean radius
OMEGA = 7.2921159e-5  # Earth's rotation rate (rad/s); the Coriolis f below is 2Ω sinφ
_DEG_PER_RAD = 180.0 / math.pi


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two ``(lat, lon)`` points (degrees).
    Scalar (``math``) — the per-segment track and detachment callers pass one pair at
    a time. A kilometre caller divides by 1000."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def coriolis(lat_deg):
    """Coriolis parameter ``f = 2Ω sin φ`` (rad/s) at latitude ``lat_deg`` (degrees) —
    **negative in the Southern Hemisphere**. numpy ufuncs, so a scalar or a lat array
    both work (the inertial decomposition and the ζ/f overlay pass arrays)."""
    return 2.0 * OMEGA * np.sin(np.radians(lat_deg))


def uv_to_deg_per_s(u, v, lat_deg):
    """Eastward/northward velocity ``(u, v)`` in m/s → ``(dlon/dt, dlat/dt)`` in deg/s
    at latitude ``lat_deg`` (degrees): ``dlat = v/R``, ``dlon = u/(R cos φ)``, scaled to
    degrees. numpy ufuncs, so it serves both the scalar RK4 step and its vectorized
    batch twin from one expression — the single source that makes their "bit-identical"
    property a fact rather than a maintained coincidence (IDIOM-3)."""
    dlat = v / EARTH_RADIUS_M * _DEG_PER_RAD
    dlon = u / (EARTH_RADIUS_M * np.cos(np.radians(lat_deg))) * _DEG_PER_RAD
    return dlon, dlat
