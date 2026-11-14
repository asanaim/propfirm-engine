"""Pass conditions, tie rule, trailing floor, product catalogue."""

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction

import pytest

from propfirm.rules.events import AccountBreached, AccountStatus, BreachReason, StagePassed
from propfirm.rules.products import PRODUCTS, RULESETS, MaxLossKind, RuleSet, Stage, TieRule
from propfirm.trading.events import PositionClosed, RejectReason
from propfirm.trading.orders import Side
from tests.engine_rig import USD_100K, one, rejected
from tests.rules_rig import RulesRig, ny

LITE_S1 = RULESETS["lite-s1-v2"]  # sold before product line v1: has a 5K size
ONE_STEP_S1 = RULESETS["one-step-s1-v2"]  # static 6 % (before line v1)
INSTANT = PRODUCTS["instant"][0]
WEEK = [ny(2026, 10, d, 10) for d in (19, 20, 21, 22, 23, 26, 27)]  # Mon-Fri, Mon, Tue


def passed(events: Sequence[object]) -> bool:
    return any(isinstance(e, StagePassed) for e in events)


# --- catalogue ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rsv", "size", "floors"),
    [
        ("lite-s1-v2", USD_100K, (9_700_000, 9_400_000)),
        ("classic-s1-v2", USD_100K, (9_600_000, 9_200_000)),
        ("one-step-s1-v2", USD_100K, (9_700_000, 9_400_000)),
        ("instant-funded-v1", 2_500_000, (2_425_000, 2_375_000)),
    ],
)
def test_opening_floors_per_product(rsv: str, size: int, floors: tuple[int, int]) -> None:
    assert RulesRig(RULESETS[rsv], size, WEEK[0]).sup.floors("A") == floors


@pytest.mark.parametrize(
    ("rsv", "size", "target"),
    [
        ("lite-s1-v2", USD_100K, 10_600_000),  # +6 %
        ("classic-s2-v2", USD_100K, 10_500_000),  # +5 %
        ("one-step-s1-v2", 2_500_000, 2_750_000),  # +10 %
        ("instant-funded-v1", 2_500_000, None),  # funded: no target
    ],
)
def test_target_level_per_product(rsv: str, size: int, target: int | None) -> None:
    assert RulesRig(RULESETS[rsv], size, WEEK[0]).sup.target("A") == target


