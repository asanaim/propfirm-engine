"""The market break rule (build plan step 1, line 5; D-13.23).

As FTMO's funded Standard account: shortly before a closed period of 2
hours or more (the weekend, a holiday halt), a funded account's positions on
that market are closed and its pending orders cancelled; until the close no
new order opens. Evaluations hold over breaks. A daily 1 hour break does
not count.
"""

from dataclasses import replace
from typing import Any

import pytest

from propfirm.journal.inputs import Advance, OpenAccount, PlaceOrder
from propfirm.journal.machine import CODEC, Machine
from propfirm.ledger import Ledger
from propfirm.market.types import PriceUpdate
from propfirm.policy import PolicyError, load_policy, parse_policy
from propfirm.rules.products import PRODUCTS
from propfirm.service.setup import setup_from_policy
from propfirm.trading.engine import EngineState, TradingEngine
from propfirm.trading.events import (
    CloseReason,
    OrderCancelled,
    OrderRejected,
    PositionClosed,
    RejectReason,
)
from propfirm.trading.hours import ALWAYS, TradingHours, day_minute, new_york_ms, week_minute
from propfirm.trading.orders import OrderRequest, OrderType, Side
from tests.engine_rig import instruments
from tests.rules_rig import RulesRig, ny
from tests.test_policy import edited

FOREX = TradingHours(week_minute("Sun 17:00"), week_minute("Fri 17:00"), flat_before_minutes=5)
METAL = TradingHours(
    week_minute("Sun 18:00"),
    week_minute("Fri 17:00"),
    day_minute("17:00"),
    day_minute("18:00"),
    flat_before_minutes=5,
)
SIZE = 10_000_000
FUNDED = replace(PRODUCTS["lite"][-1], rsv_id="lite-break-test")
EVALUATION = replace(PRODUCTS["lite"][0], rsv_id="lite-eval-break-test")
FRIDAY = ny(2026, 11, 20, 16, 0)
FLAT_BY = ny(2026, 11, 20, 16, 55)
IN_WINDOW = ny(2026, 11, 20, 16, 57)
SUNDAY_OPEN = ny(2026, 11, 22, 17, 0, 1)


def rig_with_trades(rules: Any = FUNDED, hours: TradingHours = FOREX) -> RulesRig:
    """A long position and a pending limit order on EUR/USD, Friday 16:00."""
    rig = RulesRig(rules, SIZE, FRIDAY, hours=hours)
    rig.price(110_000, FRIDAY)
    rig.order(Side.BUY, 100, FRIDAY)
    rig.order(Side.BUY, 100, FRIDAY + 1_000, order_type=OrderType.LIMIT, level=109_000)
    return rig


