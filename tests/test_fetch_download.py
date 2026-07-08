"""Retry/timeout hardening of the drifter-share download.

The share endpoint (``cloud.geomar.de``) occasionally truncates its chunked
response (``IncompleteRead``) or hands back a corrupt zip; a bare
``urlretrieve`` used to abort the whole build. These tests pin that
:func:`_download` retries such transient failures with backoff and only gives
up (raising a wrapping ``RuntimeError``) after exhausting its attempts.
"""
from __future__ import annotations

import http.client
import io
import zipfile
from pathlib import Path

import pytest

from whirls_cruise_map import _fetch, _retry


def _zip_bytes() -> bytes:
    """A minimal in-memory zip, so ``is_zipfile`` accepts a successful fetch."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026_whirls_drifters/x.csv", "t,lat,lon\n")
    return buf.getvalue()


class _FakeResp(io.BytesIO):
    """Context-manager wrapper so ``with urlopen(...) as resp`` works."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)


def test_download_retries_then_succeeds(tmp_path, monkeypatch):
    good = _zip_bytes()
    calls = {"n": 0}

    def fake_urlopen(url, timeout):
        calls["n"] += 1
        if calls["n"] < 3:  # first two attempts truncate mid-stream
            raise http.client.IncompleteRead(b"partial")
        return _FakeResp(good)

    monkeypatch.setattr(_fetch.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "share.zip"
    _fetch._download(_fetch.SHARE_URL, dest)

    assert calls["n"] == 3
    assert dest.read_bytes() == good


def test_download_retries_on_corrupt_zip(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(url, timeout):
        calls["n"] += 1
        # A completed download that is not actually a zip: caught by the
        # is_zipfile check and retried, not passed on to extraction.
        payload = b"not a zip" if calls["n"] == 1 else _zip_bytes()
        return _FakeResp(payload)

    monkeypatch.setattr(_fetch.urllib.request, "urlopen", fake_urlopen)
    _fetch._download(_fetch.SHARE_URL, tmp_path / "share.zip")
    assert calls["n"] == 2


def test_download_gives_up_after_attempts(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(url, timeout):
        calls["n"] += 1
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(_fetch.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        _fetch._download(_fetch.SHARE_URL, tmp_path / "share.zip")
    assert calls["n"] == _fetch._ATTEMPTS


def test_fetch_snapshots_extracts_after_download(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _fetch, "_download", lambda url, dest: Path(dest).write_bytes(_zip_bytes())
    )
    got = _fetch.fetch_snapshots(tmp_path)
    assert [p.name for p in got] == ["x.csv"]
