"""Trading hours per symbol (build plan step 1, line 4; review D6).

Outside its hours a symbol takes no new order, close or stop change
(`market_closed`); pending orders and stops wait; a price that arrives is
not applied; cancelling stays allowed.
"""

from dataclasses import replace
from typing import Any

import pytest

from propfirm.journal.machine import CODEC
from propfirm.market.symbols import AssetClass
from propfirm.market.types import PriceUpdate
from propfirm.policy import PolicyError, load_policy, parse_policy
from propfirm.rules.products import PRODUCTS
from propfirm.service.feeder import in_hours
from propfirm.trading.events import (
    OrderRejected,
    PositionClosed,
    RejectReason,
    UpdateOutsideHours,
)
from propfirm.trading.hours import ALWAYS, TradingHours, day_minute, new_york_ms, week_minute
from propfirm.trading.orders import OrderType, Side
from tests.engine_rig import instruments
from tests.rules_rig import RulesRig, ny
from tests.test_policy import edited

FOREX = TradingHours(week_minute("Sun 17:00"), week_minute("Fri 17:00"))
METAL = TradingHours(
    week_minute("Sun 18:00"),
    week_minute("Fri 17:00"),
    day_minute("17:00"),
    day_minute("18:00"),
)
SIZE = 10_000_000
LITE = replace(PRODUCTS["lite"][0], rsv_id="lite-hours-test")
FRIDAY = ny(2026, 11, 20, 16, 0)
CLOSED = ny(2026, 11, 21, 12, 0)  # Saturday
SUNDAY_OPEN = ny(2026, 11, 22, 17, 0, 1)


# --- the schedule -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "at", "open_"),
    [
        (FOREX, ny(2026, 11, 22, 16, 59), False),  # Sunday before the open
        (FOREX, ny(2026, 11, 22, 17, 0), True),
        (FOREX, ny(2026, 11, 18, 17, 30), True),  # no daily break
        (FOREX, ny(2026, 11, 20, 16, 59), True),
        (FOREX, ny(2026, 11, 20, 17, 0), False),  # Friday close
        (METAL, ny(2026, 11, 17, 17, 30), False),  # the daily break
        (METAL, ny(2026, 11, 17, 18, 0), True),
        (METAL, ny(2026, 11, 22, 17, 30), False),  # Sunday opens at 18:00
        (ALWAYS, ny(2026, 11, 21, 3, 0), True),  # crypto on a Saturday
    ],
)
def test_the_weekly_window_and_the_daily_break(hours: TradingHours, at: int, open_: bool) -> None:
    assert hours.is_open(at) is open_


def test_closures_close_even_an_always_open_market() -> None:
    # a Wednesday-to-Thursday closure (25 Dec 2026 is a Friday, whose 17:00
    # is the weekly close anyway)
    holiday = (new_york_ms("2026-12-23 17:00"), new_york_ms("2026-12-24 17:00"))
    hours = replace(FOREX, closures=(holiday,))
    assert not hours.is_open(ny(2026, 12, 24, 9))
    assert hours.is_open(ny(2026, 12, 24, 17))
    assert not replace(ALWAYS, closures=(holiday,)).is_open(ny(2026, 12, 24, 9))


def test_daylight_saving_keeps_new_york_time() -> None:
    # the same 17:00 New York open in summer (UTC-4) and winter (UTC-5)
    assert FOREX.is_open(ny(2026, 7, 5, 17, 0)) and not FOREX.is_open(ny(2026, 7, 5, 16, 59))
    assert FOREX.is_open(ny(2026, 12, 6, 17, 0)) and not FOREX.is_open(ny(2026, 12, 6, 16, 59))


def test_bad_schedules_are_refused() -> None:
    with pytest.raises(ValueError):
        week_minute("Sunday 17:00")
    with pytest.raises(ValueError):
        day_minute("24:00")
    with pytest.raises(ValueError):
        TradingHours(10, 10)
    with pytest.raises(ValueError):
        TradingHours(0, 10, break_start=5)
    with pytest.raises(ValueError):
        TradingHours(0, 10, closures=((20, 10),))
    with pytest.raises(ValueError):
        TradingHours(0, 10, closures=((0, 10), (5, 20)))  # overlapping


def test_the_shipped_hours() -> None:
    shipped = {i.name: i.hours for i in load_policy().trading.instruments}
    hours = {name: h for name, h in shipped.items() if h is not None}
    assert set(hours) == set(shipped)  # every symbol has hours
    assert hours["BTC/USD"].always
    assert not hours["EUR/USD"].is_open(ny(2026, 12, 25, 9))  # Christmas
    assert hours["BTC/USD"].is_open(ny(2026, 12, 25, 9))
    assert not hours["WTI"].is_open(ny(2026, 11, 17, 17, 30))  # oil's daily break


# --- the engine ---------------------------------------------------------------------


