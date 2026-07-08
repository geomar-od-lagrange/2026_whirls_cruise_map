"""Download the drifter location share and extract its snapshot CSVs."""
from __future__ import annotations

import http.client
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from ._retry import with_retry

SHARE_URL = "https://cloud.geomar.de/s/as5DjLdynsMNapt/download"

# The share endpoint occasionally truncates its chunked response
# (``IncompleteRead``) or stalls a half-open connection; a bare download then
# aborts the whole build. Retry with backoff and a read/connect timeout so a
# transient upstream blip doesn't leave overlays stale until the next cron tick.
# Total retry time (timeouts + backoff) stays well inside the fast job's 900s
# deadline.
_TIMEOUT = 60  # seconds; share is a few dozen MB
_ATTEMPTS = 4
_BACKOFF = 5  # base seconds: 5s, 10s, 20s between attempts

# A truncated download can also arrive without erroring; ``is_zipfile`` then
# rejects it and the attempt is retried, rather than failing later in extract.
_RETRYABLE = (urllib.error.URLError, http.client.IncompleteRead, OSError, ValueError)


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``, retrying on transient network failures.

    Validates that the result is a readable zip before returning, so a
    truncated (but non-erroring) download is caught here and retried rather
    than failing later in :class:`zipfile.ZipFile`.
    """

    def attempt() -> None:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp, open(
            dest, "wb"
        ) as fh:
            shutil.copyfileobj(resp, fh)
        if not zipfile.is_zipfile(dest):
            raise ValueError("downloaded file is not a valid zip")

    with_retry(
        attempt,
        attempts=_ATTEMPTS,
        backoff=_BACKOFF,
        exceptions=_RETRYABLE,
        label=f"download of {url}",
    )


def fetch_snapshots(dest_dir: Path) -> list[Path]:
    """Download the share zip into ``dest_dir``, extract it, and return the
    paths of every ``2026_whirls_drifters/*.csv`` snapshot.

    A fresh full download each call (a few dozen MB); no caching.
    """
    zip_path = dest_dir / "share.zip"
    _download(SHARE_URL, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest_dir)
    return sorted(dest_dir.glob("2026_whirls_drifters/*.csv"))
