"""Detect each drifter's deployment as its detachment from the vessel.

A drifter's fix history covers port staging and the transit leg where it was
still aboard (or alongside) the ship before it went into the water. Those fixes
are not a free drift. Using the vessel track, find where each drifter left the
vessel's vicinity and report the time of its first free-drift fix, so the
trajectory can be truncated there (see :func:`_geojson.tracks_geojson`).

The rule is deliberately conservative — exactness of the deployment instant does
not matter, but leaking a vessel-following fix into the free track does — so the
cut is placed after the *last* fix within :data:`NEAR_SHIP_KM` of the vessel:
everything kept is, by construction, beyond that distance.
"""
from __future__ import annotations

import bisect
import math

import pandas as pd

# Within this distance a drifter is treated as still attached to the vessel (on
# deck / alongside). Comfortably above GPS + ship-length scatter (~0.1 km seen on
# deck), far below deployed separations (5+ km).
NEAR_SHIP_KM = 1.0
_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class _ShipTrack:
    """Linear interpolation of vessel position to an arbitrary time, clamped to
    the track ends (a fix outside the vessel window takes the nearest endpoint —
    which reads as *far* from any deployment site, so it never spuriously counts
    as attached)."""

    def __init__(self, track: list[tuple]):
        self._t = [f[0].timestamp() for f in track]
        self._lat = [f[1] for f in track]
        self._lon = [f[2] for f in track]

    def at(self, when: pd.Timestamp) -> tuple[float, float]:
        t = when.timestamp()
        if t <= self._t[0]:
            return self._lat[0], self._lon[0]
        if t >= self._t[-1]:
            return self._lat[-1], self._lon[-1]
        i = bisect.bisect_left(self._t, t)
        t0, t1 = self._t[i - 1], self._t[i]
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return (
            self._lat[i - 1] + f * (self._lat[i] - self._lat[i - 1]),
            self._lon[i - 1] + f * (self._lon[i] - self._lon[i - 1]),
        )


def deployment_starts(
    tracks: pd.DataFrame, ship_track: list[tuple]
) -> dict[str, pd.Timestamp]:
    """Map each drifter to the time of its first free-drift fix.

    A drifter's key is present only when a cut is warranted:

    - **detached** — some fix is within :data:`NEAR_SHIP_KM` and a later fix is
      not: the value is the time of the first fix after the last attached one.
    - **still attached** — every fix (through the latest) is within range: the
      value is a time just past the last fix, so the free track is empty and the
      drifter draws no trajectory (it is not freely drifting yet).

    A drifter never seen near the vessel is **absent** from the map — no basis to
    truncate, so its full track is kept. An empty ``ship_track`` yields an empty
    map (no truncation anywhere).
    """
    if not ship_track:
        return {}
    ship = _ShipTrack(ship_track)
    starts: dict[str, pd.Timestamp] = {}
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        rows = list(group.itertuples(index=False))
        last_attached = None
        for i, row in enumerate(rows):
            slat, slon = ship.at(row.date_UTC)
            if _haversine_km(row.Latitude, row.Longitude, slat, slon) <= NEAR_SHIP_KM:
                last_attached = i
        if last_attached is None:
            continue  # never near the vessel — keep the full track
        if last_attached + 1 < len(rows):
            starts[d_number] = rows[last_attached + 1].date_UTC
        else:
            # Still attached at the latest fix: cut past the end (empty free track).
            starts[d_number] = rows[-1].date_UTC + pd.Timedelta(seconds=1)
    return starts
