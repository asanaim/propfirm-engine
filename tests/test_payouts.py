"""Payable profit and payout eligibility (M18), without a database."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.customers.payouts import FAIL, PASS, FundedAccount, eligibility
from propfirm.money import share_floor
from propfirm.policy import DEFAULT_POLICY, AccessPolicy, PolicyError, load_policy, parse_policy
from propfirm.rules.clock import NEW_YORK_17

POLICY = load_policy()
DAY = 86_400_000
FUNDED = 1_791_000_000_000  # 2026-10-03 (UTC), a Saturday
NOW = FUNDED + 17 * DAY  # 2026-10-20, past the waiting period (D1 + 14 days)
D1 = NEW_YORK_17.day_start(FUNDED + 2 * DAY)
D2 = NEW_YORK_17.day_start(FUNDED + 3 * DAY)
D3 = NEW_YORK_17.day_start(FUNDED + 4 * DAY)


def funded(**change: Any) -> FundedAccount:
    account = FundedAccount(
        acc="ACC00000003",
        product=POLICY.product("lite"),
        stage="funded",
        consistency=None,
        funded_at=FUNDED,
        baseline_cents=1_000_000,
        fee_paid_cents=4_400,
        payouts_paid=0,
        last_payout_at=None,
        status="active",
        locked=False,
        balance_cents=1_060_000,  # $600 profit
        equity_cents=1_050_000,
        open_positions=0,
        pending_orders=0,
        trading_day_ids=(D1, D2, D3),
        day_pnl=((D1, 20_000), (D2, 20_000), (D3, 20_000)),
        cif_status="registered",
        tier2_approved=True,
        residence_country="DE",
        document_country="DE",
        wallet_added_at=FUNDED,
    )
    return replace(account, **change)


def statuses(**change: Any) -> dict[str, str]:
    return {c.name: c.status for c in eligibility(POLICY, funded(**change), NOW).checks}


def test_payable_profit_and_the_split() -> None:
    e = eligibility(POLICY, funded(), NOW)
    assert (e.basis, e.measured_cents, e.baseline_cents, e.payable_cents) == (
        "closed_balance",
        1_060_000,
        1_000_000,
        60_000,
    )
    assert (e.split_percent, e.trader_share_cents, e.firm_share_cents) == ("80", 48_000, 12_000)
    assert (e.fee_refund_cents, e.trader_amount_cents, e.debit_cents) == (0, 48_000, 60_000)
    assert e.eligible and e.trading_days == 3
    assert {c.name: c.status for c in e.checks} == {
        "funded": PASS,
        "waiting_period": PASS,
        "trading_days": PASS,
        "consistency": PASS,
        "minimum": PASS,
        "flat": PASS,
        "identity": PASS,
        "residence": PASS,
        "cases": PASS,
        "no_pending_payout": PASS,
        "wallet": PASS,
    }


def test_no_profit_below_the_baseline_and_equity_basis() -> None:
    below = eligibility(POLICY, funded(balance_cents=990_000), NOW)
    assert (below.payable_cents, below.trader_amount_cents, below.eligible) == (0, 0, False)
    assert {c.name: c.status for c in below.checks}["minimum"] == FAIL
    equity = replace(POLICY, payouts=replace(POLICY.payouts, basis="equity"))
    e = eligibility(equity, funded(), NOW)
    assert (e.measured_cents, e.payable_cents) == (1_050_000, 50_000)


def test_the_trader_share_rounds_down_to_the_cent() -> None:
    e = eligibility(POLICY, funded(balance_cents=1_006_253), NOW)  # P = 6,253 cents
    assert (e.trader_share_cents, e.firm_share_cents) == (5_002, 1_251)  # 5,002.4 -> 5,002
    assert share_floor(6_253, Fraction(4, 5)) == 5_002
    assert share_floor(7, Fraction(1, 2)) == 3


def test_the_fee_refund_comes_with_the_payout_the_product_names() -> None:
    one_step = POLICY.product("one-step")
    assert one_step.fee_refund_with_payout == 3
    second = eligibility(POLICY, funded(product=one_step, payouts_paid=1), NOW)
    third = eligibility(POLICY, funded(product=one_step, payouts_paid=2), NOW)
    assert second.fee_refund_cents == 0
    assert (third.fee_refund_cents, third.trader_amount_cents) == (4_400, 48_000 + 4_400)


def test_a_maximum_caps_the_trader_amount() -> None:
    capped = replace(POLICY, payouts=replace(POLICY.payouts, maximum_cents=10_000))
    e = eligibility(capped, funded(), NOW)
    # 10,000 of the 48,000 share: G = 10,000 / 0.8 = 12,500 leaves the account
    assert (e.trader_amount_cents, e.debit_cents, e.payable_cents) == (10_000, 12_500, 60_000)
    odd = replace(POLICY, payouts=replace(POLICY.payouts, maximum_cents=10_001))
    assert eligibility(odd, funded(), NOW).debit_cents == 12_502  # 12,501.25 rounds up
    removed = eligibility(capped, funded(violations_cents=1_000), NOW)
    assert (removed.trader_amount_cents, removed.debit_cents) == (10_000, 13_500)  # G + V
    high = replace(POLICY, payouts=replace(POLICY.payouts, maximum_cents=48_000))
    assert eligibility(high, funded(), NOW).debit_cents == 60_000  # not capped: all of P


def test_the_maximum_counts_the_fee_refund() -> None:
    capped = replace(POLICY, payouts=replace(POLICY.payouts, maximum_cents=10_000))
    one_step = POLICY.product("one-step")
    third = eligibility(capped, funded(product=one_step, payouts_paid=2), NOW)
    share = 10_000 - 4_400
    assert (third.fee_refund_cents, third.trader_amount_cents) == (4_400, 10_000)
    assert third.debit_cents == -(-share * 5 // 4)  # ceil(5,600 / 0.8) = 7,000


def test_violations_leave_the_account_with_the_payout() -> None:
    e = eligibility(POLICY, funded(violations_cents=20_000), NOW)
    assert (e.payable_cents, e.debit_cents) == (40_000, 60_000)  # P + V
    none = eligibility(POLICY, funded(violations_cents=90_000), NOW)
    assert (none.payable_cents, none.debit_cents) == (0, 0)


def test_the_wallet_and_a_pending_payout() -> None:
    assert statuses(wallet_added_at=None)["wallet"] == FAIL
    detail = {
        c.name: c.detail for c in eligibility(POLICY, funded(wallet_added_at=None), NOW).checks
    }
    assert detail["wallet"] == "no payout wallet"
    assert POLICY.payouts.wallet_cooling_off_hours == 0  # as FTMO, FundedNext, The5ers
    assert statuses(wallet_added_at=NOW)["wallet"] == PASS  # usable at once
    hour = 3_600_000
    wait = replace(POLICY, payouts=replace(POLICY.payouts, wallet_cooling_off_hours=48))

    def waited(added: int) -> str:
        found = eligibility(wait, funded(wallet_added_at=added), NOW)
        return {c.name: c.status for c in found.checks}["wallet"]

    assert waited(NOW - 48 * hour) == PASS  # usable from 48 h, when a wait is set
    assert waited(NOW - 48 * hour + 1) == FAIL
    found = statuses(pending_payout=True)
    assert [n for n, status in found.items() if status == FAIL] == ["no_pending_payout"]


def test_payouts_on_request_after_the_waiting_period() -> None:
    opens = NEW_YORK_17.day_start(D1 + 14 * DAY + DAY // 2)  # as FTMO: from the first trade
    assert opens == D1 + 14 * DAY  # no clock change in between
    early = eligibility(POLICY, funded(), opens - 1)
    assert {c.name: c.status for c in early.checks}["waiting_period"] == FAIL
    assert not early.eligible and early.opens_at == opens
    on_time = eligibility(POLICY, funded(), opens)
    assert {c.name: c.status for c in on_time.checks}["waiting_period"] == PASS
    assert on_time.eligible  # on request: any day, no pay day to wait for
    detail = {c.name: c.detail for c in on_time.checks}["waiting_period"]
    assert detail == "from 2026-10-18T21:00:00+00:00 (14 days after the first trade)"
    untraded = eligibility(POLICY, funded(trading_day_ids=()), NOW)
    wait = next(c for c in untraded.checks if c.name == "waiting_period")
    assert (wait.status, untraded.opens_at) == (FAIL, None)
    assert wait.detail == "no trade yet; the first payout 14 days after the first trade"
    before = NEW_YORK_17.day_start(FUNDED - 5 * DAY)  # a day before funding does not count
    late = eligibility(POLICY, funded(trading_day_ids=(before, D3)), NOW)
    assert late.opens_at == D3 + 14 * DAY
    # across the November clock change: still the 17:00 New York boundary
    autumn = NEW_YORK_17.day_start(1_792_800_000_000)  # 2026-10-24 (New York, EDT)
    shifted = eligibility(POLICY, funded(trading_day_ids=(autumn,)), NOW)
    assert shifted.opens_at == NEW_YORK_17.day_start(autumn + 14 * DAY + DAY // 2)
    assert shifted.opens_at == autumn + 14 * DAY + 3_600_000  # EST: an hour later in UTC
    last = FUNDED + 20 * DAY
    after = funded(last_payout_at=last)
    soon = eligibility(POLICY, after, last + 14 * DAY - 1)
    assert {c.name: c.status for c in soon.checks}["waiting_period"] == FAIL
    assert soon.opens_at == last + 14 * DAY
    cycle = eligibility(POLICY, after, last + 14 * DAY)
    found = {c.name: (c.status, c.detail) for c in cycle.checks}["waiting_period"]
    assert found[0] == PASS and found[1].endswith("(14 days after the last payout)")
    weekly = replace(POLICY, payouts=replace(POLICY.payouts, interval_days=7))
    assert eligibility(weekly, after, last + 7 * DAY).opens_at == last + 7 * DAY


@pytest.mark.parametrize(
    ("change", "failing"),
    [
        ({"stage": "s2"}, "funded"),
        ({"status": "breached"}, "funded"),
        ({"locked": True}, "funded"),
        ({"open_positions": 1}, "flat"),
        ({"pending_orders": 2}, "flat"),
        ({"tier2_approved": False}, "identity"),
        ({"cif_status": "blocked"}, "identity"),
    ],
)
def test_each_check_can_fail_the_payout(change: dict[str, Any], failing: str) -> None:
    found = statuses(**change)
    assert [name for name, status in found.items() if status == FAIL] == [failing]
    assert not eligibility(POLICY, funded(**change), NOW).eligible


def test_flat_is_not_required_when_the_policy_says_so() -> None:
    relaxed = replace(POLICY, payouts=replace(POLICY.payouts, flat_required=False))
    e = eligibility(relaxed, funded(open_positions=1), NOW)
    assert "flat" not in {c.name for c in e.checks} and e.eligible


def test_minimum_trading_days_count_only_the_period() -> None:
    strict = replace(POLICY, payouts=replace(POLICY.payouts, min_trading_days=4))
    found = {c.name: c.status for c in eligibility(strict, funded(), NOW).checks}
    assert found["trading_days"] == FAIL
    before = NEW_YORK_17.day_start(FUNDED - 5 * DAY)  # an evaluation day, before funding
    e = eligibility(strict, funded(trading_day_ids=(before, D1, D2, D3)), NOW)
    assert e.trading_days == 3 and not e.eligible
    since = eligibility(POLICY, funded(last_payout_at=D3 + 1), NOW)
    assert since.trading_days == 1 and since.period_start == D3 + 1  # D3 only
    mid_day = eligibility(POLICY, funded(last_payout_at=D3 - 1), NOW)  # late on D2
    assert mid_day.trading_days == 2  # the payout's trading day counts in the new period


def test_consistency_reads_the_period_and_the_accounts_own_rule() -> None:
    half = Fraction(1, 2)
    even = funded(consistency=half)
    assert statuses(consistency=half)["consistency"] == PASS
    lopsided = ((D1, 50_000), (D2, 5_000), (D3, 5_000))
    assert statuses(consistency=half, day_pnl=lopsided)["consistency"] == FAIL
    assert statuses(consistency=half, day_pnl=((D1, -5_000),))["consistency"] == FAIL  # no profit
    at_limit = ((D1, 30_000), (D2, 30_000))  # best day exactly 50 %
    assert statuses(consistency=half, day_pnl=at_limit)["consistency"] == PASS
    before = NEW_YORK_17.day_start(FUNDED - 5 * DAY)
    old = ((before, 900_000), *even.day_pnl)  # an evaluation day does not count
    assert statuses(consistency=half, day_pnl=old)["consistency"] == PASS


def test_the_residence_gate_applies_when_switched_on() -> None:
    gated = replace(POLICY, access=AccessPolicy(True, frozenset({"BD", "IN"})))
    bd = eligibility(gated, funded(document_country="BD"), NOW)
    assert {c.name: c.status for c in bd.checks}["residence"] == FAIL
    assert eligibility(POLICY, funded(document_country="BD"), NOW).eligible  # gate off


@pytest.mark.parametrize(
    "change",
    [
        {"basis": "net"},
        {"flat_required": "yes"},
        {"min_trading_days": -1},
        {"maximum_cents": 1_000},  # below the minimum
        {"maximum_cents": 99_899},  # below the largest refundable fee (1-Step 200K)
        {"trading_during_payout": "stopped"},
        {"wallet_cooling_off_hours": "48"},
        {"require_mfa": 1},
        {"step_up_minutes": -1},
    ],
)
def test_payout_settings_are_validated(change: dict[str, Any]) -> None:
    import tomllib

    data = tomllib.loads(DEFAULT_POLICY.read_text())
    data["payouts"].update(change)
    with pytest.raises(PolicyError):
        parse_policy(data)
