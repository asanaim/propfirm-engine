"""Daily trader classes (D-14.28): the pure rules."""

from fractions import Fraction
from typing import Any

import pytest

from propfirm.analysis import Trade
from propfirm.customers.classes import (
    DAY_MS,
    PREFIX,
    Facts,
    classify,
    counts,
    decide,
    newest_fact,
    reason_text,
    t_above,
)
from propfirm.policy import ClassesPolicy, PolicyError, load_policy, parse_policy
from tests.test_policy import edited

T0 = 1_767_225_600_000  # 2026-01-01 00:00 UTC
POLICY = ClassesPolicy(on=True)


def trade(n: int, result: int, hold_s: int = 3600, day: int | None = None) -> Trade:
    opened = T0 + (n if day is None else day) * DAY_MS
    return Trade("ACC1", str(n), "EUR/USD", opened, opened + hold_s * 1000, 1, result)


def proven_trades() -> tuple[Trade, ...]:
    # 200 trades over 100 days: two a day, +300 and -100, every day +200 net
    out = []
    for d in range(100):
        out.append(trade(2 * d, 300, day=d))
        out.append(trade(2 * d + 1, -100, day=d))
    return tuple(out)


def facts(trades: tuple[Trade, ...] = (), **kw: object) -> Facts:
    base: dict[str, object] = {
        "paid_payouts": 0,
        "chargeback_at": None,
        "trades_cluster_at": None,
        "actioned_case_at": None,
    }
    base.update(kw)
    return Facts(trades, **base)  # type: ignore[arg-type]


def test_t_above_exact() -> None:
    assert not t_above([5], Fraction(2))  # one day: no estimate
    assert not t_above([-1, -2, -3], Fraction(2))  # mean below zero
    assert t_above([3, 3, 3], Fraction(2))  # no variance, positive mean
    # mean 2, sample variance 2, n 2: t = 2 * sqrt(2) / sqrt(2) = 2 exactly
    assert t_above([1, 3], Fraction(199, 100))
    assert not t_above([1, 3], Fraction(2))  # t == 2 is not above 2


def test_b_by_default() -> None:
    cls, reasons, m = classify(facts(), POLICY, T0)
    assert (cls, reasons) == ("B", [])
    assert m["trades"] == 0


def test_a_when_proven() -> None:
    now = T0 + 200 * DAY_MS
    cls, reasons, m = classify(facts(proven_trades(), paid_payouts=3), POLICY, now)
    assert (cls, reasons) == ("A", ["proven"])
    assert m["trading_days"] == 100 and m["trades"] == 200
    # one payout short of proven
    assert classify(facts(proven_trades(), paid_payouts=2), POLICY, now)[0] == "B"
    # a case actioned long ago still keeps A away
    old = facts(proven_trades(), paid_payouts=3, actioned_case_at=T0)
    assert classify(old, POLICY, now)[0] == "B"


def test_profit_factor_bound_is_strict() -> None:
    # gross profit 130 per day against loss 100: factor exactly 1.3, not above
    trades = []
    for d in range(100):
        trades.append(trade(2 * d, 260, day=d))
        trades.append(trade(2 * d + 1, -200, day=d))
    now = T0 + 200 * DAY_MS
    assert classify(facts(tuple(trades), paid_payouts=3), POLICY, now)[0] == "B"


def test_c_reasons() -> None:
    cls, reasons, _ = classify(facts(chargeback_at=T0, trades_cluster_at=T0), POLICY, T0)
    assert (cls, reasons) == ("C", ["chargeback", "trades_cluster"])
    # most profit from holds under 60 seconds
    short = (trade(1, 1000, hold_s=10), trade(2, 100, hold_s=600))
    assert classify(facts(short), POLICY, T0)[:2] == ("C", ["short_holds"])


def test_d_wins_and_lapses() -> None:
    f = facts(chargeback_at=T0, actioned_case_at=T0)
    assert classify(f, POLICY, T0 + 90 * DAY_MS)[:2] == ("D", ["case_actioned"])
    assert classify(f, POLICY, T0 + 90 * DAY_MS + 1)[:2] == ("C", ["chargeback"])


def test_reason_text_round_trip() -> None:
    text = reason_text(["chargeback"], {"trades": 3})
    assert text == 'daily: chargeback; {"trades": 3}'
    assert reason_text([], {}).startswith(PREFIX + "standard;")


def test_decide_unclassed() -> None:
    f = facts()
    assert not decide(None, "B", [], f)  # B is the default: nothing to record
    assert decide(None, "C", ["chargeback"], f)


def test_decide_own_records() -> None:
    f = facts()
    last = ("C", reason_text(["chargeback"], {"trades": 1}), T0)
    assert not decide(last, "C", ["chargeback"], f)  # measures alone changed
    assert decide(last, "C", ["chargeback", "trades_cluster"], f)
    assert decide(last, "B", [], f)  # the job lifts what it set itself


def test_decide_respects_a_person() -> None:
    staff = ("B", "reviewed: fine", T0)
    # an older chargeback was already weighed by the person
    assert not decide(staff, "C", ["chargeback"], facts(chargeback_at=T0 - 1))
    # a newer one is not
    assert decide(staff, "C", ["chargeback"], facts(chargeback_at=T0 + 1))
    # short holds have no date: the person's B stands
    assert not decide(staff, "C", ["short_holds"], facts())
    # never lifted by the job
    assert not decide(("C", "short holds: 60.00% of profit", T0), "A", ["proven"], facts())
    # D on a newer actioned case
    assert decide(("C", "x", T0), "D", ["case_actioned"], facts(actioned_case_at=T0 + 5))


def test_newest_fact() -> None:
    f = facts(chargeback_at=5, trades_cluster_at=9, actioned_case_at=7)
    assert newest_fact(f, ["chargeback", "short_holds"]) == 5
    assert newest_fact(f, ["chargeback", "trades_cluster"]) == 9
    assert newest_fact(f, ["short_holds"]) is None


def test_counts() -> None:
    rows = [{"class": "A"}, {"class": "C"}, {"class": "C"}]
    assert counts(rows) == {"A": 1, "B": 0, "C": 2, "D": 0}


def test_the_policy_file_turns_classes_on() -> None:
    policy = load_policy().classes
    assert policy.on and policy.c_short_percent == 50
    assert policy.a_min_profit_factor == Fraction(13, 10)

    def with_classes(**values: object) -> Any:
        def change(data: dict[str, Any]) -> None:
            data["classes"].update(values)

        return edited(change)

    for values, match in (
        ({"a_max_short_percent": 50}, "a_max_short_percent < c_short_percent"),
        ({"c_short_percent": 101}, "c_short_percent <= 100"),
        ({"a_min_profit_factor": "1"}, "a_min_profit_factor above 1"),
        ({"a_min_t_stat": "0"}, "a_min_t_stat above 0"),
    ):
        with pytest.raises(PolicyError, match=match):
            parse_policy(with_classes(**values))
