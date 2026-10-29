"""Delay, gap and outage guards (acceptance test 5).

A symbol is frozen when its prices cannot be trusted. While frozen it takes
no new orders, and its stops and limits are held (not triggered). Rules for
each case (Confirmed Configuration CC-5):

- No price yet: frozen until the first fresh update.
- Delay guard [DEFAULT 2,000 ms]: an update whose receive time is more than
  the threshold after its source time freezes the symbol, and its price is
  NOT applied.
- Gap guard [DEFAULT per symbol]: no fresh update for longer than the
  symbol's threshold freezes it. The plan calibrates thresholds per symbol
  and session from the measured 99th-percentile gap in P3; until then one
  threshold per symbol.
- Feed outage: every symbol freezes; each resumes on its first fresh update.

Any fresh update (delay within the threshold) unfreezes its symbol. Time is
supplied by the caller from events, never read from a clock.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from propfirm._checks import require_int
from propfirm.market.types import PriceUpdate

DEFAULT_DELAY_THRESHOLD_MS = 2_000
DEFAULT_GAP_THRESHOLD_MS = 60_000


class FreezeReason(StrEnum):
    NO_PRICE = "no_price"
    DELAYED = "delayed"
    OVERDUE = "overdue"
    OUTAGE = "outage"


@dataclass(frozen=True, slots=True)
class GuardChange:
    """A symbol froze (reason set) or resumed (reason None) at time `ts`."""

    symbol: str
    ts: int
    reason: FreezeReason | None


@dataclass(frozen=True, slots=True)
class GuardConfig:
    delay_threshold_ms: int = DEFAULT_DELAY_THRESHOLD_MS
    default_gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS
    gap_threshold_ms: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        require_int("delay_threshold_ms", self.delay_threshold_ms, minimum=0)
        require_int("default_gap_threshold_ms", self.default_gap_threshold_ms, minimum=1)
        for symbol, ms in (self.gap_threshold_ms or {}).items():
            require_int(f"gap_threshold_ms[{symbol}]", ms, minimum=1)

    def gap_threshold(self, symbol: str) -> int:
        return (self.gap_threshold_ms or {}).get(symbol, self.default_gap_threshold_ms)


class FeedGuards:
    def __init__(self, config: GuardConfig, symbols: Iterable[str]) -> None:
        self._config = config
        self._reason: dict[str, FreezeReason | None] = {s: FreezeReason.NO_PRICE for s in symbols}
        self._last_fresh: dict[str, int] = {}

    def reason(self, symbol: str) -> FreezeReason | None:
        return self._reason[symbol]

    def is_frozen(self, symbol: str) -> bool:
        return self._reason[symbol] is not None

    def advance(self, ts: int) -> list[GuardChange]:
        """Freeze every live symbol whose last fresh update is overdue at `ts`."""
        require_int("ts", ts, minimum=0)
        changes: list[GuardChange] = []
        for symbol, reason in self._reason.items():
            if reason is not None:
                continue
            if ts - self._last_fresh[symbol] > self._config.gap_threshold(symbol):
                changes.append(self._set(symbol, ts, FreezeReason.OVERDUE))
        return changes

    def on_update(self, update: PriceUpdate) -> tuple[bool, list[GuardChange]]:
        """Return (apply, changes). A delayed update is never applied."""
        symbol = update.symbol
        if symbol not in self._reason:
            raise KeyError(f"unknown symbol {symbol!r}")
        if update.receive_ts - update.source_ts > self._config.delay_threshold_ms:
            if self._reason[symbol] is FreezeReason.DELAYED:
                return False, []
            return False, [self._set(symbol, update.receive_ts, FreezeReason.DELAYED)]
        self._last_fresh[symbol] = update.receive_ts
        if self._reason[symbol] is None:
            return True, []
        return True, [self._set(symbol, update.receive_ts, None)]

    def on_outage(self, ts: int) -> list[GuardChange]:
        """The feed connection is down: freeze every symbol."""
        require_int("ts", ts, minimum=0)
        return [
            self._set(symbol, ts, FreezeReason.OUTAGE)
            for symbol, reason in self._reason.items()
            if reason is not FreezeReason.OUTAGE
        ]

    def _set(self, symbol: str, ts: int, reason: FreezeReason | None) -> GuardChange:
        self._reason[symbol] = reason
        return GuardChange(symbol, ts, reason)
