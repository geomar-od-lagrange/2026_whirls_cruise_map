"""Shared UTC time helpers — one ISO-8601 ``Z`` formatter (from a datetime *and*
from epoch seconds), one parser, one epoch cast.

The codebase had three ISO-8601 formatters — two named ``_iso`` with **incompatible
signatures** (``_iso(datetime)`` in :mod:`_field_store` vs ``_iso(epoch_s)`` in
:mod:`_api`) plus ``iso_utc`` in :mod:`_data` — and the ``.replace("Z", "+00:00")``
parse idiom smeared across several modules (the audit's IDIOM-2, API-3, API-4). This
module is their single home, with unambiguous names.

The clock convention is UTC throughout: a naive datetime is taken to already mean UTC
(the convention :mod:`_currents`/:mod:`_field_store`/:mod:`_api` share), and "epoch
seconds" means naive-UTC seconds since 1970 — the float clock
:attr:`_forecast._Field.times` uses.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso_z(when) -> str:
    """A ``datetime`` / ``pandas.Timestamp`` (tz-aware, or naive taken as UTC) →
    ISO-8601 UTC with a ``Z`` suffix, second precision."""
    when = when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime(_ISO_FMT)


def iso_z_from_epoch(epoch_s: float) -> str:
    """Epoch seconds → ISO-8601 UTC ``Z`` (second precision)."""
    return np.datetime_as_string(np.datetime64(int(round(epoch_s)), "s"), unit="s") + "Z"


def parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string (``Z`` or an explicit offset) → tz-aware **UTC**
    ``datetime``. Raises :class:`ValueError` on an unparseable string."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def to_epoch(dt: datetime) -> float:
    """A tz-aware or naive-UTC ``datetime`` → epoch seconds (naive-UTC seconds since
    1970), the float clock :mod:`_forecast`/:mod:`_field_store` share."""
    dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return float(
        np.datetime64(dt.astimezone(timezone.utc).replace(tzinfo=None), "s").astype(np.float64)
    )


def from_epoch(epoch_s: float) -> datetime:
    """Epoch seconds → tz-aware UTC ``datetime``."""
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc)


def parse_iso_to_epoch(s: str) -> float:
    """Parse an ISO-8601 start time (``Z`` or offset) → epoch seconds. Raises
    :class:`ValueError` on an unparseable string."""
    return to_epoch(parse_iso(s))


def now_iso() -> str:
    """Wall-clock now as ISO-8601 UTC ``Z`` (second precision)."""
    return iso_z_from_epoch(to_epoch(datetime.now(timezone.utc)))
