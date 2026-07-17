"""Detect each drifter's deployment as its detachment from the vessel.

A drifter's fix history covers port staging and the transit leg where it was
still aboard (or alongside) the ship before it went into the water. Those fixes
are not a free drift. Using the vessel track, find where each drifter left the
vessel's vicinity and report the time of its first free-drift fix, so the
trajectory can be truncated there (see :func:`_geojson.tracks_geojson`).

The scan walks each drifter's fixes in time order and stops at its first
**clear departure**: consecutive fixes beyond :data:`DETACHED_KM` after the
drifter has been within :data:`NEAR_SHIP_KM`. Once a drifter has been that
clearly away it is deployed for good, and later close passes (the vessel works
among its own drifters routinely) can never re-truncate the established free
track. Two kinds of distance noise are deliberately inert: far fixes *before*
the first near fix (drifters sit in the staging port days before the vessel
arrives, so a far pre-history does not mean already deployed), and a *lone* far
fix amid near ones (a GPS outlier must not end the attached leg early and leak
the remaining transit into the free track — hence *consecutive*). Within the
attached leg the rule is deliberately conservative — exactness of the deployment
instant does not matter, but leaking a vessel-following fix into the free track
does — so the cut is placed after the *last* fix within :data:`NEAR_SHIP_KM` of
the vessel: everything kept up to the departure is beyond that distance, while
fixes after it are kept regardless (a later close pass is part of the free
drift).
"""
from __future__ import annotations

import bisect

import pandas as pd

from . import _geo

# Within this distance a drifter is treated as still attached to the vessel (on
# deck / alongside). Comfortably above GPS + ship-length scatter (~0.1 km seen on
# deck), far below deployed separations (5+ km).
NEAR_SHIP_KM = 1.0
# Beyond this distance on consecutive fixes (after having been near) a drifter
# is deployed for good: the attachment scan stops there, so a later close pass
# cannot re-truncate the free track. Comfortably above any ship-track
# interpolation scatter seen while a drifter is aboard, and matched to the
# separation a genuinely deployed drifter reaches (5+ km).
DETACHED_KM = 5.0
_EARTH_RADIUS_KM = _geo.EARTH_RADIUS_M / 1000.0  # single source: the shared radius, in km


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km — the shared metre haversine over 1000."""
    return _geo.haversine_m(lat1, lon1, lat2, lon2) / 1000.0


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

    Fixes are scanned in time order up to the drifter's first clear departure —
    consecutive fixes beyond :data:`DETACHED_KM` after a fix within
    :data:`NEAR_SHIP_KM` — which freezes the cut: nothing later (in particular
    a close pass by the vessel) can re-truncate the established free track. A
    drifter's key is present only when a cut is warranted:

    - **detached** — the drifter was within :data:`NEAR_SHIP_KM` and a later
      fix exists: the value is the time of the first fix after the last
      attached one.
    - **still attached** — the latest fix is within :data:`NEAR_SHIP_KM` (and
      no clear departure happened before it): the value is a time just past the
      last fix, so the free track is empty and the drifter draws no trajectory
      (it is not freely drifting yet).

    A drifter never within :data:`NEAR_SHIP_KM` of the vessel is **absent**
    from the map — no basis to truncate, so its full track is kept. An empty
    ``ship_track`` yields an empty map (no truncation anywhere).
    """
    if not ship_track:
        return {}
    ship = _ShipTrack(ship_track)
    starts: dict[str, pd.Timestamp] = {}
    for d_number, group in tracks.sort_values("date_UTC").groupby("D_number"):
        rows = list(group.itertuples(index=False))
        dists = []
        for row in rows:
            slat, slon = ship.at(row.date_UTC)
            dists.append(_haversine_km(row.Latitude, row.Longitude, slat, slon))
        last_attached = None
        for i, dist in enumerate(dists):
            if dist <= NEAR_SHIP_KM:
                last_attached = i
            elif (
                last_attached is not None
                and dist > DETACHED_KM
                and i + 1 < len(dists)
                and dists[i + 1] > DETACHED_KM
            ):
                # Clear departure: deployed for good, the cut is frozen — a
                # later close pass by the vessel cannot re-truncate the track.
                # A lone far fix (GPS outlier) does not qualify, and neither do
                # far fixes before the first near one (port staging while the
                # vessel is still elsewhere).
                break
        if last_attached is None:
            continue  # never near the vessel — keep the full track
        if last_attached + 1 < len(rows):
            starts[d_number] = rows[last_attached + 1].date_UTC
        else:
            # Still attached at the latest fix: cut past the end (empty free track).
            starts[d_number] = rows[-1].date_UTC + pd.Timedelta(seconds=1)
    return starts
