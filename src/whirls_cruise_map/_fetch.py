"""Download the drifter location share and extract its snapshot CSVs."""
from __future__ import annotations

import urllib.request
import zipfile
from pathlib import Path

SHARE_URL = "https://cloud.geomar.de/s/as5DjLdynsMNapt/download"


def fetch_snapshots(dest_dir: Path) -> list[Path]:
    """Download the share zip into ``dest_dir``, extract it, and return the
    paths of every ``2026_whirls_drifters/*.csv`` snapshot.

    A fresh full download each call (a few dozen MB); no caching.
    """
    zip_path = dest_dir / "share.zip"
    urllib.request.urlretrieve(SHARE_URL, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest_dir)
    return sorted(dest_dir.glob("2026_whirls_drifters/*.csv"))
