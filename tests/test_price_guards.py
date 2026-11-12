"""The quote age limit (review D2) and the sanity filter (review D9).

D9 as the review found it: one price 5 % off the market, with a fresh time
stamp, breached a Lite 100K account and closed it at a large loss. With the
filter that update is held: it fills, triggers and breaches nothing until
the next update shows whether the move was real.
"""

from collections.abc import Sequence

import pytest

from propfirm.journal.machine import CODEC
from propfirm.market.guards import GuardConfig, lower_median
from propfirm.rules.events import AccountBreached
from propfirm.rules.products import PRODUCTS
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import (
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    PriceHeld,
    PriceReleased,
    RejectReason,
)
from propfirm.trading.orders import OrderType, Side
from tests.engine_rig import instruments
from tests.rules_rig import RulesRig, ny

LITE = PRODUCTS["lite"][0]
SIZE = 10_000_000  # $100,000
T = ny(2026, 11, 16, 10)
FILTER = GuardConfig(
    delay_threshold_ms=2_000,
    default_gap_threshold_ms=60_000,
    sanity_multiple=8,
    sanity_min_bp=50,  # 0.5 %
    sanity_min_changes=5,
)
AGE = GuardConfig(
    delay_threshold_ms=2_000,
    default_gap_threshold_ms=60_000,
    default_quote_age_ms=3_000,
    quote_age_ms={"XAU/USD": 10_000},
)


def calm(rig: RulesRig, start: int = 110_000, steps: int = 6) -> int:
    """A calm market: `steps` prices 5 points apart, one second apart.
    Returns the time of the last one."""
    for i in range(steps):
        rig.price(start + 5 * i, T + i * 1_000)
    return T + (steps - 1) * 1_000


def kinds(events: Sequence[object]) -> list[type]:
    return [type(e) for e in events]


# --- the sanity filter (D9) ------------------------------------------------------------


def test_the_reviews_bad_tick_is_held_and_breaches_nothing() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)  # last applied 110_025
    rig.order(Side.BUY, 1_000, at)  # 10 lots: $10 a point
    bad = rig.price(104_500, at + 1_000)  # 5 % down, with a fresh time stamp
    assert kinds(bad) == [PriceHeld]
    assert bad[0] == PriceHeld(at + 1_000, "EUR/USD", 7, 104_500, 110_025)
    assert rig.engine.quote("EUR/USD").mid == 110_025  # type: ignore[union-attr]
    assert rig.engine.positions("A")  # still open
    # the next update is back in line with the market: the held one is dropped
    back = rig.price(110_030, at + 2_000)
    assert PriceReleased(at + 2_000, "EUR/USD", 7, False) in back
    assert rig.engine.quote("EUR/USD").mid == 110_030  # type: ignore[union-attr]
    assert not any(isinstance(e, (AccountBreached, PositionClosed)) for e in back)


def test_without_the_filter_the_same_tick_breaches_the_account() -> None:
    rig = RulesRig(LITE, SIZE, T)  # configurations before 12 Nov 2026: filter off
    at = calm(rig)
    rig.order(Side.BUY, 1_000, at)
    events = rig.price(104_500, at + 1_000)
    assert any(isinstance(e, AccountBreached) for e in events)


def test_a_real_move_is_applied_on_the_next_update() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)
    rig.order(Side.BUY, 1_000, at)
    assert kinds(rig.price(104_500, at + 1_000)) == [PriceHeld]
    confirmed = rig.price(104_480, at + 2_000)  # the market really is there
    assert PriceReleased(at + 2_000, "EUR/USD", 7, True) in confirmed
    assert rig.engine.quote("EUR/USD").mid == 104_480  # type: ignore[union-attr]
    assert any(isinstance(e, AccountBreached) for e in confirmed)  # on a confirmed price


def test_a_second_bad_tick_takes_the_place_of_the_first() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)
    rig.price(104_500, at + 1_000)
    events = rig.price(116_000, at + 2_000)  # out of line with both
    assert kinds(events) == [PriceReleased, PriceHeld]
    assert events[0] == PriceReleased(at + 2_000, "EUR/USD", 7, False)
    assert isinstance(events[1], PriceHeld) and events[1].seq == 8
    assert rig.engine.quote("EUR/USD").mid == 110_025  # type: ignore[union-attr]


