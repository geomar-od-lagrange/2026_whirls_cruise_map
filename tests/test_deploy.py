"""Unit tests for ``_deploy.deployment_starts``: the cut sits after the last
fix within ``NEAR_SHIP_KM`` of the vessel, scanned only up to the drifter's
first clear departure — consecutive fixes beyond ``DETACHED_KM`` after a near
fix — which freezes the cut, so a later close pass by the vessel never
re-truncates an established free track (regression: issue #10, D-433/D-434
blanked by a ship re-approach). Far fixes before the first near one (port
staging while the vessel is still elsewhere) and lone far outliers amid near
fixes are inert.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from whirls_cruise_map import _deploy


def _ts(*args) -> pd.Timestamp:
    return pd.Timestamp(datetime(*args, tzinfo=timezone.utc))


def _tracks(fixes: list[tuple]) -> pd.DataFrame:
    """Build a tracks frame from ``(D_number, time, latitude)`` tuples. All
    fixes sit on the prime meridian, so drifter–vessel distance is just the
    latitude offset from the (pinned) vessel: 1 deg lat ~ 111 km."""
    return pd.DataFrame(
        [(d, t, lat, 0.0) for d, t, lat in fixes],
        columns=["D_number", "date_UTC", "Latitude", "Longitude"],
    )


# Vessel pinned at the origin for the whole test window.
SHIP = [
    (_ts(2026, 7, 1, 0), 0.0, 0.0),
    (_ts(2026, 7, 10, 0), 0.0, 0.0),
]

# Latitude offsets from the vessel, chosen against the two thresholds:
NEAR = 0.005  # ~0.6 km  — within NEAR_SHIP_KM (attached)
MID = 0.02  # ~2.2 km  — beyond NEAR_SHIP_KM, below DETACHED_KM
FAR = 0.1  # ~11 km   — beyond DETACHED_KM (clearly deployed)


def test_normal_deployment_cuts_after_attached_leg():
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), NEAR),
                ("D1", _ts(2026, 7, 1, 12), MID),
                ("D1", _ts(2026, 7, 1, 18), FAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 1, 12)}


def test_reapproach_after_clear_departure_keeps_cut_frozen():
    """Regression #10: a drifter well into its free drift (beyond DETACHED_KM)
    comes back within NEAR_SHIP_KM of the vessel — the cut must not jump to the
    re-approach and blank the established track."""
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), MID),
                ("D1", _ts(2026, 7, 1, 12), FAR),
                ("D1", _ts(2026, 7, 2, 0), 2 * FAR),
                ("D1", _ts(2026, 7, 3, 0), NEAR),  # ship works nearby again
                ("D1", _ts(2026, 7, 3, 6), MID),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 1, 6)}


def test_still_attached_cut_sits_past_last_fix():
    last = _ts(2026, 7, 1, 12)
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), NEAR),
                ("D1", last, NEAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": last + pd.Timedelta(seconds=1)}


def test_never_near_vessel_is_absent():
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), MID),
                ("D1", _ts(2026, 7, 1, 6), 2 * MID),
            ]
        ),
        SHIP,
    )
    assert starts == {}


def test_excursion_inside_attached_window_is_swallowed():
    """A 1–5 km excursion during the attached leg (ship-track interpolation
    scatter) followed by a return near the vessel: the conservative rule still
    holds inside the window — the cut lands after the *last* near fix."""
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), MID),  # excursion, still attached
                ("D1", _ts(2026, 7, 1, 12), NEAR),
                ("D1", _ts(2026, 7, 1, 18), FAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 1, 18)}


def test_far_prehistory_then_attachment_still_cuts():
    """Drifters sit in the staging port days before the vessel arrives, so
    their histories open far (>5 km) from the ship. That pre-history is not a
    departure — the drifter has not been near yet — and must neither freeze the
    cut nor stop the later attached leg from being truncated (regression: 52
    real drifters lost their cut when a far start closed the window at index 0).
    """
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), FAR),  # in port, ship still en route
                ("D1", _ts(2026, 7, 1, 6), FAR),
                ("D1", _ts(2026, 7, 1, 12), NEAR),  # loaded aboard
                ("D1", _ts(2026, 7, 1, 18), NEAR),
                ("D1", _ts(2026, 7, 2, 0), MID),
                ("D1", _ts(2026, 7, 2, 6), FAR),
                ("D1", _ts(2026, 7, 2, 12), FAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 2, 0)}


def test_lone_far_outlier_does_not_end_the_attached_leg():
    """A single spurious GPS fix far from the vessel amid near fixes (regression:
    D-546, one 31 km outlier between 0.25 km neighbours) is not a departure —
    treating it as one would leak the rest of the vessel-following leg into the
    free track. A clear departure needs consecutive fixes beyond DETACHED_KM."""
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), FAR),  # GPS outlier
                ("D1", _ts(2026, 7, 1, 12), NEAR),
                ("D1", _ts(2026, 7, 1, 18), NEAR),
                ("D1", _ts(2026, 7, 2, 0), FAR),  # real deployment
                ("D1", _ts(2026, 7, 2, 6), FAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 2, 0)}


def test_far_prehistory_encounter_is_treated_as_attachment():
    """A drifter whose history opens far and later passes within NEAR_SHIP_KM
    is indistinguishable, by distance alone, from one staged in port and then
    loaded aboard — so the conservative rule treats the pass as attachment and
    cuts after it (and the following clear departure freezes that cut)."""
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), FAR),
                ("D1", _ts(2026, 7, 1, 6), MID),
                ("D1", _ts(2026, 7, 1, 12), NEAR),  # encounter
                ("D1", _ts(2026, 7, 1, 18), MID),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 1, 18)}


def _offset_exactly(km: float) -> float:
    """Latitude offset (deg) whose distance from the origin, through the
    module's own haversine, is *exactly* ``km`` — pins threshold inclusivity."""
    deg = math.degrees(km / _deploy._EARTH_RADIUS_KM)
    for _ in range(50):
        d = _deploy._haversine_km(deg, 0.0, 0.0, 0.0)
        if d == km:
            return deg
        deg = math.nextafter(deg, math.inf if d < km else -math.inf)
    raise AssertionError(f"no float offset lands exactly on {km} km")


def test_fix_exactly_at_near_threshold_counts_as_attached():
    """NEAR_SHIP_KM is inclusive: a fix at exactly 1.0 km is attached."""
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), _offset_exactly(_deploy.NEAR_SHIP_KM)),
                ("D1", _ts(2026, 7, 1, 6), MID),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 1, 6)}


def test_fix_exactly_at_detached_threshold_does_not_freeze():
    """DETACHED_KM is exclusive: consecutive fixes at exactly 5.0 km are no
    clear departure, so the scan continues to the later near fixes."""
    at_5km = _offset_exactly(_deploy.DETACHED_KM)
    starts = _deploy.deployment_starts(
        _tracks(
            [
                ("D1", _ts(2026, 7, 1, 0), NEAR),
                ("D1", _ts(2026, 7, 1, 6), at_5km),
                ("D1", _ts(2026, 7, 1, 12), at_5km),
                ("D1", _ts(2026, 7, 1, 18), NEAR),
                ("D1", _ts(2026, 7, 2, 0), FAR),
                ("D1", _ts(2026, 7, 2, 6), FAR),
            ]
        ),
        SHIP,
    )
    assert starts == {"D1": _ts(2026, 7, 2, 0)}


def test_empty_ship_track_yields_empty_map():
    tracks = _tracks([("D1", _ts(2026, 7, 1, 0), NEAR)])
    assert _deploy.deployment_starts(tracks, []) == {}
