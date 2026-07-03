"""Tests for the per-cell near-inertial decomposition (``_inertial``).

All synthetic — no network, no CMEMS. The synthetic window is built with the
module's own convention (``f = 2 Omega sin(lat)`` kept negative in the SH,
``g(t) = exp(-i f (t - t_ref))``), so recovery pins the algebra and the
rotation-sense test pins the convention itself.
"""
from __future__ import annotations

import json

import numpy as np
import xarray as xr

from whirls_cruise_map import _inertial

T0 = np.datetime64("2026-07-03T00:00:00", "s")
N_TIME = 25  # hourly steps, ~1.2 inertial periods at lat ~ -37 (T_f ~ 19.9 h)
N_LAT, N_LON = 4, 5

META_KEYS = {"valid_time", "bounds", "vmax", "units", "colorbar", "gain"}


def _epoch(times: np.ndarray) -> np.ndarray:
    return times.astype("datetime64[s]").astype(np.float64)


def _synthetic_window() -> tuple[xr.Dataset, dict]:
    """Hourly window on a small grid near lat -37 with a known per-cell mean
    plus one inertial-frequency rotary component. Returns the window and the
    per-cell truth (``u0``, ``v0``, ``amp``, ``phase``, ``t_ref``)."""
    lats = -37.0 + 0.25 * np.arange(N_LAT)
    lons = 11.0 + 0.25 * np.arange(N_LON)
    times = T0 + np.arange(N_TIME).astype("timedelta64[h]")
    t_ref = float(_epoch(times)[0])

    rng = np.random.default_rng(42)
    u0 = rng.uniform(-0.3, 0.3, (N_LAT, N_LON))
    v0 = rng.uniform(-0.3, 0.3, (N_LAT, N_LON))
    amp = rng.uniform(0.05, 0.25, (N_LAT, N_LON))
    phase = rng.uniform(-np.pi, np.pi, (N_LAT, N_LON))

    f = 2.0 * _inertial.OMEGA * np.sin(np.radians(lats))  # (lat,); < 0 here
    g = np.exp(-1j * np.outer(_epoch(times) - t_ref, f))  # (time, lat)
    w = (u0 + 1j * v0)[None] + (amp * np.exp(1j * phase))[None] * g[:, :, None]

    window = xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), w.real),
            "vo": (("time", "latitude", "longitude"), w.imag),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )
    return window, {"u0": u0, "v0": v0, "amp": amp, "phase": phase, "t_ref": t_ref}


def test_decompose_recovers_synthetic_field():
    window, truth = _synthetic_window()
    d = _inertial.decompose(window, t_ref=truth["t_ref"])
    np.testing.assert_allclose(d["mean_u"].values, truth["u0"], atol=1e-10)
    np.testing.assert_allclose(d["mean_v"].values, truth["v0"], atol=1e-10)
    np.testing.assert_allclose(d["amp"].values, truth["amp"], atol=1e-10)
    dphi = np.angle(np.exp(1j * (d["phase"].values - truth["phase"])))  # mod 2 pi
    np.testing.assert_allclose(dphi, 0.0, atol=1e-9)
    assert d.attrs["t_ref"] == "2026-07-03T00:00:00Z"


def test_southern_hemisphere_inertial_rotation_is_ccw():
    """f < 0 in the SH, so g(t) = exp(-i f t) advances counter-clockwise — the
    SH-anticyclonic inertial sense. The cross product of consecutive NI
    velocity samples must stay positive (hourly steps rotate well under pi, so
    the sign is unambiguous). Guards the sign convention."""
    f = 2.0 * _inertial.OMEGA * np.sin(np.radians(-37.0))
    assert f < 0
    w = 0.1 * np.exp(-1j * f * 3600.0 * np.arange(N_TIME))  # pure NI component
    u, v = w.real, w.imag
    cross = u[:-1] * v[1:] - v[:-1] * u[1:]
    assert np.all(cross > 0)


def test_single_nan_timestep_masks_the_cell_everywhere():
    window, truth = _synthetic_window()
    window["uo"][3, 1, 2] = np.nan  # one timestep, one component, one cell
    d = _inertial.decompose(window, t_ref=truth["t_ref"])
    for name in ("mean_u", "mean_v", "amp", "phase"):
        values = d[name].values
        assert np.isnan(values[1, 2])
        others = np.delete(values.ravel(), 1 * N_LON + 2)
        assert np.all(np.isfinite(others))
    # Neighbours are not just finite but still exact.
    np.testing.assert_allclose(d["amp"].values[1, 3], truth["amp"][1, 3], atol=1e-10)


def test_to_inertial_png_contract():
    window, truth = _synthetic_window()
    d = _inertial.decompose(window, t_ref=truth["t_ref"])
    png, meta = _inertial.to_inertial_png(d)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    assert set(meta) == META_KEYS
    assert meta["gain"] == 1.0
    assert meta["vmax"] > 0
    assert meta["units"] == "m/s"
    assert meta["valid_time"] == d.attrs["t_ref"]
    json.dumps(meta)  # the build writes it verbatim; must be JSON-serializable

    _, meta2 = _inertial.to_inertial_png(d, gain=2.0)
    assert meta2["gain"] == 2.0
    assert np.isclose(meta2["vmax"], 2.0 * meta["vmax"])


def test_default_t_ref_is_a_window_time():
    """Without an explicit t_ref, decompose anchors to the window time nearest
    now — whichever that is, it must be one of the window's own times."""
    window, _ = _synthetic_window()
    d = _inertial.decompose(window)
    window_iso = {
        np.datetime_as_string(t, unit="s") + "Z"
        for t in window["time"].values.astype("datetime64[s]")
    }
    assert d.attrs["t_ref"] in window_iso