def test_catalogue_matches_confirmed_configuration() -> None:
    targets = {r.rsv_id: r.profit_target for r in RULESETS.values()}
    assert targets == {
        "lite-s1-v2": Fraction(6, 100),
        "lite-s2-v2": Fraction(6, 100),
        "lite-funded-v1": None,
        "classic-s1-v2": Fraction(10, 100),
        "classic-s2-v2": Fraction(5, 100),
        "classic-funded-v1": None,
        "one-step-s1-v2": Fraction(10, 100),
        "one-step-funded-v1": None,
        "instant-funded-v1": None,  # retired 12 Nov 2026; open accounts keep it
        "instant-funded-v2": None,
        # product line version 1 (13 Nov 2026); the versions above it are retired
        "lite-s1-v3": Fraction(6, 100),
        "lite-s2-v3": Fraction(6, 100),
        "lite-funded-v2": None,
        "one-step-s1-v3": Fraction(10, 100),
        "one-step-funded-v2": None,
        # market break rule (13 Nov 2026); the funded versions above it are retired
        "lite-funded-v3": None,
        "classic-funded-v2": None,
        "one-step-funded-v3": None,
        "instant-funded-v3": None,
        # news rule as FTMO (14 Nov 2026); the funded versions above it are retired
        "lite-funded-v4": None,
        "classic-funded-v3": None,
        "one-step-funded-v4": None,
        "instant-funded-v4": None,
        # 1-Step floor stops at the starting balance (D-14.10, 14 Nov 2026)
        "one-step-s1-v4": Fraction(10, 100),
        "one-step-funded-v5": None,
    }
    assert [s.stage for s in PRODUCTS["lite"]] == [Stage.S1, Stage.S2, Stage.FUNDED]
    assert INSTANT.max_loss_kind is MaxLossKind.TRAILING
    assert all(r.min_trading_days == 4 for r in RULESETS.values() if r.profit_target)
    assert all(r.tie_rule is TieRule.BREACH_WINS for r in RULESETS.values())  # D11
    assert {r.rsv_id for r in RULESETS.values() if r.consistency} == {
        "one-step-s1-v2",
        "one-step-funded-v1",
        "one-step-s1-v3",
        "one-step-funded-v2",
        "one-step-funded-v3",
        "one-step-funded-v4",
        "one-step-s1-v4",
        "one-step-funded-v5",
        "instant-funded-v1",
        "instant-funded-v2",
        "instant-funded-v3",
        "instant-funded-v4",
    }
    on_sale = PRODUCTS["one-step"]
    assert all(r.max_loss_kind is MaxLossKind.TRAILING_EOD for r in on_sale)  # D-13.17
    assert [r.rsv_id for r in on_sale] == ["one-step-s1-v4", "one-step-funded-v5"]
    assert all(r.floor_locks_at_start for r in on_sale)  # D-14.10: stops at the start
    assert not RULESETS["one-step-s1-v3"].floor_locks_at_start  # sold accounts keep D-13.20
    assert not RULESETS["one-step-funded-v4"].floor_locks_at_start
    assert RULESETS["one-step-funded-v4"].floor_restarts_after_payout
    assert all(500_000 not in r.sizes_cents for r in (*PRODUCTS["lite"], *on_sale))  # no 5K
    restart = {r.rsv_id for r in RULESETS.values() if not r.floor_restarts_after_payout}
    kept = {"instant-funded-v2", "instant-funded-v3", "instant-funded-v4", "one-step-funded-v5"}
    assert restart == kept  # D1: Instant keeps its floor; D-14.10: funded 1-Step too
    every = [r for st in PRODUCTS.values() for r in st]
    on_sale_funded = {r.rsv_id for r in every if r.stage is Stage.FUNDED}
    breaks = {r.rsv_id for r in every if r.flat_before_breaks}
    assert breaks == on_sale_funded  # D-13.23: every funded stage on sale, no evaluation
    news = {r.rsv_id for r in RULESETS.values() if r.news_breaches}
    # news rule as FTMO: funded stages from 14 Nov 2026; one-step-funded-v4 was
    # retired the same day by D-14.10 and keeps the rule for accounts sold on it
    assert news == on_sale_funded | {"one-step-funded-v4"}


def test_size_must_be_on_the_price_list() -> None:
    with pytest.raises(ValueError):
        RulesRig(PRODUCTS["classic"][0], 500_000, WEEK[0])  # no Classic 5K
    with pytest.raises(ValueError):
        RulesRig(INSTANT, 5_000_000, WEEK[0])  # no Instant 50K


def test_rule_set_validation() -> None:
    with pytest.raises(ValueError):
        replace(LITE_S1, profit_target=None)  # only funded stages lack a target
    with pytest.raises(ValueError):
        replace(LITE_S1, daily_loss=Fraction(0))
    with pytest.raises(ValueError, match="must not exceed max_loss"):
        replace(LITE_S1, daily_loss=LITE_S1.max_loss + Fraction(1, 100))
    assert replace(LITE_S1, daily_loss=LITE_S1.max_loss).daily_loss == LITE_S1.max_loss
    with pytest.raises(ValueError):
        replace(LITE_S1, sizes_cents=(1_234_567,))  # 3% is not whole cents
    with pytest.raises(ValueError):
        replace(LITE_S1, consistency_at_pass=True)  # no share given


# --- passing -----------------------------------------------------------------------------


def test_lite_passes_on_closed_balance_after_four_trading_days() -> None:
    rig = RulesRig(LITE_S1, USD_100K, WEEK[0])
    assert not passed(rig.round_trip(700, WEEK[0]))  # +$7,000: target met, 1 day
    for day in WEEK[1:3]:
        assert not passed(rig.round_trip(1, day))  # 2 and 3 days
    assert rig.sup.trading_days("A") == 3
    events = rig.round_trip(1, WEEK[3])  # 4th day
    stage = one(events, StagePassed)
    assert (stage.balance_cents, stage.trading_days, stage.rsv_id) == (
        10_703_000,
        4,
        "lite-s1-v2",
    )
    assert rig.sup.status("A") is AccountStatus.PASSED
    assert rejected(rig.order(Side.BUY, 100, WEEK[3] + 5_000)) is RejectReason.ACCOUNT_LOCKED


