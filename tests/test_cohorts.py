"""The cohort alarm (D-14.33): the exact binomial test and the tallies."""

from fractions import Fraction
from math import comb
from typing import Any

import pytest

from propfirm.customers.cohorts import (
    Result,
    Tally,
    alarmed,
    binomial_tail_at_most,
    last_evaluation_stage,
    month_of,
    outcome,
    percent,
    tallies,
)
from propfirm.policy import PolicyError, load_policy, parse_policy
from tests.test_policy import edited

NOV = 1_793_491_200_000  # 2026-11-01 00:00 UTC
OCT = NOV - 86_400_000  # 2026-10-31


def tail(n: int, k: int, p: Fraction) -> Fraction:
    return sum((comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1)), Fraction(0))


def test_the_tail_matches_a_direct_sum() -> None:
    for p in (Fraction(1, 4), Fraction(3, 8), Fraction(46, 125), Fraction(547, 2000)):
        for n in (1, 2, 7, 20, 41):
            for k in range(n + 2):
                exact = tail(n, k, p)
                for alpha in (Fraction(1, 100), Fraction(1, 20), exact):
                    assert binomial_tail_at_most(n, k, p, alpha) == (exact <= alpha), (n, k, p)


def test_known_values() -> None:
    half, one_percent = Fraction(1, 2), Fraction(1, 100)
    assert not binomial_tail_at_most(10, 9, half, one_percent)  # 11/1024 = 1.07 %
    assert binomial_tail_at_most(10, 10, half, one_percent)  # 1/1024
    assert binomial_tail_at_most(5, 6, half, one_percent)  # impossible
    assert not binomial_tail_at_most(5, 0, half, one_percent)  # certain
    with pytest.raises(ValueError):
        binomial_tail_at_most(5, 1, Fraction(1), one_percent)


def lite(n: int, k: int) -> Tally:
    return Tally("product", "lite:2026-11", "lite", n, k, Fraction(1, 4))


def test_alarmed_needs_results_a_rate_above_and_significance() -> None:
    alpha = Fraction(1, 100)
    assert not alarmed(lite(9, 9), alpha, 10)  # too few results
    assert alarmed(lite(10, 7), alpha, 10)  # 70 % against 25 %: P = 0.35 %
    assert not alarmed(lite(10, 5), alpha, 10)  # 50 %: P = 7.8 %
    assert not alarmed(lite(40, 10), alpha, 10)  # at the bound
    assert lite(0, 0).rate == 0


def test_tallies_by_product_month_and_affiliate() -> None:
    bounds = {"lite": Fraction(1, 4)}
    results = [
        Result("ORD1", "lite", NOV, "CIF9", True),
        Result("ORD2", "lite", NOV + 1, None, False),
        Result("ORD3", "lite", OCT, "CIF9", True),
        Result("ORD4", "instant", NOV, "CIF9", True),  # no bound: not watched
    ]
    found = {(t.kind, t.key): (t.resolved, t.passed) for t in tallies(results, bounds)}
    assert found == {
        ("affiliate", "CIF9:lite"): (2, 2),
        ("product", "lite:2026-10"): (1, 1),
        ("product", "lite:2026-11"): (2, 1),
    }


def test_month_and_percent() -> None:
    assert (month_of(NOV), month_of(OCT)) == ("2026-11", "2026-10")
    assert percent(Fraction(547, 2000)) == "27.3 %"
    assert percent(Fraction(7, 10)) == "70.0 %"


def test_the_policy_file_turns_the_alarm_on() -> None:
    policy = load_policy().cohorts
    assert policy.on and policy.significance == Fraction(1, 100)
    assert policy.bounds == {
        "classic": Fraction(547, 2000),
        "instant": Fraction(46, 125),
        "lite": Fraction(1, 4),
        "one-step": Fraction(3, 8),
    }
    assert policy.instant_products == ("instant",)

    def change(data: dict[str, Any]) -> None:
        data["cohorts"]["bounds"]["lite"] = "1"

    with pytest.raises(PolicyError, match="bound of lite between 0 and 1"):
        parse_policy(edited(change))


def acct(stage: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"stage": stage, "passed_at": None, "failed_at": None}
    return {**base, "voided": False, "locked_at": None, **kw}


def test_last_evaluation_stage_per_product() -> None:
    stages = {p.id: last_evaluation_stage(p) for p in load_policy().products}
    assert stages == {"lite": "s2", "classic": "s2", "one-step": "s1", "instant": None}


def test_an_order_outcome() -> None:
    assert outcome([acct("s1", passed_at=1), acct("s2", passed_at=2)], False, "s2") is True
    assert outcome([acct("s1", passed_at=1), acct("s2")], False, "s2") is None  # running
    assert outcome([acct("s1", passed_at=1), acct("s2", failed_at=3)], False, "s2") is False
    assert outcome([acct("s1", failed_at=1)], False, "s2") is False
    # a funded breach after the pass does not undo it
    passed = [acct("s1", passed_at=1, failed_at=None), acct("funded", failed_at=9)]
    assert outcome(passed, False, "s1") is True
    assert outcome([acct("s1", passed_at=1, voided=True)], False, "s1") is None
    # Instant: the floor locked first, or a breach first
    assert outcome([acct("funded", locked_at=5, failed_at=9)], True, None) is True
    assert outcome([acct("funded", locked_at=9, failed_at=5)], True, None) is False
    assert outcome([acct("funded")], True, None) is None
