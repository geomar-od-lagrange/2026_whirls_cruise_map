"""Retry a transient operation with exponential backoff (stdlib only).

Shared by the download fetchers whose upstreams occasionally blip: the drifter
share (:mod:`_fetch`) and the CMEMS subsets (:mod:`_currents`). A transient
failure that isn't retried leaves overlays stale until the next cron tick —
minutes on the fast/ingest tier, but up to 6h on the slow tier — so a couple of
backed-off retries inside the job's deadline is the cheap, targeted fix.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int,
    backoff: float,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    label: str = "operation",
) -> T:
    """Call ``fn`` and return its result, retrying on ``exceptions``.

    Backs off ``backoff * 2**n`` seconds between attempts (so ``backoff=5`` ->
    5s, 10s, 20s, ...). After ``attempts`` failures raises ``RuntimeError``
    chained from the last exception; ``label`` names the operation in that
    message.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except exceptions as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(backoff * 2**attempt)
    raise RuntimeError(f"{label} failed after {attempts} attempts") from last
