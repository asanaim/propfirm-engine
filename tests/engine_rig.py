"""Shared test rig for the order engine."""

from collections import defaultdict
from dataclasses import replace
from fractions import Fraction

from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import SpreadConfig
from propfirm.market.symbols import SYMBOLS
from propfirm.market.types import PriceUpdate
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import EngineEvent, OrderRejected, RejectReason
from propfirm.trading.instruments import InstrumentSpec, default_instrument
from propfirm.trading.orders import OrderRequest, OrderType, Side

T0 = 1_790_000_000_000
USD_100K = 10_000_000
SYMS = ("EUR/USD", "USD/JPY", "GBP/USD", "EUR/GBP", "XAU/USD", "BTC/USD")
VERSION = "rsv-test-1"


def instruments() -> dict[str, InstrumentSpec]:
    """Fixed 10-point spread (vol_multiplier 0) so prices are easy to follow."""
    fixed = SpreadConfig(min_points=10, vol_multiplier=Fraction(0))
    return {s: replace(default_instrument(SYMBOLS[s]), spread=fixed) for s in SYMS}


class Rig:
    def __init__(self, balance: int = USD_100K, **kwargs: object) -> None:
        self.ledger = Ledger()
        self.engine = TradingEngine(
            instruments(),
            self.ledger,
            config_version=VERSION,
            guard_config=GuardConfig(delay_threshold_ms=2_000, default_gap_threshold_ms=60_000),
            **kwargs,  # type: ignore[arg-type]
        )
        self.seq: dict[str, int] = defaultdict(int)
        self.engine.open_account("A", balance, T0)

    def price(self, symbol: str, price: int, at: int = T0, lag: int = 0) -> list[EngineEvent]:
        self.seq[symbol] += 1
        return self.engine.on_price(PriceUpdate(symbol, self.seq[symbol], at, at + lag, price))

    def order(
        self,
        order_id: str,
        side: Side,
        volume: int = 100,
        symbol: str = "EUR/USD",
        order_type: OrderType = OrderType.MARKET,
        level: int | None = None,
        sl: int | None = None,
        tp: int | None = None,
        at: int = T0,
        account: str = "A",
    ) -> list[EngineEvent]:
        request = OrderRequest(order_id, account, symbol, side, order_type, volume, level, sl, tp)
        return self.engine.place_order(request, at)


def one[E](events: list[EngineEvent], kind: type[E]) -> E:
    found = [e for e in events if isinstance(e, kind)]
    assert len(found) == 1, events
    return found[0]


def rejected(events: list[EngineEvent]) -> RejectReason:
    return one(events, OrderRejected).reason
