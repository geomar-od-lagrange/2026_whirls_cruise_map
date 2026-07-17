"""Shared IPSL observations-portal HTTP fetch.

One place the portal's fetch policy lives — the ``Accept``-header requirement, the
timeout, and the decode — so :mod:`_gliders` and :mod:`_agulhas` fetch the same host the
same way instead of each re-implementing the dance (SRC-1). The portal's Apache 403s a
request with no ``Accept`` header (urllib omits it by default); a descriptive
``User-Agent`` is courtesy, not required. The natural home for a consistent per-source
retry policy too, should one be added.
"""
from __future__ import annotations

import urllib.request

_HEADERS = {"User-Agent": "whirls-cruise-map ingest", "Accept": "*/*"}
_TIMEOUT = 30  # seconds — the one named source for the portal fetch timeout (SRC-4)


def get_bytes(url: str) -> bytes:
    """Fetch ``url`` from the portal, returning the raw bytes. Raises on any HTTP or
    network error — callers wrap in their own best-effort ``try/except``."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def get(url: str) -> str:
    """Fetch ``url`` from the portal as UTF-8 text (undecodable bytes replaced)."""
    return get_bytes(url).decode("utf-8", "replace")
