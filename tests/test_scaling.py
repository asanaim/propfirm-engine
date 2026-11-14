"""Scaling (build plan step 2, line 4; D-14.8): the check and the engine step."""

from dataclasses import replace

import pytest

from propfirm.customers.scaling import DAY_MS, Period, check, step_size
from propfirm.journal.inputs import ScaleAccount
from propfirm.journal.machine import CODEC
from propfirm.policy import load_policy
from propfirm.rules.events import AccountScaled
from propfirm.rules.products import PRODUCTS
from tests.engine_rig import USD_100K, one
from tests.rules_rig import RulesRig, ny

SCALING = load_policy().scaling
T0 = ny(2026, 11, 17, 10, 0)


def period(**changes: object) -> Period:
    base = Period(
        acc="ACC00000001",
        size_cents=10_000_000,
        since=0,
        start_balance_cents=10_000_000,
        balance_cents=11_000_000,  # +10 %
        taken_cents=0,
        paid_payouts=3,
        risk_class=None,
        active=True,
        trader_allocation_cents=10_000_000,
        steps=0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_step_is_due_when_all_four_conditions_hold() -> None:
    v = check(SCALING, period(), 60 * DAY_MS)
    assert v.due and v.add_cents == 2_000_000  # 20 % of the starting balance
    assert [n for n, ok, _ in v.checks if not ok] == []


@pytest.mark.parametrize(
    ("changes", "now", "failing"),
    [
        ({}, 59 * DAY_MS, "period"),
        ({"balance_cents": 10_999_999}, 60 * DAY_MS, "net_profit"),
        ({"paid_payouts": 2}, 60 * DAY_MS, "paid_payouts"),
        ({"risk_class": "C"}, 60 * DAY_MS, "class"),
        ({"active": False}, 60 * DAY_MS, "active"),
        ({"trader_allocation_cents": 38_000_001}, 60 * DAY_MS, "allocation"),
    ],
)
def test_each_condition_alone_stops_a_step(changes: dict, now: int, failing: str) -> None:  # type: ignore[type-arg]
    v = check(SCALING, period(**changes), now)
    assert not v.due
    assert [n for n, ok, _ in v.checks if not ok] == [failing]


def test_profit_paid_out_counts_toward_the_step() -> None:
    v = check(SCALING, period(balance_cents=10_200_000, taken_cents=800_000), 60 * DAY_MS)
    assert v.due  # 2,000 left in the account plus 8,000 debited by payouts


def test_class_b_and_unclassed_scale_and_steps_round_down() -> None:
    assert check(SCALING, period(risk_class="B"), 60 * DAY_MS).due
    assert step_size(SCALING, 1_000_001) == 200_000


def test_the_engine_adds_the_allocation_and_moves_the_floors() -> None:
    funded = PRODUCTS["lite"][-1]
    r = RulesRig(funded, USD_100K, T0)
    r.price(110_000, T0)
    state = r.sup._state("A")
    events = r.sup.scale("A", 2_000_000, "SCL00000001", T0 + 1)
    scaled = one(events, AccountScaled)
    assert (scaled.add_cents, scaled.initial_balance_cents) == (2_000_000, USD_100K + 2_000_000)
    assert r.engine.balance("A") == USD_100K + 2_000_000
    assert (state.initial_balance, state.day_base, state.high_water) == (
        USD_100K + 2_000_000,
        USD_100K + 2_000_000,
        USD_100K + 2_000_000,
    )
    assert r.sup.scaling_refusal("A", "SCL00000001") == "duplicate_scaling"
    item = ScaleAccount("A", 2_000_000, "SCL00000002", T0 + 2)
    assert CODEC.decode(CODEC.encode(item)) == item
    with pytest.raises(ValueError):
        ScaleAccount("A", 0, "SCL00000003", 1)
