"""Per-cell near-inertial decomposition of the hourly current window.

The hourly CMEMS window that drives the forecast/hindcast advection
(:func:`whirls_cruise_map._field_store.load_window`) already carries the
near-inertial (NI) oscillation these latitudes ring with — phase and rotation
sense correct, amplitude muted (``plans/012-near-inertial-forecast.md``,
Phase 0). :func:`decompose` separates that window, per grid cell, into a mean
current plus one rotating inertial-frequency component (``mean_u``, ``mean_v``,
``amp``, ``phase``).

The four 2-D fields plus their reference time are also the storage-lean seam a
compressed advection artifact reads instead of an hourly time series: the
whole ~25-step hourly window reconstructs analytically as
``u + i v ~= (mean_u + i mean_v) + amp * exp(i (phase - f (t - t_ref)))``.
:func:`to_inertial_field_json` ships exactly that reconstruction seam — the
four flat fields plus the geometry/timing needed to evaluate it — as the
``inertial_field.json`` artifact the client reconstructs the near-inertial
*animation* from (``plans/014-near-inertial-animation.md``); the client
sweeps ``t - t_ref`` itself, so no time series ever crosses the wire.
:func:`to_inertial_png` renders the amplitude alone as a Mercator-warped
raster (cmocean ``amp``, land transparent) structured exactly like the
surface-speed shading; built and then dropped from ``build.py`` by decision
on review (an amplitude-only overlay), it stays as library code — a second,
simpler seam onto the same decomposition.

The amplitude ships **un-gained** (see :data:`GAIN`): the drifter calibration
found the model's NI amplitude low by a factor that does not generalize, and an
unvalidated multiplier that over-corrects some drifters while under-correcting
others is worse than an honest field. :func:`to_inertial_field_json` ships
``amp`` straight from :func:`decompose`, same reasoning.
"""
from __future__ import annotations

from datetime import datetime, timezone

import cmocean
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

from . import _raster  # noqa: E402

OMEGA = 7.2921159e-5  # Earth's rotation rate (rad/s); f = 2*OMEGA*sin(lat)

# The inertial amplitude ships UN-GAINED. The drifter calibration
# (plans/done/013-inertial-gain-generalization.md) measured sim/obs NI amplitude
# ratios of ~0.4-0.65 with no scalar that generalizes across drifters, windows,
# and sites, so no correction is applied. This constant is the documented seam
# for a future *validated* gain — change it here or pass ``gain=`` explicitly;
# never bake a silent multiplier elsewhere.
GAIN = 1.0

# Amplitude shading, mirroring the speed raster's choices in ``_currents``.
AMP_CMAP = cmocean.cm.amp
AMP_CLIP_PERCENTILE = 99
COLORBAR_STOPS = 16

# Coarsen the native 1/12-deg grid for the animated NI field: the animation draws
# arrows, not particles, and the NI field is spatially smooth, so a sparser grid reads
# just as well at a fraction of the payload — ~68x60 -> ~4k cells over the cruise bbox.
INERTIAL_STRIDE = 8


# --- decomposition -----------------------------------------------------------

