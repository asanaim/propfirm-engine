"""Bid and ask around the feed's single price.

The feed sends one price per update and no bid or ask (Twelve Data sends no
bid or ask over its WebSocket, on any plan), so the engine derives them
from the market's own movement. Two models, chosen by the config type:

`MarketSpreadConfig` (policy trading-v2 on): follows the market.

    move   = the median of the last `window` |price changes| (the lower one
             of the middle two for an even count), once there are
             `min_samples` of them; 0 before
    raw    = min(max(min_points, ceil(vol_multiplier * move)), max_points)
    spread = round_up_to_even(raw * (news_multiplier inside a news window, else 1))

    In a calm market the spread is the symbol's normal spread (`min_points`);
    when the market moves fast for a while the spread widens with it, up to
    `max_points` (news windows multiply that). A single jump, a gap or a bad
    tick is one change among `window`, so it does not move the median: the
    price follows the market at once, the spread does not blow out.

`SpreadConfig` (the first model, kept so journals written under it replay
exactly): raw = max(min_points, ceil(vol_multiplier * mean |price change|)),
no cap. A single large jump widens it to about the jump's size for up to
`window` updates, so a market order right after a gap could breach a loss
limit at once; that is why trading-v2 replaced it.

Both: bid = price - spread / 2; ask = bid + spread. The spread is always an
even number of points, so the midpoint of bid and ask equals the feed price.
Bid never goes below 1 point; in that edge case the spread is kept and the
midpoint moves up. Only applied (fresh) prices feed the estimate.

`min_points`, `max_points`, `window`, `min_samples`, `vol_multiplier` and
`news_multiplier` are [DEFAULT] values, calibrated in P3 on recorded
prices and broker spreads.
"""

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction

from propfirm._checks import require_int, require_text


@dataclass(frozen=True, slots=True)
class SpreadConfig:
    min_points: int
    window: int = 20  # [DEFAULT] development placeholder
    vol_multiplier: Fraction = field(default_factory=lambda: Fraction(1))  # [DEFAULT]
    news_multiplier: int = 3  # [DEFAULT] development placeholder

    def __post_init__(self) -> None:
        require_int("min_points", self.min_points, minimum=1)
        require_int("window", self.window, minimum=1)
        require_int("news_multiplier", self.news_multiplier, minimum=1)
        if not isinstance(self.vol_multiplier, Fraction) or self.vol_multiplier < 0:
            raise ValueError("vol_multiplier must be a non-negative Fraction")


@dataclass(frozen=True, slots=True)
class MarketSpreadConfig:
    min_points: int  # the symbol's normal spread in a calm market
    max_points: int  # the widest spread outside news windows
    window: int = 20  # [DEFAULT] recent price changes considered
    min_samples: int = 3  # [DEFAULT] changes needed before the market can widen it
    vol_multiplier: Fraction = field(default_factory=lambda: Fraction(1))  # [DEFAULT]
    news_multiplier: int = 3  # [DEFAULT]

    def __post_init__(self) -> None:
        require_int("min_points", self.min_points, minimum=1)
        require_int("max_points", self.max_points, minimum=self.min_points)
        require_int("window", self.window, minimum=1)
        require_int("min_samples", self.min_samples, minimum=1)
        require_int("news_multiplier", self.news_multiplier, minimum=1)
        if self.min_samples > self.window:
            raise ValueError("min_samples must be <= window")
        if not isinstance(self.vol_multiplier, Fraction) or self.vol_multiplier < 0:
            raise ValueError("vol_multiplier must be a non-negative Fraction")


type AnySpreadConfig = SpreadConfig | MarketSpreadConfig


