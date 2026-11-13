"""Loss limit as rule set fields (build plan step 1, line 1).

Kinds: static, trailing intraday (on equity) and trailing at end of day (on
the balance at each 17:00 New York reset, as FTMO's 1-Step). A trailing floor
may lock at the starting balance (`floor_locks_at_start`) and may restart
after a payout (`floor_restarts_after_payout`). Each is a field of the rule
set, so a change is a new rule-set version.
"""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.journal.machine import CODEC
from propfirm.policy import PolicyError, parse_policy, rule_set_view
from propfirm.rules.events import AccountStatus
from propfirm.rules.products import KIND_WORDS, PRODUCTS, MaxLossKind, RuleSet, max_loss_words
from propfirm.settings.overlay import from_json, limits_rule_set, stage_limits, to_json
from propfirm.trading.orders import Side
from tests.rules_rig import RulesRig, ny
from tests.test_policy import edited

SIZE = 10_000_000  # $100,000 (10 lots fit its volume cap)
LOSS = 1_000_000  # 10 %
MONDAY = ny(2026, 11, 16, 10)
RESET = ny(2026, 11, 16, 17)  # Monday's 17:00 New York reset
TUESDAY = ny(2026, 11, 17, 10)

ONE_STEP = PRODUCTS["one-step"][0]


def rules(kind: MaxLossKind, *, locks: bool = True, restarts: bool = True) -> RuleSet:
    return replace(
        ONE_STEP,
        rsv_id=f"test-{kind.value}-{locks}-{restarts}",
        max_loss=Fraction(10, 100),
        max_loss_kind=kind,
        floor_locks_at_start=locks,
        floor_restarts_after_payout=restarts,
        profit_target=Fraction(50, 100),  # out of reach: no pass interferes
    )


def max_floor(rig: RulesRig) -> int:
    return rig.sup.floors("A")[1]


def after_reset(rig: RulesRig) -> None:
    """A price just after Monday's reset rolls the trading day."""
    rig.price(110_000, RESET + 1_000)


# --- end-of-day trailing ----------------------------------------------------------


def test_end_of_day_floor_moves_only_at_the_reset_and_only_upwards() -> None:
    rig = RulesRig(rules(MaxLossKind.TRAILING_EOD), SIZE, MONDAY)
    assert max_floor(rig) == SIZE - LOSS
    rig.round_trip(50, MONDAY)  # +$500 closed
    assert max_floor(rig) == SIZE - LOSS  # intraday: unchanged
    after_reset(rig)
    assert max_floor(rig) == SIZE + 50_000 - LOSS  # highest end-of-day balance
    rig.round_trip(-20, TUESDAY)  # -$200 on Tuesday
    rig.price(110_000, ny(2026, 11, 17, 17, 0, 1))
    assert max_floor(rig) == SIZE + 50_000 - LOSS  # never moves down


def test_end_of_day_floor_ignores_floating_profit_at_the_reset() -> None:
    eod = RulesRig(rules(MaxLossKind.TRAILING_EOD), SIZE, MONDAY)
    intraday = RulesRig(rules(MaxLossKind.TRAILING, locks=False), SIZE, MONDAY)
    for rig in (eod, intraday):
        rig.price(110_000, MONDAY)
        rig.order(Side.BUY, 1_000, MONDAY)
        rig.price(110_060, MONDAY + 1_000)  # in profit, not closed
        rig.price(110_060, RESET + 1_000)
    equity = intraday.engine.equity("A")
    assert equity > SIZE and eod.engine.balance("A") == SIZE
    assert max_floor(eod) == SIZE - LOSS  # the balance did not move
    assert max_floor(intraday) == equity - LOSS  # equity did


def test_the_end_of_day_floor_breaches_on_equity_at_any_time() -> None:
    rig = RulesRig(rules(MaxLossKind.TRAILING_EOD), SIZE, MONDAY)
    rig.round_trip(50, MONDAY)
    after_reset(rig)  # day base $100,500; daily floor $97,500
    rig.price(110_000, TUESDAY)
    rig.order(Side.BUY, 1_000, TUESDAY)
    rig.price(109_650, TUESDAY + 1_000)  # about -$3,500 open
    assert rig.sup.status("A") is AccountStatus.BREACHED  # on equity, intraday
    assert max_floor(rig) == SIZE + 50_000 - LOSS


# --- lock at the starting balance ----------------------------------------------------


@pytest.mark.parametrize("kind", [MaxLossKind.TRAILING, MaxLossKind.TRAILING_EOD])
def test_a_locking_floor_stops_at_the_starting_balance(kind: MaxLossKind) -> None:
    rig = RulesRig(rules(kind, locks=True), SIZE, MONDAY)
    rig.round_trip(1_500, MONDAY)  # +$15,000, beyond the $10,000 allowance
    after_reset(rig)
    assert max_floor(rig) == SIZE


@pytest.mark.parametrize("kind", [MaxLossKind.TRAILING, MaxLossKind.TRAILING_EOD])
def test_a_floor_that_does_not_lock_keeps_trailing(kind: MaxLossKind) -> None:
    rig = RulesRig(rules(kind, locks=False), SIZE, MONDAY)
    rig.round_trip(1_500, MONDAY)
    after_reset(rig)
    assert max_floor(rig) == SIZE + 1_500_000 - LOSS