def decompose(window: xr.Dataset, t_ref: float | None = None) -> xr.Dataset:
    """Separate the hourly window per grid cell into a mean current and one
    inertial-frequency rotary component by complex least squares.

    Per cell, fit ``w(t) = m + C g(t)`` to the ~25 hourly samples, where
    ``w = uo + i vo``, ``g(t) = exp(-i f (t - t_ref))``, and ``f = 2 Omega
    sin(lat)`` is the local Coriolis frequency — **negative in the southern
    hemisphere**, and kept so: ``g`` then rotates counter-clockwise there, the
    SH-anticyclonic inertial sense the drifters (and the model) show. The joint
    least-squares solve, rather than plain complex demodulation, is what keeps
    mean and NI separated: demodulation needs the window to span an integer
    number of inertial periods for the mean to average out of the demodulated
    series, and this window never does (T_f runs ~15 h at -55 deg to ~46 h at
    -15 deg).

    Closed form: with ``N`` samples and ``S = sum(g_k)`` (note
    ``sum |g_k|^2 = N``), the normal equations are ``[[N, S], [conj(S), N]] .
    [m, C]^T = [sum(w_k), sum(w_k conj(g_k))]^T`` — a 2x2 solved in closed
    form. ``f``, hence ``g``, ``S``, and the 2x2, depend only on latitude, so
    they are computed per latitude row and applied vectorized across
    longitudes; no per-cell Python loop. Conditioning: the determinant
    ``N^2 - |S|^2`` shrinks as the window covers less of an inertial period;
    over this bbox (lat -55..-15) even the worst case — T_f ~46 h at -15 deg,
    so the ~24 h window covers only ~half a period — stays well away from
    singular, though ``amp`` is noisier toward the northern edge than in the
    south, where the window spans more than a full period.

    ``t_ref`` (epoch seconds) defaults to the window time nearest now — the
    same "t = 0 nearest now" anchoring the advection uses
    (:func:`whirls_cruise_map._forecast._anchor_t0`), so the phase
    field and the forecast share one clock. Pass it explicitly for
    reproducible tests.

    Land: CMEMS land is static NaN, so any cell with a NaN anywhere in its
    time series gets NaN in **all** outputs (masking explicitly rather than
    letting NaN ride the sums, which would leak a finite ``mean_v`` through a
    cell whose NaN sat only in ``uo``).

    Returns an ``(latitude, longitude)`` Dataset with ``mean_u``/``mean_v``
    (m/s), ``amp`` (= |C|, m/s), and ``phase`` (= arg(C), radians), carrying
    the reference time as attrs ``t_ref`` (ISO-8601, ``Z`` suffix). The NI
    velocity reconstructs on top of the mean as
    ``amp * exp(i (phase - f (t - t_ref)))``.
    """
    f = window.sortby("latitude").sortby("longitude")  # both ascending
    f = f.transpose("time", "latitude", "longitude")
    times = f["time"].values.astype("datetime64[s]").astype(np.float64)
    if t_ref is None:
        # Anchor to the window time nearest now — the advection's t=0 (see
        # _forecast._anchor_t0); the sub-hour gap to wall-clock now is
        # immaterial.
        now = np.datetime64(
            datetime.now(timezone.utc).replace(tzinfo=None), "s"
        ).astype(np.float64)
        t_ref = float(times[int(np.argmin(np.abs(times - now)))])

    lats = f["latitude"].values.astype(float)
    u = f["uo"].values
    v = f["vo"].values
    w = u + 1j * v  # (time, lat, lon)
    n = times.size

    coriolis = 2.0 * OMEGA * np.sin(np.radians(lats))  # (lat,); < 0 in the SH
    g = np.exp(-1j * np.outer(times - t_ref, coriolis))  # (time, lat)

    # Normal equations, closed form (see docstring): S and the 2x2 depend only
    # on latitude; the right-hand sides are per-cell sums over time.
    s = g.sum(axis=0)  # (lat,)
    b_mean = w.sum(axis=0)  # (lat, lon): sum w_k
    b_rot = np.einsum("tyx,ty->yx", w, np.conj(g))  # (lat, lon): sum w_k conj(g_k)
    det = (n * n - np.abs(s) ** 2)[:, np.newaxis]  # (lat, 1); > 0 off the equator
    m = (n * b_mean - s[:, np.newaxis] * b_rot) / det
    c = (n * b_rot - np.conj(s)[:, np.newaxis] * b_mean) / det

    land = np.isnan(u).any(axis=0) | np.isnan(v).any(axis=0)

    def masked(a: np.ndarray) -> np.ndarray:
        return np.where(land, np.nan, a)

    t_ref_iso = (
        np.datetime_as_string(np.datetime64(int(round(t_ref)), "s"), unit="s") + "Z"
    )
    return xr.Dataset(
        {
            "mean_u": (("latitude", "longitude"), masked(m.real)),
            "mean_v": (("latitude", "longitude"), masked(m.imag)),
            "amp": (("latitude", "longitude"), masked(np.abs(c))),
            "phase": (("latitude", "longitude"), masked(np.angle(c))),
        },
        coords={"latitude": f["latitude"].values, "longitude": f["longitude"].values},
        attrs={"t_ref": t_ref_iso},
    )


# --- amplitude shading (Mercator-warped PNG) ---------------------------------

def _colorbar_stops(n: int = COLORBAR_STOPS) -> list[str]:
    """Hex stops sampled along the amplitude colour map, low -> high. A local
    twin of ``_currents._colorbar_stops`` (that one is bound to the speed
    colour map; sharing would widen ``_currents``' surface for a one-liner)."""
    return [mcolors.to_hex(AMP_CMAP(i / (n - 1))) for i in range(n)]