def lower_median(values: Iterable[int]) -> int:
    """The middle value; of the two middle values for an even count, the
    lower (an integer, so the result is exact and deterministic)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    return ordered[(len(ordered) - 1) // 2]


@dataclass(frozen=True, slots=True)
class NewsWindow:
    """Spreads widen on `symbols` while engine time is in [start_ts, end_ts).

    Built from the restricted-events calendar (M13). Blocking trades inside
    the window is a rule (step 5); this only widens the spread.
    """

    symbols: frozenset[str]
    start_ts: int
    end_ts: int

    def __post_init__(self) -> None:
        if not isinstance(self.symbols, frozenset) or not self.symbols:
            raise TypeError("symbols must be a non-empty frozenset")
        for symbol in self.symbols:
            require_text("symbol", symbol)
        require_int("start_ts", self.start_ts, minimum=0)
        require_int("end_ts", self.end_ts, minimum=0)
        if self.end_ts <= self.start_ts:
            raise ValueError("end_ts must be > start_ts")

    def covers(self, symbol: str, ts: int) -> bool:
        return symbol in self.symbols and self.start_ts <= ts < self.end_ts


def in_news(windows: Iterable[NewsWindow], symbol: str, ts: int) -> bool:
    return any(w.covers(symbol, ts) for w in windows)


@dataclass(frozen=True, slots=True)
class Quote:
    """A tradable price derived from one applied feed update."""

    symbol: str
    mid: int  # the feed price, points
    bid: int
    ask: int
    seq: int  # feed sequence number of the update it came from
    source_ts: int  # when the source produced the price, ms UTC
    receive_ts: int  # when the connector received it, ms UTC

    @property
    def spread(self) -> int:
        return self.ask - self.bid


def round_up_to_even(points: int) -> int:
    """Smallest even integer >= points."""
    return points + (points % 2)


@dataclass(frozen=True, slots=True)
class SpreadState:
    """A SpreadModel's memory: recent absolute changes and the last price."""

    changes: tuple[int, ...]
    last: int | None


class SpreadModel:
    """Tracks recent price changes for one symbol and quotes bid and ask."""

    def __init__(self, config: AnySpreadConfig) -> None:
        if not isinstance(config, (SpreadConfig, MarketSpreadConfig)):
            raise TypeError("config must be a SpreadConfig or MarketSpreadConfig")
        self._config: AnySpreadConfig = config
        self._changes: deque[int] = deque(maxlen=config.window)
        self._last: int | None = None

    def reconfigure(self, config: AnySpreadConfig) -> None:
        """New parameters (or model) from the next quote on; keeps the most
        recent changes that fit the new window."""
        if not isinstance(config, (SpreadConfig, MarketSpreadConfig)):
            raise TypeError("config must be a SpreadConfig or MarketSpreadConfig")
        kept = list(self._changes)[-config.window :]
        self._config = config
        self._changes = deque(kept, maxlen=config.window)

    def state(self) -> SpreadState:
        return SpreadState(tuple(self._changes), self._last)

    def restore(self, state: SpreadState) -> None:
        if len(state.changes) > self._config.window:
            raise ValueError("more changes than the window holds")
        for change in state.changes:
            require_int("change", change, minimum=0)
        if state.last is not None:
            require_int("last", state.last, minimum=1)
        if state.changes and state.last is None:
            raise ValueError("changes without a last price")
        self._changes = deque(state.changes, maxlen=self._config.window)
        self._last = state.last

    def observe(self, price: int) -> None:
        require_int("price", price, minimum=1)
        if self._last is not None:
            self._changes.append(abs(price - self._last))
        self._last = price

    def spread_points(self, news: bool) -> int:
        cfg = self._config
        if isinstance(cfg, MarketSpreadConfig):
            enough = len(self._changes) >= cfg.min_samples
            move = lower_median(self._changes) if enough else 0
            raw = min(max(cfg.min_points, math.ceil(move * cfg.vol_multiplier)), cfg.max_points)
        else:
            volatility = Fraction(sum(self._changes), len(self._changes)) if self._changes else 0
            raw = max(cfg.min_points, math.ceil(volatility * cfg.vol_multiplier))
        return round_up_to_even(raw * cfg.news_multiplier if news else raw)

    def quote(
        self, symbol: str, price: int, seq: int, source_ts: int, receive_ts: int, news: bool
    ) -> Quote:
        spread = self.spread_points(news)
        bid = max(1, price - spread // 2)
        return Quote(
            symbol=symbol,
            mid=price,
            bid=bid,
            ask=bid + spread,
            seq=seq,
            source_ts=source_ts,
            receive_ts=receive_ts,
        )
