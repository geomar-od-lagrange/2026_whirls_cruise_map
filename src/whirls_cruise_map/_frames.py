"""Layer-neutral frame primitives — naming, time-slicing, and colour-class
quantization — shared by the speed (``_currents``) and vorticity (``_vorticity``)
shading renderers."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import xarray as xr

from . import _time

# Discrete shading classes. Both the sequential speed raster and the diverging ζ/f
# raster (_vorticity, which imports these) snap the normalized [0, 1] colormap input
# to one of N_BINS flat colour classes — bin midpoints — *before* the cmocean lookup,
# trading the continuous ~256-step ramp for N_BINS constant-colour regions on the same
# palette (no new colours). Lossless WebP compresses those large flat regions far
# better: ~-60% bytes per frame at 12 bins (measured on the real frames — the speed set
# drops ~0.70 MB -> ~0.27 MB, ζ/f ~0.95 MB -> ~0.38 MB, and the critical-path now frame
# ~87 kB -> ~34 kB). The classes also make the map<->legend lookup quantitative
# (standard for oceanographic charts). The cost is deliberate banding; this is the one
# constant to raise (toward a continuous ramp) to revert. Keep it **even** so the
# diverging ζ/f map holds zero on a bin *edge* — clip(x/vmax,-1,1)*0.5+0.5 = 0.5 = 6/12
# lands on the boundary between the two middle classes, 6 classes per sign.
N_BINS = 12


def _quantize_unit(t: np.ndarray) -> np.ndarray:
    """Snap a normalized [0, 1] colormap input to its N_BINS-bin midpoint, so the
    cmocean lookup returns one of N_BINS flat colours. Bin ``i`` spans ``[i/N,
    (i+1)/N)`` with midpoint ``(i+0.5)/N``; ``t == 1`` clamps into the top bin. NaN
    (land) passes straight through — the caller masks it to a transparent pixel after
    the colour lookup, so the alpha handling is untouched."""
    idx = np.clip(np.floor(t * N_BINS), 0.0, N_BINS - 1)
    return (idx + 0.5) / N_BINS


# A frame filename is ``kind_<token>.ext`` with an absolute, colon-free (so it's a
# safe filename) hour-precision UTC token, e.g. ``speed_2026-07-01T00Z.webp``. The
# retired offset form ``kind_±NNh.ext`` (from the moving-anchor design) is matched by
# _STALE_FRAME_RE only, so :func:`prune_stale_frames` sweeps those leftovers.
_TOKEN_FMT = "%Y-%m-%dT%HZ"
_FRAME_FILE_RE = re.compile(
    r"^(speed|vorticity|flowvis)_(\d{4}-\d{2}-\d{2}T\d{2})Z\.(webp|json)$"
)


def frame_token(when: datetime) -> str:
    """The colon-free hour-precision UTC token in a frame filename, e.g.
    ``2026-07-01T00Z`` — a frame time always lands on the hour, so the token is
    lossless for it."""
    return when.astimezone(timezone.utc).strftime(_TOKEN_FMT)


def frame_valid_time(when: datetime) -> str:
    """The full ISO-8601 ``valid_time`` (``...T00:00:00Z``) a manifest carries for the
    frame at ``when`` — the same shape :func:`valid_time` emits for a single slice, so
    clients parse frame and slice times identically."""
    return _time.iso_z(when)


def frame_filename(kind: str, when: datetime, ext: str = "webp") -> str:
    """Absolute-time frame artifact name, e.g. ``speed_2026-07-01T00Z.webp`` /
    ``vorticity_...`` / ``flowvis_...`` (all lossless WebP: the two shadings and the
    static flow streamlines). Single source of truth shared by the renderers and the
    build's manifests / pruning."""
    return f"{kind}_{frame_token(when)}.{ext}"


def parse_frame_filename(name: str) -> tuple[str, datetime] | None:
    """``(kind, valid_time)`` for an absolute-time frame filename, else ``None`` (a
    retired offset name, a meta file, or anything else) — the inverse of
    :func:`frame_filename`, so ``file -> valid_time`` round-trips exactly."""
    m = _FRAME_FILE_RE.match(name)
    if m is None:
        return None
    when = datetime.strptime(m.group(2), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    return m.group(1), when


def _slice_at(window: xr.Dataset, when: datetime) -> xr.Dataset:
    """The window's 2-D slice nearest ``when`` (a frame time lands on the 6-hourly grid
    exactly, so ``nearest`` picks the true step)."""
    target = np.datetime64(when.astimezone(timezone.utc).replace(tzinfo=None), "ns")
    return window.sel(time=target, method="nearest")