def to_inertial_png(decomp: xr.Dataset, gain: float = GAIN) -> tuple[bytes, dict]:
    """Render ``gain * amp`` as a Mercator-warped RGBA PNG (cmocean ``amp``,
    clipped at the 99th percentile of the gained field, land transparent) and
    return ``(png_bytes, meta)``. ``meta`` matches ``currents_meta.json``'s
    shape plus a ``gain`` key, with ``valid_time`` the decomposition's
    ``t_ref`` — the client relies on exactly this contract."""
    d = decomp.sortby("latitude").sortby("longitude")  # both ascending
    lats = d["latitude"].values
    lons = d["longitude"].values
    field = gain * d["amp"].values  # land NaN preserved
    vmax = float(np.nanpercentile(field, AMP_CLIP_PERCENTILE))

    def to_rgba(warped):
        rgba = AMP_CMAP(np.clip(warped / vmax, 0.0, 1.0))
        rgba[np.isnan(warped), 3] = 0.0  # land transparent
        return rgba

    png, bounds = _raster.mercator_rgba_png(field, lats, lons, to_rgba)
    meta = {
        "valid_time": decomp.attrs["t_ref"],
        "bounds": bounds,
        "vmax": vmax,
        "units": "m/s",
        "colorbar": _colorbar_stops(),
        "gain": float(gain),
    }
    return png, meta


# --- animation field (JSON) --------------------------------------------------

def to_inertial_field_json(decomp: xr.Dataset, stride: int = INERTIAL_STRIDE) -> dict:
    """Flatten the decomposition to the JSON vector field the client
    reconstructs the rotating near-inertial current from analytically
    (``plans/014-near-inertial-animation.md``): per cell, ``mean_u``,
    ``mean_v``, ``amp``, ``phase`` plus the geometry/timing needed to
    evaluate ``amp * exp(i (phase - f (t - t_ref)))`` at any client-side
    ``t``.

    Geometry follows the leaflet-velocity grid convention: ``stride`` is
    applied here (via ``.isel``) rather than in :func:`decompose`, which
    stays full-resolution for other consumers; the surviving cells are then
    sorted latitude-descending, longitude-ascending and raveled in C (row-
    major) order, so ``la1``/``la2`` are the north/south edges and flat index
    ``row * nx + col`` addresses the cell at ``lat = la1 - row * dy``,
    ``lon = lo1 + col * dx`` — the standard leaflet-velocity header layout, so
    the same grid math applies.

    This ships four *analytic* fields, not two velocity components, and applies
    **no** magnitude compression: ``amp`` is true, un-gained m/s
    (:data:`GAIN` = 1.0) straight from
    :func:`decompose`, because the client reconstructs a physical rotation
    from it, not just a direction-preserving trail. Land is JSON ``null``
    (NaN converted to ``None`` before serialisation — ``json.dumps`` would
    otherwise emit a bare, invalid ``NaN``) rather than the leaflet-velocity
    convention of zero: this is an analytic field the client evaluates
    per-frame, not a grid a bilinear-interpolating library walks, so there is
    no need to paper over land with a fake current. A cell that is NaN in the
    decomposition (see :func:`decompose`'s land handling) is ``null`` in all
    four output arrays together. Values are rounded to 4 dp to keep the
    payload small.
    """
    coarse = decomp.isel(
        latitude=slice(None, None, stride),
        longitude=slice(None, None, stride),
    )
    coarse = coarse.sortby("latitude", ascending=False).sortby(
        "longitude", ascending=True
    )
    lats = coarse["latitude"].values
    lons = coarse["longitude"].values

    def flat(name: str) -> list[float | None]:
        values = coarse[name].values.ravel(order="C")
        return [None if np.isnan(v) else round(float(v), 4) for v in values]

    header = {
        "nx": int(lons.size),
        "ny": int(lats.size),
        "lo1": float(lons.min()),
        "lo2": float(lons.max()),
        "la1": float(lats.max()),
        "la2": float(lats.min()),
        "dx": float(abs(lons[1] - lons[0])),
        "dy": float(abs(lats[1] - lats[0])),
        "t_ref": decomp.attrs["t_ref"],
        "omega": OMEGA,
        "units": "m.s-1",
    }
    return {
        "header": header,
        "mean_u": flat("mean_u"),
        "mean_v": flat("mean_v"),
        "amp": flat("amp"),
        "phase": flat("phase"),
    }
