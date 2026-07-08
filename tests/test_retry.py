"""The shared backoff/retry primitive behind the download fetchers.

:func:`whirls_cruise_map._retry.with_retry` wraps the drifter-share download
(:mod:`_fetch`) and the CMEMS subsets (:mod:`_currents`); both rely on it to
outlast a transient upstream blip instead of leaving overlays stale until the
next cron tick.
"""
from __future__ import annotations

import pytest

from whirls_cruise_map import _retry


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)


def test_returns_first_success_without_retry():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert _retry.with_retry(fn, attempts=3, backoff=1) == "ok"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return calls["n"]

    assert _retry.with_retry(fn, attempts=4, backoff=1) == 3
    assert calls["n"] == 3


def test_gives_up_after_attempts_and_chains_cause():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ConnectionError("blip")

    with pytest.raises(RuntimeError, match="widget failed after 3 attempts") as ei:
        _retry.with_retry(fn, attempts=3, backoff=1, label="widget")
    assert calls["n"] == 3
    assert isinstance(ei.value.__cause__, ConnectionError)


def test_does_not_retry_unlisted_exceptions():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise KeyError("not transient")

    with pytest.raises(KeyError):
        _retry.with_retry(fn, attempts=3, backoff=1, exceptions=(ConnectionError,))
    assert calls["n"] == 1