def rig_with_long(hours: TradingHours = FOREX) -> tuple[RulesRig, str]:
    rig = RulesRig(LITE, SIZE, FRIDAY, hours=hours)
    rig.price(110_000, FRIDAY)
    rig.order(Side.BUY, 100, FRIDAY, sl=109_800)
    return rig, rig.engine.positions("A")[0].position_id


def rejected(events: list[Any]) -> list[RejectReason]:
    return [e.reason for e in events if isinstance(e, OrderRejected)]


def test_nothing_trades_while_the_market_is_closed() -> None:
    rig, position = rig_with_long()
    pending = rig.order(Side.BUY, 100, FRIDAY + 1_000, order_type=OrderType.LIMIT, level=109_000)
    assert not rejected(pending)
    assert rejected(rig.order(Side.BUY, 100, CLOSED)) == [RejectReason.MARKET_CLOSED]
    assert rejected(rig.sup.close_position("A", position, CLOSED)) == [RejectReason.MARKET_CLOSED]
    assert rejected(rig.sup.modify_stops("A", position, 109_700, None, CLOSED)) == [
        RejectReason.MARKET_CLOSED
    ]
    # cancelling a pending order stays allowed
    assert not rejected(
        rig.sup.cancel_order("A", rig.engine.pending_orders("A")[0].request.order_id, CLOSED)
    )
    assert not rig.engine.is_open("EUR/USD")


def test_a_stray_price_outside_hours_moves_nothing_and_fires_no_stop() -> None:
    rig, _ = rig_with_long()
    before = rig.engine.quote("EUR/USD")
    events = rig.price(109_000, CLOSED)  # far through the stop-loss
    assert [type(e) for e in events if isinstance(e, UpdateOutsideHours)] == [UpdateOutsideHours]
    assert not any(isinstance(e, PositionClosed) for e in events)
    assert rig.engine.quote("EUR/USD") == before
    assert len(rig.engine.positions("A")) == 1


def test_the_stop_fires_on_the_first_price_after_the_open() -> None:
    rig, _ = rig_with_long()
    events = rig.price(109_000, SUNDAY_OPEN)
    closed = [e for e in events if isinstance(e, PositionClosed)]
    assert len(closed) == 1 and closed[0].reason.value == "stop_loss"


def test_a_symbol_without_hours_trades_at_any_time() -> None:
    rig = RulesRig(LITE, SIZE, FRIDAY)  # older setups: no hours
    rig.price(110_000, CLOSED)
    assert not rejected(rig.order(Side.BUY, 100, CLOSED))
    assert rig.engine.is_open("EUR/USD")


# --- records, policy, feeder ---------------------------------------------------------------


def test_hours_round_trip_and_older_instruments_keep_their_encoding() -> None:
    spec = instruments()["EUR/USD"]
    assert "hours" not in CODEC.encode(spec)
    with_hours = replace(spec, hours=replace(FOREX, closures=((1, 2),)))
    assert CODEC.decode(CODEC.encode(with_hours)) == with_hours
    event = UpdateOutsideHours(1, "EUR/USD", 3)
    assert CODEC.decode(CODEC.encode(event)) == event


def test_a_policy_without_hours_is_always_open() -> None:
    policy = parse_policy(edited(lambda d: d["trading"].pop("hours")))
    assert all(i.hours is None for i in policy.trading.instruments)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["trading"]["hours"]["forex"].update(open="Sunday 17:00"),
        lambda d: d["trading"]["hours"]["metal"].update(daily_break="17:00"),
        lambda d: d["trading"]["hours"]["closures"][0].update(classes=["stocks"]),
        lambda d: d["trading"]["hours"]["closures"][0].update(to="2026-12-24 16:00"),
    ],
)
def test_bad_hours_in_the_policy_are_refused(change: Any) -> None:
    with pytest.raises(PolicyError):
        parse_policy(edited(change))


def test_the_feeder_sends_nothing_into_a_closed_market() -> None:
    hours = {"EUR/USD": FOREX, "BTC/USD": None}
    updates = [
        PriceUpdate("EUR/USD", 1, FRIDAY, FRIDAY, 110_000),
        PriceUpdate("EUR/USD", 2, CLOSED, CLOSED, 110_001),
        PriceUpdate("BTC/USD", 1, CLOSED, CLOSED, 8_000_000),
        PriceUpdate("EUR/USD", 3, SUNDAY_OPEN, SUNDAY_OPEN, 110_002),
    ]
    out = list(in_hours(iter(updates), hours))
    assert [(u.symbol, u.seq, u.price) for u in out] == [
        ("EUR/USD", 1, 110_000),
        ("BTC/USD", 1, 8_000_000),
        ("EUR/USD", 2, 110_002),  # numbered on without a gap
    ]
    assert AssetClass.FOREX.value == "forex"
