"""The engine's indexes and selective checks give exactly the events of the
full scans they replace (review D3).

A seeded random session runs through two supervisors: the one shipped,
which checks only the accounts a price can move, and one that checks every
account after every input, as before 12 Nov 2026. Every input must produce
the same events, in the same order, and the same final state.
"""

import random
from collections.abc import Callable
from fractions import Fraction

import pytest

from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.types import PriceUpdate
from propfirm.rules.products import RULESETS
from propfirm.rules.supervisor import Event, RuleSupervisor
from propfirm.trading.book import OrderBook, PositionBook
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import EngineEvent
from propfirm.trading.orders import OrderRequest, OrderType, PendingOrder, Position, Side
from tests.engine_rig import instruments
from tests.rules_rig import ny

START = ny(2026, 11, 16, 9)
BASE = {"EUR/USD": 110_000, "USD/JPY": 15_000, "GBP/USD": 127_000, "EUR/GBP": 86_600}
RULES = ("lite-s1-v2", "one-step-s1-v2", "instant-funded-v1", "lite-funded-v1")


class CheckEveryAccount(RuleSupervisor):
    """The supervisor as it was: every account checked after every input."""

    def _run(
        self,
        ts: int,
        action: Callable[[], list[EngineEvent]],
        moved: Callable[[], set[str]] | None = None,
    ) -> list[Event]:
        return super()._run(ts, action, None)


def supervisor(kind: type[RuleSupervisor]) -> RuleSupervisor:
    engine = TradingEngine(
        instruments(),
        Ledger(),
        config_version="engine-test",
        guard_config=GuardConfig(delay_threshold_ms=2_000, default_gap_threshold_ms=60_000),
    )
    return kind(engine)


type Step = tuple[str, object, int]


def session(seed: int, accounts: int, steps: int) -> list[Step]:
    """A random but repeatable list of inputs: (kind, what, time)."""
    rng = random.Random(seed)
    out: list[Step] = []
    seq = dict.fromkeys(BASE, 0)
    price = dict(BASE)
    ts = START
    for i in range(accounts):
        rules = RULESETS[rng.choice(RULES)]
        out.append(("open", (f"A{i:03d}", rules.rsv_id, rng.choice(rules.sizes_cents)), ts))
    for step in range(steps):
        ts += rng.choice((200, 900, 3_000, 40_000)) + (6 * 3_600_000 if step % 97 == 96 else 0)
        roll = rng.random()
        if roll < 0.55:
            symbol = rng.choice(tuple(BASE))
            jump = rng.choice((1, 1, 1, 1, 8, 40))  # now and then a large move
            price[symbol] = max(1_000, price[symbol] + rng.randint(-30, 30) * jump)
            seq[symbol] += 1
            out.append(("price", PriceUpdate(symbol, seq[symbol], ts, ts, price[symbol]), ts))
        elif roll < 0.85:
            acc = f"A{rng.randrange(accounts):03d}"
            symbol = rng.choice(tuple(BASE))
            side = rng.choice((Side.BUY, Side.SELL))
            kind = rng.choice((OrderType.MARKET, OrderType.MARKET, OrderType.LIMIT))
            p, buy = price[symbol], side is Side.BUY
            level = None if kind is OrderType.MARKET else (p - 40 if buy else p + 40)
            stop = rng.choice((None, 300))
            sl = None if stop is None else (p - stop if buy else p + stop)
            volume = rng.choice((10, 100, 500))
            out.append(
                ("order", OrderRequest(f"o{step}", acc, symbol, side, kind, volume, level, sl), ts)
            )
        elif roll < 0.90:
            out.append(("close", f"A{rng.randrange(accounts):03d}", ts))
        elif roll < 0.95:
            out.append(("manage", (f"A{rng.randrange(accounts):03d}", rng.randrange(3)), ts))
        else:
            out.append(("advance", None, ts))
    return out