def test_restart_switch_works_on_the_end_of_day_floor() -> None:
    for restarts in (True, False):
        rig = RulesRig(rules(MaxLossKind.TRAILING_EOD, restarts=restarts), SIZE, MONDAY)
        rig.round_trip(50, MONDAY)
        after_reset(rig)
        rig.sup.debit_payout("A", 40_000, "po-1", TUESDAY)
        assert max_floor(rig) == SIZE + 50_000 - LOSS - (40_000 if restarts else 0)


def test_static_floor_is_unchanged() -> None:
    rig = RulesRig(rules(MaxLossKind.STATIC), SIZE, MONDAY)
    rig.round_trip(1_500, MONDAY)
    after_reset(rig)
    assert max_floor(rig) == SIZE - LOSS


# --- the fields -------------------------------------------------------------------------


def test_switches_belong_to_trailing_kinds_only() -> None:
    with pytest.raises(ValueError, match="could rise"):
        rules(MaxLossKind.STATIC, locks=False)
    with pytest.raises(ValueError, match="could restart"):
        rules(MaxLossKind.STATIC, restarts=False)
    with pytest.raises(TypeError):
        replace(ONE_STEP, floor_locks_at_start=1)  # type: ignore[arg-type]


def test_rule_sets_round_trip_through_the_journal_codec() -> None:
    for r in (
        rules(MaxLossKind.TRAILING_EOD, locks=False, restarts=False),
        rules(MaxLossKind.TRAILING),
    ):
        assert CODEC.decode(CODEC.encode(r)) == r
    # a rule set from before the field keeps its exact encoding
    assert "floor_locks_at_start" not in CODEC.encode(ONE_STEP)


def test_views_and_words() -> None:
    r = rules(MaxLossKind.TRAILING_EOD, locks=False)
    view = rule_set_view(r)
    assert (view["max_loss_kind"], view["floor_locks_at_start"]) == ("trailing_eod", False)
    assert max_loss_words(r) == "trailing at end of day, not capped at the starting balance"
    assert max_loss_words(rules(MaxLossKind.TRAILING)) == "trailing"
    assert max_loss_words(ONE_STEP) == "static"
    assert KIND_WORDS[MaxLossKind.TRAILING_EOD] == "end-of-day trailing "


# --- policy file ------------------------------------------------------------------------


def _one_step_stage(data: dict[str, Any]) -> dict[str, Any]:
    product = next(p for p in data["products"] if p["id"] == "one-step")
    stage: dict[str, Any] = product["stages"][0]
    return stage


def test_policy_file_takes_the_new_kind_and_switch() -> None:
    def change(data: dict[str, Any]) -> None:
        stage = _one_step_stage(data)
        stage["max_loss_kind"] = "trailing_eod"
        stage["max_loss_percent"] = "10"
        stage["floor_locks_at_start"] = False

    policy = parse_policy(edited(change))
    r = next(p for p in policy.products if p.id == "one-step").rule_sets[0]
    assert (r.max_loss_kind, r.floor_locks_at_start) == (MaxLossKind.TRAILING_EOD, False)


def test_policy_file_refuses_a_switch_on_a_static_floor() -> None:
    def change(data: dict[str, Any]) -> None:
        _one_step_stage(data)["floor_locks_at_start"] = False

    with pytest.raises(PolicyError, match="could rise"):
        parse_policy(edited(change))


def test_policy_file_names_the_kinds_it_accepts() -> None:
    def change(data: dict[str, Any]) -> None:
        _one_step_stage(data)["max_loss_kind"] = "trailing_intraday"

    with pytest.raises(PolicyError, match="static, trailing or trailing_eod"):
        parse_policy(edited(change))


# --- owners' change (stage limits) ---------------------------------------------------------


def test_owners_change_kind_and_switches_as_a_new_version() -> None:
    limits = stage_limits("one-step", "s1", "10", "3", "10", "trailing_eod", None, True)
    r = limits_rule_set(ONE_STEP, limits, 7)
    assert r.rsv_id == f"{ONE_STEP.rsv_id}-a7"
    assert (r.max_loss_kind, r.floor_locks_at_start, r.floor_restarts_after_payout) == (
        MaxLossKind.TRAILING_EOD,
        True,
        True,  # None kept the stage's switch
    )
    kind, value = to_json(limits)
    assert from_json(kind, value) == limits


def test_a_change_back_to_static_resets_the_switches() -> None:
    trailing = rules(MaxLossKind.TRAILING_EOD, locks=False, restarts=False)
    limits = stage_limits("one-step", "s1", "10", "3", "6", "static")
    r = limits_rule_set(trailing, limits, 8)
    assert (r.floor_locks_at_start, r.floor_restarts_after_payout) == (True, True)


def test_owners_cannot_switch_off_a_static_lock() -> None:
    with pytest.raises(PolicyError, match="trailing maximum loss only"):
        stage_limits("one-step", "s1", "10", "3", "6", "static", None, False)


def test_older_stage_limit_records_still_read() -> None:
    limits = stage_limits("one-step", "s1", "10", "3", "6", "trailing")
    kind, value = to_json(limits)
    assert not any(k.startswith("floor_") for k in value)  # unchanged form when unset
    assert from_json(kind, value) == limits
