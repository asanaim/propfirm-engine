"""Trading hours per symbol (build plan step 1, line 4; review D6).

A schedule in New York time, the firm's trading-day zone:

    weekly window   opens on one weekday and minute, closes on another
                    (forex Sunday 17:00 to Friday 17:00)
    daily break     a daily pause inside the window (metals and oil
                    17:00 to 18:00, as the CME Globex session)
    closures        dated holiday closures, [from, to) in ms UTC
    always          open every minute (crypto), closures aside

While a symbol is closed the engine refuses new orders, closes and stop
changes (reason `market_closed`), holds pending orders, stop-losses and
take-profits, and does not apply a price that arrives (a stray tick cannot
fire a stop). A pending order cannot be cancelled either: MetaTrader, the
platform of FTMO and FundedNext, refuses every request on a closed market.
A breach close-out still closes at the last price, as for a frozen symbol.

Minutes are integers: a weekly minute counts from Monday 00:00 (0) to
Sunday 23:59 (10079); a daily minute from 00:00 (0) to 23:59 (1439). Local
time comes from the pinned tzdata, so every machine agrees.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from propfirm.journal.codec import added_field
from propfirm.rules.clock import NEW_YORK

WEEK_MINUTES = 7 * 1440
DAY_MINUTES = 1440
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEK_TIME = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01][0-9]|2[0-3]):([0-5][0-9])")
_DAY_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_LOCAL = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([01][0-9]|2[0-3]):([0-5][0-9])")


def _minute(value: object, name: str, top: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < top:
        raise ValueError(f"{name} must be a whole minute in [0, {top})")


def _within(m: int, start: int, end: int) -> bool:
    """m in [start, end) on a circle (start > end wraps)."""
    return start <= m < end if start < end else (m >= start or m < end)


@dataclass(frozen=True, slots=True)
class TradingHours:
    open_minute: int  # weekly minute the window opens (New York)
    close_minute: int  # weekly minute it closes
    break_start: int | None = None  # daily minute a break starts
    break_end: int | None = None  # daily minute it ends
    always: bool = False  # open every minute (closures aside)
    closures: tuple[tuple[int, int], ...] = ()  # [from, to) ms UTC, sorted
    # Market break rule (build plan step 1; FTMO's funded Standard account):
    # an account bound by it is made flat this many minutes before a closed
    # period of at least `long_break_minutes`; 0: no such rule on this
    # symbol. Added after records existed.
    flat_before_minutes: int = added_field(0)
    long_break_minutes: int = added_field(120)

    def __post_init__(self) -> None:
        _minute(self.open_minute, "open_minute", WEEK_MINUTES)
        _minute(self.close_minute, "close_minute", WEEK_MINUTES)
        if self.open_minute == self.close_minute and not self.always:
            raise ValueError("a weekly window needs different open and close minutes")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("a daily break needs a start and an end")
        if self.break_start is not None and self.break_end is not None:
            _minute(self.break_start, "break_start", DAY_MINUTES)
            _minute(self.break_end, "break_end", DAY_MINUTES)
            if self.break_start == self.break_end:
                raise ValueError("a daily break needs different start and end minutes")
        if not isinstance(self.always, bool):
            raise TypeError("always must be a bool")
        for name in ("flat_before_minutes", "long_break_minutes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be whole minutes >= 0")
        if self.long_break_minutes < 1:
            raise ValueError("long_break_minutes must be at least 1")
        if self.flat_before_minutes > DAY_MINUTES:
            raise ValueError("flat_before_minutes must be at most a day")
        last = -1
        for pair in self.closures:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in pair)
            ):
                raise ValueError("a closure is (from ms, to ms)")
            start, end = pair
            if not 0 <= start < end or start < last:
                raise ValueError("closures must be non-empty, sorted and apart")
            last = end

    def next_long_break(self, ts: int) -> tuple[int, int] | None:
        """(close, open) in ms UTC of the first closed period that starts
        after `ts` and lasts at least `long_break_minutes`; None when the
        market never closes that long in the next 16 days."""
        if self.always and not self.closures:
            return None
        changes = sorted(c for c in self._changes(ts, 16) if c > ts)
        state = self.is_open(ts)
        close: int | None = None
        for at in changes:
            now = self.is_open(at)
            if now == state:
                continue
            state = now
            if not now:
                close = at
            elif close is not None:
                if at - close >= self.long_break_minutes * 60_000:
                    return close, at
                close = None
        return None

    def _changes(self, ts: int, days: int) -> set[int]:
        """Every instant at which the schedule can change in the next
        `days` days: local midnights plus each daily minute the window or
        the break turns at, and every closure's ends."""
        minutes = {0, self.open_minute % DAY_MINUTES, self.close_minute % DAY_MINUTES}
        if self.break_start is not None and self.break_end is not None:
            minutes |= {self.break_start, self.break_end}
        start = datetime.fromtimestamp(ts // 1000, tz=NEW_YORK).date()
        out: set[int] = set()
        for d in range(days + 1):
            day = start + timedelta(days=d)
            for m in minutes:
                local = datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=NEW_YORK)
                out.add(int(local.timestamp()) * 1000)
        for a, b in self.closures:
            out |= {a, b}
        return out

    def is_open(self, ts: int) -> bool:
        """Whether trading is open at `ts` (ms UTC)."""
        for start, end in self.closures:
            if start <= ts < end:
                return False
        if self.always:
            return True
        local = datetime.fromtimestamp(ts // 1000, tz=NEW_YORK)
        day = local.hour * 60 + local.minute
        week = local.weekday() * DAY_MINUTES + day
        if not _within(week, self.open_minute, self.close_minute):
            return False
        if self.break_start is not None and self.break_end is not None:
            return not _within(day, self.break_start, self.break_end)
        return True


ALWAYS = TradingHours(0, 1, always=True)


def week_minute(text: str) -> int:
    """'Sun 17:00' as a weekly minute (Monday 00:00 = 0)."""
    found = _WEEK_TIME.fullmatch(text)
    if found is None:
        raise ValueError(f'{text!r} is not a weekday and time like "Sun 17:00"')
    day, hour, minute = found.groups()
    return _DAYS.index(day) * DAY_MINUTES + int(hour) * 60 + int(minute)


def day_minute(text: str) -> int:
    """'17:00' as a daily minute."""
    found = _DAY_TIME.fullmatch(text)
    if found is None:
        raise ValueError(f'{text!r} is not a time like "17:00"')
    return int(found.group(1)) * 60 + int(found.group(2))


def new_york_ms(text: str) -> int:
    """'2026-12-24 17:00' (New York wall time) as ms UTC."""
    found = _LOCAL.fullmatch(text)
    if found is None:
        raise ValueError(f'{text!r} is not a date and time like "2026-12-24 17:00"')
    y, mo, d, h, mi = (int(g) for g in found.groups())
    local = datetime(y, mo, d, h, mi, tzinfo=NEW_YORK)
    return int(local.timestamp()) * 1000
