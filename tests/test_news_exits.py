"""News exits (D15): stop-loss, take-profit and trailing-stop fills inside a
news trade block stand, are recorded as violations, and their profit (never
their loss) is kept out of the profit target.

Lite 100K: target $106,000 after 4 trading days; daily floor = day base -
$3,000. EUR/USD 10 lots (volume 1,000) = $10 per point; spread 10 points.
"""

from collections.abc import Sequence
from dataclasses import replace

from propfirm.journal.machine import CODEC
from propfirm.rules.events import AccountBreached, AccountStatus, NewsExitViolation, StagePassed
from propfirm.rules.news import EventScope, RestrictedEvent
from propfirm.rules.products import PRODUCTS, RuleSet
from propfirm.rules.supervisor import RuleSupervisor
from propfirm.trading.events import CloseReason, PositionClosed, TrailingStopSet
from propfirm.trading.orders import OrderRequest, OrderType, Side, Trail
from tests.engine_rig import USD_100K, one
from tests.rules_rig import RulesRig, ny

LITE_S1 = PRODUCTS["lite"][0]
WEEK = [ny(2026, 10, d, 10) for d in (19, 20, 21, 22)]  # Monday to Thursday, 10:00
EVENT = WEEK[3] + 600_000  # Thursday 10:10 New York
INSIDE = EVENT + 10_000
AFTER = EVENT + 120_001


def rig_with_three_days(rules: RuleSet = LITE_S1) -> RulesRig:
    """Three trading days of +$10 each, then Thursday with a USD event at 10:10."""
    nfp = RestrictedEvent("nfp", EVENT, EventScope.CURRENCY, "USD")
    rig = RulesRig(rules, USD_100K, WEEK[0], news=[nfp])
    for day in WEEK[:3]:
        rig.round_trip(1, day)
    return rig


def violations(events: Sequence[object]) -> list[NewsExitViolation]:
    return [e for e in events if isinstance(e, NewsExitViolation)]


def test_take_profit_inside_a_block_stands_but_its_profit_does_not_pass_the_target() -> None:
    rig = rig_with_three_days()
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 1_000, WEEK[3], tp=110_700)  # ask 1.10005
    events = rig.price(110_710, INSIDE)  # bid 1.10705: take-profit inside the block
    closed = one(events, PositionClosed)
    assert (closed.reason, closed.pnl_cents) == (CloseReason.TAKE_PROFIT, 700_000)
    violation = one(events, NewsExitViolation)
    assert (
        violation.close_id,
        violation.symbol,
        violation.reason,
        violation.pnl_cents,
        violation.excluded_cents,
        violation.rsv_id,
    ) == (closed.close_id, "EUR/USD", CloseReason.TAKE_PROFIT, 700_000, 700_000, "lite-s1-v1")
    assert rig.engine.balance("A") == 10_703_000  # the fill stands
    assert rig.sup.trading_days("A") == 4
    assert not [e for e in events if isinstance(e, StagePassed)]  # $106,030 - $7,000 < target
    assert rig.sup.status("A") is AccountStatus.ACTIVE
    assert rig.sup.news_excluded("A") == 700_000

    events = rig.round_trip(600, AFTER)  # +$6,000 outside the block
    assert not violations(events)
    assert one(events, StagePassed).balance_cents == 11_303_000


def test_stop_loss_inside_a_block_is_recorded_and_its_loss_stands() -> None:
    rig = rig_with_three_days()
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 100, WEEK[3], sl=109_900)
    events = rig.price(109_890, INSIDE)  # bid 1.09885
    violation = one(events, NewsExitViolation)
    assert (violation.reason, violation.pnl_cents, violation.excluded_cents) == (
        CloseReason.STOP_LOSS,
        -12_000,
        0,
    )
    assert rig.sup.news_excluded("A") == 0
    assert rig.engine.balance("A") == USD_100K + 3_000 - 12_000


def test_trailing_stop_inside_a_block_is_a_news_exit() -> None:
    rig = rig_with_three_days()
    rig.price(110_000, WEEK[3])
    request = OrderRequest(
        "t1", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100, trailing_stop=Trail(100, 1)
    )
    one(rig.sup.place_order(request, WEEK[3]), TrailingStopSet)
    rig.price(110_500, WEEK[3] + 1_000)  # stop follows to 1.10395
    events = rig.price(110_300, INSIDE)  # bid 1.10295
    violation = one(events, NewsExitViolation)
    assert (violation.reason, violation.pnl_cents, violation.excluded_cents) == (
        CloseReason.TRAILING_STOP,
        29_000,
        29_000,
    )


def test_exits_outside_the_block_and_breach_close_outs_are_not_violations() -> None:
    rig = rig_with_three_days()
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 100, WEEK[3], tp=110_100)
    events = rig.price(110_110, EVENT - 120_001)  # one ms before the block
    one(events, PositionClosed)
    assert not violations(events)

    rig.price(110_000, EVENT - 120_001)
    rig.order(Side.BUY, 1_000, EVENT - 120_001)
    events = rig.price(109_600, INSIDE)  # -$4,100 floating: breaches the daily floor
    one(events, AccountBreached)
    assert one(events, PositionClosed).reason is CloseReason.BREACH
    assert not violations(events)


def test_rule_set_can_keep_news_exit_profit_in_the_target() -> None:
    rig = rig_with_three_days(replace(LITE_S1, exclude_news_exit_profit=False))
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 1_000, WEEK[3], tp=110_700)
    events = rig.price(110_710, INSIDE)
    assert one(events, NewsExitViolation).excluded_cents == 0  # still recorded
    one(events, StagePassed)


def test_excluded_profit_survives_a_state_snapshot() -> None:
    rig = rig_with_three_days()
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 1_000, WEEK[3], tp=110_700)
    rig.price(110_710, INSIDE)
    state = rig.sup.state()
    assert CODEC.decode(CODEC.encode(state)) == state
    restored = RuleSupervisor(rig.engine)
    restored.restore(state)
    assert restored.news_excluded("A") == 700_000
