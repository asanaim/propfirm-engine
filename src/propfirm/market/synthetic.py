"""Deterministic synthetic price feed for development and engine tests.

Never used to price a paid account. Properties:

- Same config and time range give the same updates on every machine: only
  integer randomness is used (Mersenne Twister integer draws, string seeds
  hashed with SHA-512), never floating point.
- Each symbol has its own random stream seeded from (seed, symbol), so adding
  or removing a symbol does not change the others.
- Updates arrive at irregular intervals, like a feed that sends a price only
  when a new quote exists.
- Silences (no updates) and delays (receive time lagging source time) can be
  injected to exercise the gap and delay guards.

Synthetic symbols move independently, so crosses are not arbitrage-consistent
with their legs. The starting levels below are synthetic, not market data.
"""

import heapq
import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from propfirm._checks import require_int, require_text
from propfirm.market.symbols import SYMBOLS
from propfirm.market.types import PriceUpdate

# Synthetic starting levels in points (price * 10**digits for each symbol).
DEFAULT_START_POINTS: Mapping[str, int] = {
    "EUR/USD": 110_000,
    "GBP/USD": 130_000,
    "USD/JPY": 150_000,
    "USD/CHF": 80_000,
    "AUD/USD": 65_000,
    "USD/CAD": 138_000,
    "NZD/USD": 59_000,
    "EUR/GBP": 85_000,
    "EUR/JPY": 165_000,
    "EUR/CHF": 93_000,
    "EUR/AUD": 170_000,
    "EUR/CAD": 152_000,
    "EUR/NZD": 186_000,
    "GBP/JPY": 195_000,
    "GBP/CHF": 107_000,
    "GBP/AUD": 200_000,
    "GBP/CAD": 179_000,
    "AUD/JPY": 97_500,
    "AUD/CAD": 90_000,
    "CAD/JPY": 108_700,
    "CHF/JPY": 187_500,
    "XAU/USD": 330_000,
    "XAG/USD": 35_000,
    "WTI": 6_500,
    "Brent": 6_800,
    "BTC/USD": 10_000_000,
    "ETH/USD": 300_000,
    "SOL/USD": 15_000,
    "XRP/USD": 25_000,
    "LTC/USD": 9_000,
}


@dataclass(frozen=True, slots=True)
class Silence:
    """No updates for `symbol` with source time in [start_ts, end_ts)."""

    symbol: str
    start_ts: int
    end_ts: int

    def __post_init__(self) -> None:
        _check_window(self.symbol, self.start_ts, self.end_ts)


@dataclass(frozen=True, slots=True)
class Delay:
    """Updates for `symbol` with source time in [start_ts, end_ts) arrive
    `delay_ms` later than they otherwise would."""

    symbol: str
    start_ts: int
    end_ts: int
    delay_ms: int

    def __post_init__(self) -> None:
        _check_window(self.symbol, self.start_ts, self.end_ts)
        require_int("delay_ms", self.delay_ms, minimum=0)


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    """Parameters of a synthetic feed.

    Attributes:
        seed: Any string; the same seed reproduces the same feed.
        symbols: Symbols to generate (default: all 30 launch symbols).
        interval_ms: Inclusive range of the gap between two updates of a symbol.
        base_delay_ms: Inclusive range of the normal source-to-receive delay.
        jump_per_mille: Chance per update, out of 1000, of a large move.
        start_points: Starting price per symbol, in points.
        silences: Injected periods with no updates.
        delays: Injected periods of extra delay.
    """

    seed: str
    symbols: tuple[str, ...] = tuple(SYMBOLS)
    interval_ms: tuple[int, int] = (50, 500)
    base_delay_ms: tuple[int, int] = (0, 300)
    jump_per_mille: int = 2
    start_points: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_START_POINTS))
    silences: tuple[Silence, ...] = ()
    delays: tuple[Delay, ...] = ()

    def __post_init__(self) -> None:
        require_text("seed", self.seed)
        if not self.symbols:
            raise ValueError("symbols must not be empty")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        for symbol in self.symbols:
            if symbol not in SYMBOLS:
                raise ValueError(f"unknown symbol {symbol!r}")
            if symbol not in self.start_points:
                raise ValueError(f"no start price for {symbol!r}")
            require_int(f"start_points[{symbol}]", self.start_points[symbol], minimum=1)
        _check_range("interval_ms", self.interval_ms, minimum=1)
        _check_range("base_delay_ms", self.base_delay_ms, minimum=0)
        require_int("jump_per_mille", self.jump_per_mille, minimum=0)
        if self.jump_per_mille > 1000:
            raise ValueError(f"jump_per_mille must be <= 1000, got {self.jump_per_mille}")


def generate(config: SyntheticConfig, start_ts: int, end_ts: int) -> Iterator[PriceUpdate]:
    """Yield every update with source time in [start_ts, end_ts), in arrival order.

    Arrival order is (receive_ts, symbol, seq). Per symbol, receive times never
    go backwards and sequence numbers run 1, 2, 3, ... without gaps.
    """
    require_int("start_ts", start_ts, minimum=0)
    require_int("end_ts", end_ts, minimum=0)
    if end_ts < start_ts:
        raise ValueError("end_ts must be >= start_ts")
    streams = [_symbol_updates(config, s, start_ts, end_ts) for s in config.symbols]
    return heapq.merge(*streams, key=lambda u: (u.receive_ts, u.symbol, u.seq))


def _symbol_updates(
    config: SyntheticConfig, symbol: str, start_ts: int, end_ts: int
) -> Iterator[PriceUpdate]:
    # seeded on purpose (deterministic replay); never for secrets
    rng = random.Random(f"{config.seed}|{symbol}")  # nosec B311
    start = config.start_points[symbol]
    unit = max(1, start // 50_000)
    jump = unit * 200
    silences = [w for w in config.silences if w.symbol == symbol]
    delays = [w for w in config.delays if w.symbol == symbol]
    lo, hi = config.interval_ms
    dlo, dhi = config.base_delay_ms

    price = start
    seq = 0
    last_receive = 0
    t = start_ts + rng.randint(lo, hi)
    while t < end_ts:
        silent = next((w for w in silences if w.start_ts <= t < w.end_ts), None)
        if silent is not None:
            t = silent.end_ts
            continue
        if rng.randrange(1000) < config.jump_per_mille:
            step = rng.randint(-jump, jump)
        else:
            step = rng.randint(-unit, unit)
        price = max(1, price + step)
        seq += 1
        delay = rng.randint(dlo, dhi)
        delay += sum(w.delay_ms for w in delays if w.start_ts <= t < w.end_ts)
        receive = max(last_receive, t + delay)
        last_receive = receive
        yield PriceUpdate(symbol=symbol, seq=seq, source_ts=t, receive_ts=receive, price=price)
        t += rng.randint(lo, hi)


def _check_window(symbol: str, start_ts: int, end_ts: int) -> None:
    require_text("symbol", symbol)
    require_int("start_ts", start_ts, minimum=0)
    require_int("end_ts", end_ts, minimum=0)
    if end_ts <= start_ts:
        raise ValueError("end_ts must be > start_ts")


def _check_range(name: str, value: tuple[int, int], *, minimum: int) -> None:
    lo, hi = value
    require_int(f"{name}[0]", lo, minimum=minimum)
    require_int(f"{name}[1]", hi, minimum=minimum)
    if hi < lo:
        raise ValueError(f"{name} must be (low, high) with low <= high, got {value}")
