"""The profit split ladder (build plan step 2, line 3; section 1, 13 Nov 2026):
80 % at funding; 85 % from the payout after 3 paid payouts with no confirmed
case; 90 % from the first scaling step; Instant fixed at 80 %."""

from dataclasses import replace
from fractions import Fraction

import pytest

from propfirm.customers.onboarding import split_ladder_words
from propfirm.customers.payouts import FundedAccount, split_for
from propfirm.policy import PolicyError, load_policy, parse_policy
from tests.test_policy import edited

POLICY = load_policy()


def account(product_id: str, paid: int = 0, cases: int = 0, steps: int = 0) -> FundedAccount:
    return FundedAccount(
        acc="ACC00000001",
        product=POLICY.product(product_id),
        stage="funded",
        consistency=None,
        funded_at=0,
        baseline_cents=10_000_000,
        fee_paid_cents=0,
        payouts_paid=paid,
        last_payout_at=None,
        status="active",
        locked=False,
        balance_cents=10_000_000,
        equity_cents=10_000_000,
        open_positions=0,
        pending_orders=0,
        trading_day_ids=(),
        day_pnl=(),
        cif_status="active",
        tier2_approved=True,
        residence_country="DE",
        document_country="DE",
        confirmed_cases=cases,
        scaling_steps=steps,
    )


@pytest.mark.parametrize("product", ["lite", "one-step", "classic"])
def test_the_ladder_climbs_with_paid_payouts_then_scaling(product: str) -> None:
    assert split_for(POLICY, account(product, paid=0))[0] == Fraction(80, 100)
    assert split_for(POLICY, account(product, paid=2))[0] == Fraction(80, 100)
    assert split_for(POLICY, account(product, paid=3)) == (
        Fraction(85, 100),
        "after 3 paid payouts",
    )
    assert split_for(POLICY, account(product, paid=1, steps=1)) == (
        Fraction(90, 100),
        "from the scaling step",
    )


def test_a_confirmed_case_stops_the_payout_rung_but_not_scaling() -> None:
    assert split_for(POLICY, account("lite", paid=5, cases=1))[0] == Fraction(80, 100)
    assert split_for(POLICY, account("lite", paid=5, cases=1, steps=1))[0] == Fraction(90, 100)


def test_instant_stays_at_its_split() -> None:
    assert split_for(POLICY, account("instant", paid=9, steps=3)) == (
        Fraction(80, 100),
        "the product's split",
    )
    assert split_ladder_words(POLICY.product("instant"), POLICY.scaling) == ""


def test_the_contract_and_the_policy_agree() -> None:
    words = split_ladder_words(POLICY.product("lite"), POLICY.scaling)
    assert words.startswith("; 85 % from the payout after 3 paid payouts")
    assert words.endswith("and 90 % from the first scaling step")
    assert POLICY.scaling.split_after_step == 1


def test_a_rung_needs_both_keys_and_must_raise_the_split() -> None:
    def only_count(d: dict) -> None:  # type: ignore[type-arg]
        d["products"][0].pop("split_percent_after_payouts")

    def lower(d: dict) -> None:  # type: ignore[type-arg]
        d["products"][0]["split_percent_after_payouts"] = "75"

    for change in (only_count, lower):
        with pytest.raises(PolicyError):
            parse_policy(edited(change))
    assert replace(POLICY.product("lite"), split_after_payouts=0).split_after_payouts == 0
