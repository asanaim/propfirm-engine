"""Instant's trailing floor after a payout (owner decision K7, review D1).

Version 1 (retired): a payout lowers the high-water mark by the amount paid,
so the floor restarts 5 % below the new balance and the trader gets the
whole loss allowance back. Version 2: once the floor reaches the starting
balance it stays there; a payout may not take the account to or below it.
"""

from typing import Any

from propfirm.customers.payouts import FAIL, PASS, eligibility
from propfirm.journal.inputs import InputRejectReason
from propfirm.rules.events import AccountStatus, PayoutDebited
from propfirm.rules.products import PRODUCTS, RULESETS
from tests.rules_rig import RulesRig, ny
from tests.test_payouts import NOW, POLICY, funded

SIZE = 2_500_000  # $25,000; 5 % trailing = $1,250
DAY = ny(2026, 11, 16, 10)


def rig_in_profit(rsv: str) -> RulesRig:
    rig = RulesRig(RULESETS[rsv], SIZE, DAY)
    rig.round_trip(130, DAY)  # +$1,300: the floor locks at the starting balance
    assert rig.engine.balance("A") == SIZE + 130_000
    assert rig.sup.floors("A")[1] == SIZE
    return rig


def test_the_new_instant_is_on_sale_and_keeps_its_floor() -> None:
    (instant,) = PRODUCTS["instant"]
    assert (instant.rsv_id, instant.floor_restarts_after_payout) == ("instant-funded-v3", False)
    assert RULESETS["instant-funded-v1"].floor_restarts_after_payout  # open accounts keep it


def test_version_1_restarts_the_floor_after_a_payout() -> None:
    rig = rig_in_profit("instant-funded-v1")
    events = rig.sup.debit_payout("A", 130_000, "po-1", DAY + 5_000)
    assert any(isinstance(e, PayoutDebited) for e in events)
    assert rig.engine.balance("A") == SIZE
    assert rig.sup.floors("A")[1] == SIZE - 125_000  # the whole allowance again


def test_version_2_keeps_the_floor_and_refuses_a_payout_that_reaches_it() -> None:
    rig = rig_in_profit("instant-funded-v2")
    assert rig.sup.payout_refusal("A", 130_000, "po-1") == "payout_breaches_floor"
    assert InputRejectReason("payout_breaches_floor") is InputRejectReason.PAYOUT_BREACHES_FLOOR
    assert rig.sup.payout_refusal("A", 129_999, "po-1") is None
    rig.sup.debit_payout("A", 129_999, "po-1", DAY + 5_000)
    assert rig.engine.balance("A") == SIZE + 1
    assert rig.sup.floors("A")[1] == SIZE  # it stays at the starting balance
    assert rig.sup.status("A") is AccountStatus.ACTIVE


def test_a_floor_not_yet_locked_does_not_move_down_either() -> None:
    rig = RulesRig(RULESETS["instant-funded-v2"], SIZE, DAY)
    rig.round_trip(60, DAY)  # +$600: high-water mark 2,560,000; floor 2,435,000
    assert rig.sup.floors("A")[1] == SIZE + 60_000 - 125_000
    rig.sup.debit_payout("A", 60_000, "po-1", DAY + 5_000)
    assert rig.sup.floors("A")[1] == SIZE + 60_000 - 125_000  # unchanged by the payout


def eligible(**change: Any) -> Any:
    return eligibility(POLICY, funded(product=POLICY.product("instant"), **change), NOW)


def test_the_payable_profit_leaves_the_balance_above_a_kept_floor() -> None:
    # $600 profit on a $10,000 account, floor kept at the starting balance
    capped = eligible(kept_floor_cents=1_000_000)
    assert capped.payable_cents == 59_999
    assert capped.debit_cents == 59_999
    floor = next(c for c in capped.checks if c.name == "floor")
    assert floor.status == PASS
    assert "at most 59999 cents" in floor.detail
    # a floor below the profit does not bind; a floor at the balance leaves nothing
    assert eligible(kept_floor_cents=990_000).payable_cents == 60_000
    none_left = eligible(kept_floor_cents=1_060_000)
    assert none_left.payable_cents == 0
    assert {c.name: c.status for c in none_left.checks}["floor"] == FAIL
    # violations come out of the same room
    assert eligible(kept_floor_cents=1_000_000, violations_cents=10_000).payable_cents == 49_999
    # rule sets whose floor restarts, or static floors: no such check
    assert "floor" not in {c.name for c in eligible().checks}