def of(kind: type, events: list[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


# --- the schedule -------------------------------------------------------------------


def test_the_next_long_break() -> None:
    wednesday = ny(2026, 11, 18)
    weekend = (ny(2026, 11, 20, 17), ny(2026, 11, 22, 17))
    assert FOREX.next_long_break(wednesday) == weekend
    # the metals' daily 1 hour break is not long; their weekend opens at 18:00
    assert METAL.next_long_break(wednesday) == (weekend[0], ny(2026, 11, 22, 18))
    assert ALWAYS.next_long_break(wednesday) is None
    halt = (new_york_ms("2026-11-26 14:30"), new_york_ms("2026-11-26 18:00"))
    assert replace(ALWAYS, closures=(halt,)).next_long_break(wednesday) == halt
    short = (new_york_ms("2026-11-26 14:30"), new_york_ms("2026-11-26 16:00"))
    assert replace(ALWAYS, closures=(short,)).next_long_break(wednesday) is None


# --- the engine ---------------------------------------------------------------------


def test_a_funded_account_is_made_flat_at_the_flat_by_time() -> None:
    rig = rig_with_trades()
    assert not of(PositionClosed, rig.sup.advance(FLAT_BY - 1))
    events = rig.sup.advance(IN_WINDOW)
    (closed,) = of(PositionClosed, events)
    (cancelled,) = of(OrderCancelled, events)
    assert (closed.reason, closed.ts) == (CloseReason.MARKET_BREAK, FLAT_BY)
    assert (cancelled.reason, cancelled.ts) == (RejectReason.MARKET_BREAK, FLAT_BY)
    assert not rig.engine.positions("A") and not rig.engine.pending_orders("A")
    assert rig.engine.next_break("EUR/USD") == (FLAT_BY, ny(2026, 11, 20, 17), ny(2026, 11, 22, 17))


def test_a_price_after_the_flat_by_time_closes_at_the_price_before_it() -> None:
    rig = rig_with_trades()
    events = rig.price(111_000, IN_WINDOW)
    (closed,) = of(PositionClosed, events)
    assert (
        closed.ts == FLAT_BY and closed.close_price == 109_995
    )  # the bid then (mid 110_000), not the new price


def test_no_new_order_opens_in_the_window_and_trading_resumes_at_the_open() -> None:
    rig = rig_with_trades()
    events = rig.order(Side.BUY, 100, IN_WINDOW)
    assert [e.reason for e in of(OrderRejected, events)] == [RejectReason.MARKET_BREAK]
    rig.price(110_000, SUNDAY_OPEN)
    assert not of(OrderRejected, rig.order(Side.BUY, 100, SUNDAY_OPEN))
    assert len(rig.engine.positions("A")) == 1


def test_an_evaluation_holds_over_the_weekend() -> None:
    rig = rig_with_trades(EVALUATION)
    events = rig.sup.advance(IN_WINDOW)
    assert not of(PositionClosed, events) and not of(OrderCancelled, events)
    rig.price(110_000, IN_WINDOW)
    assert not of(OrderRejected, rig.order(Side.BUY, 100, IN_WINDOW))
    assert len(rig.engine.positions("A")) == 2


def test_an_unbound_account_is_untouched_beside_a_bound_one() -> None:
    rig = rig_with_trades()
    rig.engine.set_break_bound("A", False)  # as the Swing add-on will
    assert not of(PositionClosed, rig.sup.advance(IN_WINDOW))


def test_a_daily_one_hour_break_closes_nothing() -> None:
    tuesday = ny(2026, 11, 17, 16, 0)
    rig = RulesRig(FUNDED, SIZE, tuesday, hours=METAL)
    rig.price(110_000, tuesday)
    rig.order(Side.BUY, 100, tuesday)
    assert not of(PositionClosed, rig.sup.advance(ny(2026, 11, 17, 18, 30)))
    assert len(rig.engine.positions("A")) == 1


def test_hours_without_a_notice_close_nothing() -> None:
    rig = rig_with_trades(hours=replace(FOREX, flat_before_minutes=0))
    assert not of(PositionClosed, rig.sup.advance(IN_WINDOW))
    assert rig.engine.next_break("EUR/USD") is None


def test_one_long_advance_misses_no_break() -> None:
    monday = ny(2026, 11, 16, 9, 0)
    rig = RulesRig(FUNDED, SIZE, monday, hours=FOREX)
    rig.price(110_000, monday)
    rig.order(Side.BUY, 100, monday)
    (closed,) = of(PositionClosed, rig.sup.advance(ny(2026, 11, 21, 12)))  # to Saturday
    assert closed.ts == FLAT_BY
    # from the weekend over the reopening to the next window, in one step
    rig.sup.advance(ny(2026, 11, 27, 16, 58))
    assert dict(rig.engine.state().flattened)["EUR/USD"] == ny(2026, 11, 27, 16, 55)


def test_the_handled_break_survives_state_and_restore() -> None:
    rig = rig_with_trades()
    rig.sup.advance(IN_WINDOW)
    state = rig.engine.state()
    decoded = CODEC.decode(CODEC.encode(state))
    assert isinstance(decoded, EngineState) and decoded == state
    specs = {name: replace(spec, hours=FOREX) for name, spec in instruments().items()}
    engine = TradingEngine(specs, Ledger(), config_version="engine-test")
    engine.restore(decoded)
    engine.set_break_bound("A", True)
    later = ny(2026, 11, 20, 16, 59)
    assert engine.advance(later) == rig.engine.advance(later) == []
    assert engine.state() == rig.engine.state()


def test_an_older_state_keeps_its_encoding() -> None:
    rig = RulesRig(FUNDED, SIZE, FRIDAY)  # no hours: nothing handled
    assert "flattened" not in CODEC.encode(rig.engine.state())


# --- the shipped policy, end to end ----------------------------------------------------


def test_the_shipped_funded_stages_and_settings() -> None:
    policy = load_policy()
    funded = {r.rsv_id for r in policy.rule_sets() if r.flat_before_breaks}
    assert funded == {
        "lite-funded-v4",
        "classic-funded-v3",
        "one-step-funded-v5",
        "instant-funded-v4",
    }
    hours = {i.name: i.hours for i in policy.trading.instruments}
    assert all(h is not None and h.flat_before_minutes == 5 for h in hours.values())
    assert all(h is not None and h.long_break_minutes == 120 for h in hours.values())


def test_settings_default_to_off_and_bad_values_are_refused() -> None:
    def without(d: Any) -> None:
        d["trading"]["hours"].pop("flat_before_break_minutes")
        d["trading"]["hours"].pop("long_break_minutes")

    hours = [i.hours for i in parse_policy(edited(without)).trading.instruments]
    assert all(h is not None and h.flat_before_minutes == 0 for h in hours)
    for value in (-1, 1_441):  # whole minutes, at most a day

        def notice(d: Any, v: int = value) -> None:
            d["trading"]["hours"]["flat_before_break_minutes"] = v

        with pytest.raises((PolicyError, ValueError)):
            parse_policy(edited(notice))


def run(inputs: list[Any]) -> list[list[object]]:
    machine = Machine(CODEC.encode(setup_from_policy(load_policy())), {1: bytes(32)})
    machine.use_price_key(1)
    out = []
    for n, item in enumerate(inputs, start=1):
        events = CODEC.decode(machine.apply(f"{n}-0", item).record.events)
        assert isinstance(events, tuple)
        out.append(list(events))
    return out


def test_the_journal_closes_a_funded_position_and_replays_identically() -> None:
    def buy(order_id: str, account: str, ts: int) -> PlaceOrder:
        request = OrderRequest(order_id, account, "EUR/USD", Side.BUY, OrderType.MARKET, 100)
        return PlaceOrder(request, ts)

    inputs = [
        OpenAccount("F", "lite-funded-v4", 1_000_000, FRIDAY),
        OpenAccount("E", "lite-s1-v3", 1_000_000, FRIDAY),
        PriceUpdate("EUR/USD", 1, FRIDAY, FRIDAY, 110_000),
        buy("f1", "F", FRIDAY + 1),
        buy("e1", "E", FRIDAY + 2),
        Advance(IN_WINDOW),
        buy("f2", "F", IN_WINDOW + 1),
    ]
    out = run(inputs)
    closed = of(PositionClosed, out[5])
    assert [(c.account_id, c.reason) for c in closed] == [("F", CloseReason.MARKET_BREAK)]
    (refused,) = of(OrderRejected, out[6])
    assert refused.reason is RejectReason.MARKET_BREAK
    assert run(inputs) == out  # deterministic
