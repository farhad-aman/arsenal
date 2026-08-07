import time
from datetime import datetime, timezone

from .format import DASH

Epoch = int | float | str | None

CELL = 16

_TODAY = "%H:%M"
_OLDER = "%Y-%m-%d %H:%M"


def _local(epoch: Epoch) -> datetime | None:
    """A stored timestamp read back in the machine's own timezone.

    Every time dl writes is Unix epoch, which is UTC by definition. Reading it
    as UTC and converting makes that explicit rather than leaning on
    fromtimestamp's local-time default, so the same log shows 12:00 in London
    and 15:30 in Tehran.
    """
    try:
        seconds = float(epoch)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def stamp(epoch: Epoch, now: Epoch = None) -> str:
    """When this happened, in local time. Today gives the clock, older gives the date.

    Today means the same local calendar day, not the last 24 hours: something
    from 23:00 last night is yesterday's even when it is five hours old.
    """
    when = _local(epoch)
    if when is None:
        return DASH
    against = _local(time.time() if now is None else now)
    if against is not None and when.date() == against.date():
        return when.strftime(_TODAY)
    return when.strftime(_OLDER)