def test_floating_profit_never_passes_under_closed_balance() -> None:
    rig = RulesRig(LITE_S1, USD_100K, WEEK[0])
    for day in WEEK[:4]:
        rig.round_trip(1, day)
    rig.price(110_000, WEEK[4])
    rig.order(Side.BUY, 1_000, WEEK[4])
    events = rig.price(111_000, WEEK[4] + 1_000)  # +$9,900 floating, equity over target
    assert not passed(events)
    position = rig.engine.positions("A")[0].position_id
    assert passed(rig.sup.close_position("A", position, WEEK[4] + 2_000))


def test_target_met_with_a_position_still_open_waits_until_flat() -> None:
    rig = RulesRig(LITE_S1, USD_100K, WEEK[0])
    for day in WEEK[:3]:
        rig.round_trip(1, day)
    day = WEEK[3]
    rig.price(110_000, day)
    rig.order(Side.BUY, 1_000, day)  # will close for +$7,000
    rig.order(Side.BUY, 100, day)  # small position left open
    rig.price(110_710, day + 1_000)
    big = rig.engine.positions("A")[0].position_id
    events = rig.sup.close_position("A", big, day + 1_000)
    assert one(events, PositionClosed).pnl_cents == 700_000
    assert not passed(events)  # balance over target but not flat
    small = rig.engine.positions("A")[0].position_id
    assert passed(rig.sup.close_position("A", small, day + 2_000))


def test_one_step_needs_consistency_best_day_at_most_half() -> None:
    rig = RulesRig(ONE_STEP_S1, USD_100K, WEEK[0])
    rig.round_trip(600, WEEK[0])  # best day $6,000
    for day in WEEK[1:4]:
        assert not passed(rig.round_trip(150, day))
    # balance $110,500 >= target, 4 days, but $6,000 > 50% of $10,500
    assert rig.engine.balance("A") == 11_050_000
    assert not rig.sup.consistency_ok("A")
    events = rig.round_trip(150, WEEK[4])  # total $12,000: $6,000 is exactly 50%
    assert passed(events) and rig.sup.consistency_ok("A")


def test_funded_stages_never_pass() -> None:
    rig = RulesRig(RULESETS["lite-funded-v1"], USD_100K, WEEK[0])
    for day in WEEK[:5]:
        assert not passed(rig.round_trip(500, day))
    assert rig.sup.status("A") is AccountStatus.ACTIVE


def test_trading_day_counts_once_per_day_with_an_opening_fill() -> None:
    rig = RulesRig(LITE_S1, USD_100K, WEEK[0])
    rig.round_trip(1, WEEK[0])
    rig.round_trip(1, WEEK[0] + 60_000)
    assert rig.sup.trading_days("A") == 1
    rig.round_trip(1, WEEK[1])
    assert rig.sup.trading_days("A") == 2


# --- tie rule (D11) ---------------------------------------------------------------------


def tie_rig(rules: RuleSet) -> tuple[RulesRig, Sequence[object]]:
    """A stop-loss closes the last position in profit (balance over target,
    flat, 4 days) on the same update that takes equity below the daily floor
    set from yesterday's higher equity."""
    rig = RulesRig(rules, USD_100K, WEEK[0])
    for day in WEEK[:3]:
        rig.round_trip(1, day)
    rig.price(110_000, WEEK[3])
    rig.order(Side.BUY, 2_000, WEEK[3])  # 20 lots at 1.10005
    rig.price(110_605, WEEK[3] + 1_000)  # bid 1.10600: +$11,900 floating
    position = rig.engine.positions("A")[0].position_id
    rig.sup.modify_stops("A", position, 110_380, None, WEEK[3] + 1_000)
    rig.sup.advance(ny(2026, 10, 22, 17))  # reset: base = equity $111,930
    rig.price(110_605, WEEK[4] - 1_000)
    events = rig.price(110_380, WEEK[4])  # bid 1.10375 hits the stop: +$7,400 closed
    return rig, list(events)