def test_a_held_price_fills_no_pending_order() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)
    placed = rig.order(Side.BUY, 100, at, order_type=OrderType.LIMIT, level=105_000)
    assert any(isinstance(e, OrderPlaced) for e in placed)
    assert not any(isinstance(e, PositionOpened) for e in rig.price(104_500, at + 1_000))


def test_small_moves_and_moves_under_the_percentage_pass() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)
    # 500 points: 100 times the median move, but under 0.5 % of the price
    assert PriceHeld not in kinds(rig.price(110_525, at + 1_000))


def test_with_little_history_the_percentage_alone_decides() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    rig.price(110_000, T)
    rig.price(110_005, T + 1_000)  # one change: below sanity_min_changes
    assert kinds(rig.price(110_700, T + 2_000)) == [PriceHeld]  # 0.63 %
    assert PriceHeld not in kinds(rig.price(110_500, T + 3_000))  # 0.45 % from 110_005


def test_a_volatile_market_raises_the_bar() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    for i, p in enumerate((110_000, 110_100, 110_000, 110_100, 110_000, 110_100)):
        rig.price(p, T + i * 1_000)  # 100-point swings: the bar is 800 points
    assert PriceHeld not in kinds(rig.price(110_800, T + 7_000))  # 700 points, 0.64 %
    assert kinds(rig.price(111_700, T + 8_000)) == [PriceHeld]  # 900 points


def test_the_held_update_survives_a_snapshot() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=FILTER)
    at = calm(rig)
    rig.price(104_500, at + 1_000)
    state = rig.engine.state()
    assert [u.price for u in state.held] == [104_500]
    assert CODEC.decode(CODEC.encode(state)) == state
    fresh = TradingEngine(
        instruments(), rig.ledger, config_version="engine-test", guard_config=FILTER
    )
    fresh.restore(state)
    assert fresh.state() == state


def test_old_snapshots_decode_with_nothing_held() -> None:
    rig = RulesRig(LITE, SIZE, T)
    state = rig.engine.state()
    assert state.held == ()
    assert '"held"' not in CODEC.encode(state)  # the default is left out: old records match


def test_the_median_is_exact() -> None:
    assert lower_median([5, 1, 3, 9]) == 3  # the lower of the middle two
    with pytest.raises(ValueError):
        lower_median([])
    assert not FILTER.out_of_line(110_000, 110_000, [])
    off = GuardConfig()
    assert not off.out_of_line(1, 1_000_000, [1, 1, 1, 1, 1])  # filter off


# --- the quote age limit (D2) ----------------------------------------------------------


def test_a_market_order_on_an_old_price_is_refused() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=AGE)
    rig.price(110_000, T)
    assert any(isinstance(e, PositionOpened) for e in rig.order(Side.BUY, 100, T + 3_000))
    late = rig.order(Side.BUY, 100, T + 3_001)
    rejected = [e for e in late if isinstance(e, OrderRejected)]
    assert rejected and rejected[0].reason is RejectReason.STALE_QUOTE
    # a new price: accepted again
    rig.price(110_010, T + 4_000)
    assert any(isinstance(e, PositionOpened) for e in rig.order(Side.BUY, 100, T + 4_500))


def test_a_close_on_an_old_price_is_refused_and_a_pending_order_is_not() -> None:
    rig = RulesRig(LITE, SIZE, T, guard=AGE)
    rig.price(110_000, T)
    rig.order(Side.BUY, 100, T)
    position = rig.engine.positions("A")[0].position_id
    closing = rig.sup.close_position("A", position, T + 5_000)
    rejected = [e for e in closing if isinstance(e, OrderRejected)]
    assert rejected and rejected[0].reason is RejectReason.STALE_QUOTE
    assert rig.engine.positions("A")  # still open
    limit = rig.order(Side.BUY, 100, T + 5_000, order_type=OrderType.LIMIT, level=109_000)
    assert any(isinstance(e, OrderPlaced) for e in limit)  # fills later, on a new price


def test_the_limit_is_set_per_symbol() -> None:
    assert (AGE.quote_age_limit("EUR/USD"), AGE.quote_age_limit("XAU/USD")) == (3_000, 10_000)
    assert GuardConfig().quote_age_limit("EUR/USD") == 0  # off before 12 Nov 2026
    rig = RulesRig(LITE, SIZE, T, guard=AGE)
    rig.price(330_000, T, symbol="XAU/USD")
    gold = rig.order(Side.BUY, 10, T + 9_000, symbol="XAU/USD")
    assert any(isinstance(e, PositionOpened) for e in gold)