def apply(s: RuleSupervisor, step: Step) -> list[Event]:
    kind, what, ts = step
    if kind == "open":
        assert isinstance(what, tuple)
        acc, rsv, size = what
        return s.open_account(acc, RULESETS[rsv], size, ts)
    if kind == "price":
        assert isinstance(what, PriceUpdate)
        return s.on_price(what)
    if kind == "order":
        assert isinstance(what, OrderRequest)
        return s.place_order(what, ts)
    if kind == "manage":
        assert isinstance(what, tuple)
        acc, how = what
        positions, pending = s.engine.positions(acc), s.engine.pending_orders(acc)
        if how == 0 and pending:
            return s.cancel_order(acc, pending[0].request.order_id, ts)
        if how == 1 and positions:
            p = positions[0]
            sl = p.open_price - 500 if p.side is Side.BUY else p.open_price + 500
            return s.modify_stops(acc, p.position_id, sl, None, ts)
        if how == 2 and positions and s.status(acc).value == "active":
            return s.set_trailing_stop(f"t{ts}", acc, positions[-1].position_id, 200, 10, ts)
        return s.advance(ts)
    if kind == "close":
        assert isinstance(what, str)
        positions = s.engine.positions(what)
        if s.status(what).value == "active" and positions:
            return s.close_position(what, positions[0].position_id, ts)
    return s.advance(ts)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_selective_checks_give_the_events_of_checking_every_account(seed: int) -> None:
    fast, full = supervisor(RuleSupervisor), supervisor(CheckEveryAccount)
    produced = 0
    for step in session(seed, accounts=30, steps=1_200):
        a, b = apply(fast, step), apply(full, step)
        assert a == b
        produced += len(a)
    assert produced > 1_000  # the session did things: fills, closes, breaches
    assert fast.state() == full.state()
    assert fast.engine.state() == full.engine.state()
    statuses = {fast.status(a).value for a in fast.accounts()}
    assert "breached" in statuses and "active" in statuses


def test_a_price_checks_only_the_accounts_it_moves() -> None:
    s = supervisor(RuleSupervisor)
    for acc in ("A", "B", "C"):
        s.open_account(acc, RULESETS["lite-s1-v2"], 10_000_000, START)
    for symbol, p in BASE.items():
        s.on_price(PriceUpdate(symbol, 1, START, START, p))
    s.place_order(OrderRequest("a1", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100), START)
    s.place_order(OrderRequest("b1", "B", "EUR/GBP", Side.BUY, OrderType.MARKET, 100), START)
    engine = s.engine
    assert engine.accounts_priced_by("EUR/USD") == {"A"}
    assert engine.accounts_priced_by("GBP/USD") == {"B"}  # EUR/GBP profit is in pounds
    assert engine.accounts_priced_by("EUR/GBP") == {"B"}
    assert engine.accounts_priced_by("USD/JPY") == set()  # C holds nothing


def position(pid: str, account: str, symbol: str) -> Position:
    return Position(pid, account, symbol, Side.BUY, 10, 110_000, 0, None, None, Fraction(1))


def test_the_books_keep_the_main_order_in_every_index() -> None:
    book = PositionBook()
    for pid, acc, sym in [("p1", "A", "X"), ("p2", "B", "X"), ("p3", "A", "Y"), ("p4", "A", "X")]:
        book[pid] = position(pid, acc, sym)
    assert [p.position_id for p in book.of_account("A")] == ["p1", "p3", "p4"]
    assert [p.position_id for p in book.on_symbol("X")] == ["p1", "p2", "p4"]
    assert book.accounts_on("X") == ["A", "B"]
    book["p1"] = position("p1", "A", "X")  # replaced in place: keeps its place
    del book["p2"]
    assert [p.position_id for p in book.on_symbol("X")] == ["p1", "p4"]
    assert list(book) == ["p1", "p3", "p4"]
    with pytest.raises(ValueError):
        book["p3"] = position("p3", "B", "Y")  # a position keeps its account
    for mutate in (book.pop, book.clear, book.popitem):
        with pytest.raises(TypeError):
            mutate()
    orders = OrderBook()
    request = OrderRequest("o1", "A", "X", Side.BUY, OrderType.LIMIT, 10, 100_000)
    orders["o1"] = PendingOrder(request, 0)
    assert [i for i, _ in orders.on_symbol("X")] == ["o1"] and orders.of_account("B") == []
    del orders["o1"]
    assert orders.on_symbol("X") == [] and orders.stop_of("p1") is None
