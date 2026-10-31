"""Deterministic replay and engine invariants over random sessions.

A synthetic feed (with injected delays and silences) is interleaved with a
random but seeded script of trader commands. The same seed must give the same
events and the same ledger; at every step the ledger balances, every fill
used an applied price at the correct side, and balances equal funding plus
realized P&L.
"""

import random
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.ledger import Entry, Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import Quote
from propfirm.market.synthetic import Delay, Silence, SyntheticConfig, generate
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import (
    EngineEvent,
    PositionClosed,
    PositionOpened,
    TrailingMoved,
    TrailingPlaced,
)
from propfirm.trading.instruments import default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side

T0 = 1_790_000_000_000
SYMS = ("EUR/USD", "USD/JPY", "XAU/USD", "BTC/USD")
FUNDING = {"A": 10_000_000, "B": 500_000}


@dataclass
class Session:
    events: list[EngineEvent]
    entries: tuple[Entry, ...]


def run_session(seed: int) -> Session:
    rng = random.Random(seed)
    feed = SyntheticConfig(
        seed=f"replay-{seed}",
        symbols=SYMS,
        delays=(Delay("EUR/USD", T0 + 20_000, T0 + 30_000, delay_ms=3_000),),
        silences=(Silence("XAU/USD", T0 + 40_000, T0 + 55_000),),
    )
    specs = {s: spec for s, spec in default_instruments().items() if s in SYMS}
    ledger = Ledger()
    engine = TradingEngine(
        specs,
        ledger,
        config_version="rsv-replay",
        guard_config=GuardConfig(delay_threshold_ms=2_000, default_gap_threshold_ms=5_000),
    )
    events: list[EngineEvent] = []
    for account, cents in FUNDING.items():
        events += engine.open_account(account, cents, T0)
    applied: dict[tuple[str, int], Quote] = {}
    pnl: dict[str, int] = dict.fromkeys(FUNDING, 0)
    levels: dict[str, tuple[int, int, int, str, Side, str]] = {}  # trailing orders
    n = 0

    def record(new: list[EngineEvent], from_command: bool) -> None:
        for e in new:
            if isinstance(e, PositionOpened | PositionClosed):
                quote = applied[(e.symbol, e.price_seq)]  # an applied (fresh) price
                if from_command:
                    assert not engine.is_frozen(e.symbol)
            if isinstance(e, PositionOpened):
                assert e.price == (quote.ask if e.side is Side.BUY else quote.bid)
                assert (e.price_source_ts, e.price_receive_ts) == (
                    quote.source_ts,
                    quote.receive_ts,
                )
            if isinstance(e, PositionClosed):
                assert e.close_price == (quote.bid if e.side is Side.BUY else quote.ask)
                pnl[e.account_id] += e.pnl_cents
            if isinstance(e, TrailingPlaced):
                levels[e.order_id] = (e.level, e.distance, e.step, e.kind.value, e.side, e.symbol)
            if isinstance(e, TrailingMoved):  # by an applied price, a step in the trader's favour
                level, distance, step, kind, side, symbol = levels[e.order_id]
                moved_by = applied[(symbol, e.price_seq)]
                if (kind == "entry") == (side is Side.BUY):  # follows the ask
                    assert e.level == moved_by.ask + distance <= level - step
                else:
                    assert e.level == moved_by.bid - distance >= level + step
                levels[e.order_id] = (e.level, distance, step, kind, side, symbol)
        events.extend(new)

    for update in generate(feed, T0, T0 + 90_000):
        new = engine.on_price(update)
        quote = engine.quote(update.symbol)
        if quote is not None and quote.seq == update.seq:
            applied[(update.symbol, update.seq)] = quote
        record(new, from_command=False)
        if rng.randrange(4) == 0:
            n += 1
            record(random_command(engine, rng, n, update.receive_ts), from_command=True)
        ledger.verify()
        for account, cents in FUNDING.items():
            assert engine.balance(account) == cents + pnl[account]
            assert all(p.volume >= 1 for p in engine.positions(account))
    return Session(events, ledger.entries())


