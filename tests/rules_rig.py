"""Shared test rig for the rules supervisor."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import NewsWindow
from propfirm.market.symbols import SYMBOLS
from propfirm.market.types import PriceUpdate
from propfirm.rules.clock import NEW_YORK
from propfirm.rules.news import RestrictedEvent, trade_blocks
from propfirm.rules.products import RuleSet
from propfirm.rules.supervisor import Event, RuleSupervisor
from propfirm.trading.engine import TradingEngine
from propfirm.trading.orders import OrderRequest, OrderType, Side
from propfirm.trading.swaps import SwapTerms
from tests.engine_rig import SYMS, instruments


def ny(y: int, mo: int, d: int, h: int = 12, mi: int = 0, s: int = 0) -> int:
    """ms UTC of a New York wall-clock time."""
    local = datetime(y, mo, d, h, mi, s, tzinfo=NEW_YORK)
    return int(local.timestamp()) * 1000


class RulesRig:
    """One supervised account "A" on the fixed-spread test instruments
    (EUR/USD: 10-point spread, 1 lot = $1 per point)."""

    def __init__(
        self,
        rules: RuleSet,
        size: int,
        start: int,
        news: Iterable[RestrictedEvent] = (),
        spread_windows: Iterable[NewsWindow] = (),
        guard: GuardConfig | None = None,
        swap: SwapTerms | None = None,
    ) -> None:
        self.ledger = Ledger()
        specs = instruments()
        if swap is not None:  # every test symbol pays this swap
            specs = {name: replace(spec, swap=swap) for name, spec in specs.items()}
        engine = TradingEngine(
            specs,
            self.ledger,
            config_version="engine-test",
            guard_config=guard
            or GuardConfig(delay_threshold_ms=2_000, default_gap_threshold_ms=60_000),
            news_windows=spread_windows,
            trade_blocks=trade_blocks(news, {s: SYMBOLS[s] for s in SYMS}),
        )
        self.sup = RuleSupervisor(engine)
        self.engine = engine
        self.seq: dict[str, int] = defaultdict(int)
        self.n = 0
        self.sup.open_account("A", rules, size, start)

    def price(self, price: int, at: int, symbol: str = "EUR/USD") -> list[Event]:
        self.seq[symbol] += 1
        return self.sup.on_price(PriceUpdate(symbol, self.seq[symbol], at, at, price))

    def order(
        self,
        side: Side,
        volume: int,
        at: int,
        symbol: str = "EUR/USD",
        order_type: OrderType = OrderType.MARKET,
        level: int | None = None,
        sl: int | None = None,
        tp: int | None = None,
    ) -> list[Event]:
        self.n += 1
        request = OrderRequest(f"o{self.n}", "A", symbol, side, order_type, volume, level, sl, tp)
        return self.sup.place_order(request, at)

    def round_trip(self, points: int, at: int, volume: int = 1_000) -> list[Event]:
        """Buy at 1.10000 and close `points` above the open (after spread):
        P&L = points * volume / 100 dollars on EUR/USD."""
        events = self.price(110_000, at)
        events += self.order(Side.BUY, volume, at)
        events += self.price(110_000 + points + 10, at + 1_000)
        position = self.engine.positions("A")[0].position_id
        events += self.sup.close_position("A", position, at + 1_000)
        return events