def test_breach_wins_over_pass_on_the_same_check() -> None:
    rig, events = tie_rig(LITE_S1)
    assert one(events, PositionClosed).pnl_cents == 740_000
    breach = one(events, AccountBreached)
    assert breach.reasons == (BreachReason.DAILY_LOSS,)
    assert not passed(events)
    assert rig.sup.status("A") is AccountStatus.BREACHED


def test_pass_wins_when_configured() -> None:
    rig, events = tie_rig(replace(LITE_S1, tie_rule=TieRule.PASS_WINS))
    assert passed(events) and rig.sup.status("A") is AccountStatus.PASSED
    assert not any(isinstance(e, AccountBreached) for e in events)


# --- trailing max loss (Instant) ----------------------------------------------------------


def test_instant_trailing_floor_follows_equity_then_locks_at_start() -> None:
    day = WEEK[0]
    rig = RulesRig(INSTANT, 2_500_000, day)  # $25,000: max loss $1,250, daily $750
    rig.price(110_000, day)
    rig.order(Side.BUY, 100, day)  # 1 lot: $1 per point
    rig.price(110_805, day + 1_000)  # bid 1.10800: equity $25,795
    assert rig.sup.floors("A")[1] == 2_454_500  # high-water $25,795 - $1,250
    for price, at in ((110_550, 2_000), (110_300, 3_000)):  # equity $25,540, $25,290
        events = rig.price(price, day + at)
        assert not any(isinstance(e, AccountBreached) for e in events)
        assert rig.sup.floors("A")[1] == 2_454_500  # the floor never moves down
    events = rig.price(109_550, day + 5_000)  # bid 1.09545: equity $24,540 <= $24,545
    breach = one(events, AccountBreached)
    assert breach.reasons == (BreachReason.MAX_LOSS,)
    assert breach.max_floor_cents == 2_454_500


def test_instant_trailing_floor_stops_at_the_starting_balance() -> None:
    day = WEEK[0]
    rig = RulesRig(INSTANT, 2_500_000, day)
    rig.price(110_000, day)
    rig.order(Side.BUY, 100, day)
    rig.price(111_505, day + 1_000)  # equity $26,495: floor would be $25,245
    assert rig.sup.floors("A")[1] == 2_500_000  # capped at the start
    rig.price(112_005, day + 2_000)  # equity $26,995
    assert rig.sup.floors("A")[1] == 2_500_000
    events = rig.price(110_010, day + 3_000)  # bid 1.10005: equity $25,000
    assert one(events, AccountBreached).reasons == (BreachReason.MAX_LOSS,)


# --- the next reset (dashboard warning, audit M12) ------------------------------------------


def test_the_next_reset_floor_shows_a_breach_at_the_reset_coming() -> None:
    rig = RulesRig(LITE_S1, USD_100K, WEEK[0])  # daily 3 % = $3,000, max 6 %
    assert rig.sup.limits("A") == (300_000, 600_000)
    assert rig.sup.initial_balance("A") == USD_100K
    rig.price(110_000, WEEK[0])
    assert rig.sup.next_reset("A") == (ny(2026, 10, 19, 17), USD_100K - 300_000)
    rig.round_trip(200, WEEK[0])  # +$2,000 closed today: balance $102,000
    rig.price(110_000, WEEK[0] + 2_000)
    rig.order(Side.BUY, 1_000, WEEK[0] + 2_000)  # 10 lots at 1.10005
    rig.price(109_660, WEEK[0] + 3_000)  # bid 1.09655: -$3,500 floating, equity $98,500
    assert rig.sup.status("A") is AccountStatus.ACTIVE
    assert rig.sup.floors("A")[0] == 9_700_000  # today's floor: base $100,000
    reset, floor = rig.sup.next_reset("A")
    assert (reset, floor) == (ny(2026, 10, 19, 17), 9_900_000)  # from $102,000
    assert rig.engine.equity("A") == 9_850_000 <= floor  # so the reset will breach
    events = rig.sup.advance(reset)
    assert one(events, AccountBreached).reasons == (BreachReason.DAILY_LOSS,)
    assert rig.sup.next_reset("A")[0] == ny(2026, 10, 20, 17)