def random_command(
    engine: TradingEngine, rng: random.Random, n: int, now: int
) -> list[EngineEvent]:
    account = rng.choice(list(FUNDING))
    ts = now + rng.randrange(50)
    action = rng.randrange(8)
    positions = engine.positions(account)
    if action == 6 and positions:
        position = rng.choice(positions)
        distance, step = rng.choice([0, 1, 20, 150]), rng.choice([0, 1, 5, 40])
        return engine.set_trailing_stop(f"t{n}", account, position.position_id, distance, step, ts)
    if action == 7:
        distance, step = rng.choice([1, 30, 120, 400]), rng.choice([1, 10])
        side = rng.choice([Side.BUY, Side.SELL])
        lots = rng.choice([0, 10, 100])
        return engine.place_trailing_entry(
            f"o{n}", account, rng.choice(SYMS), side, lots, distance, step, ts
        )
    if action == 4 and positions:
        position = rng.choice(positions)
        volume = rng.choice([None, 1, position.volume])
        return engine.close_position(account, position.position_id, ts, volume=volume)
    if action == 5 and positions:
        position = rng.choice(positions)
        quote = engine.quote(position.symbol)
        mid = quote.mid if quote else 1_000
        sl = rng.choice([None, mid - rng.randrange(1, 500), mid + rng.randrange(1, 500)])
        tp = rng.choice([None, mid - rng.randrange(1, 500), mid + rng.randrange(1, 500)])
        return engine.modify_stops(account, position.position_id, sl, tp, ts)
    working = [o.request.order_id for o in engine.pending_orders(account)]
    working += [o.order_id for o, _ in engine.trailing_orders(account)]
    if action == 3 and working:
        return engine.cancel_order(account, rng.choice(working), ts)
    symbol = rng.choice(SYMS)
    side = rng.choice([Side.BUY, Side.SELL])
    order_type = rng.choice([OrderType.MARKET, OrderType.LIMIT, OrderType.STOP])
    quote = engine.quote(symbol)
    mid = quote.mid if quote else 1_000
    level = None if order_type is OrderType.MARKET else mid + rng.randrange(-300, 301)
    if level is not None and level < 1:
        level = 1
    sl = rng.choice([None, mid - rng.randrange(1, 600), mid + rng.randrange(1, 600)])
    tp = rng.choice([None, mid - rng.randrange(1, 600), mid + rng.randrange(1, 600)])
    sl = sl if sl is None or sl >= 1 else None
    tp = tp if tp is None or tp >= 1 else None
    volume = rng.choice([0, 1, 10, 100, 1_000, 6_000])
    request = OrderRequest(f"o{n}", account, symbol, side, order_type, volume, level, sl, tp)
    return engine.place_order(request, ts)


@settings(max_examples=12, deadline=None)
@given(st.integers(min_value=0, max_value=2**32))
def test_same_inputs_give_identical_events_and_ledger(seed: int) -> None:
    first = run_session(seed)
    second = run_session(seed)
    assert first.events == second.events
    assert first.entries == second.entries


def test_sessions_actually_trade() -> None:
    """Guard against a vacuous replay test: the script must produce fills,
    closes, rejections and freezes."""
    kinds = {type(e).__name__ for e in run_session(7).events}
    assert {
        "PositionOpened",
        "PositionClosed",
        "OrderRejected",
        "OrderPlaced",
        "SymbolFrozen",
        "SymbolResumed",
        "StaleUpdate",
        "TrailingPlaced",
        "TrailingMoved",
    } <= kinds
    events = run_session(7).events
    assert any(isinstance(e, PositionOpened) and e.order_type == "trailing" for e in events)
    assert any(isinstance(e, PositionClosed) and e.reason == "trailing_stop" for e in events)
