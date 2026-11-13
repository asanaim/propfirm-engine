"""The trading day: from 17:00 America/New_York to the next 17:00 [CONFIRMED].

Time-zone rules come from the pinned `tzdata` package, never from the
operating system, so every machine computes the same reset instants.
Arithmetic is integer milliseconds; 17:00 is never inside a daylight-saving
jump (those happen at 02:00), so every boundary is unambiguous.
"""

from datetime import UTC, date, datetime, time, timedelta
from importlib import resources
from zoneinfo import ZoneInfo

from propfirm._checks import require_int

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def _zone(key: str) -> ZoneInfo:
    with resources.files("tzdata.zoneinfo").joinpath(key).open("rb") as f:
        return ZoneInfo.from_file(f, key=key)


NEW_YORK = _zone("America/New_York")


class TradingClock:
    """Trading-day boundaries at a fixed local time in one zone."""

    def __init__(self, zone: ZoneInfo = NEW_YORK, reset: time = time(17)) -> None:
        if reset.tzinfo is not None:
            raise ValueError("reset must be a naive local time")
        self._zone = zone
        self._reset = reset

    def day_start(self, ts: int) -> int:
        """The latest boundary at or before `ts` (ms UTC): the trading day's id."""
        require_int("ts", ts, minimum=0)
        local = datetime.fromtimestamp(ts // 1000, tz=self._zone)
        day = local.date() if local.time() >= self._reset else local.date() - timedelta(days=1)
        return self._boundary(day)

    def next_boundary(self, ts: int) -> int:
        """The earliest boundary strictly after `ts` (ms UTC)."""
        start = self.day_start(ts)
        local = datetime.fromtimestamp(start // 1000, tz=self._zone)
        return self._boundary(local.date() + timedelta(days=1))

    def weekday(self, ts: int) -> int:
        """The local weekday of `ts` (ms UTC), Monday = 0."""
        require_int("ts", ts, minimum=0)
        return datetime.fromtimestamp(ts // 1000, tz=self._zone).weekday()

    def _boundary(self, day: date) -> int:
        local = datetime.combine(day, self._reset, tzinfo=self._zone)
        return (local - _EPOCH) // _MS


NEW_YORK_17 = TradingClock()
