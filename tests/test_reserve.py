"""Payout reserve (D-13.7, D-14.20): sweep, draw, coverage K and its
states. Database paths are in tests/integration/test_reserve.py."""

from fractions import Fraction
from typing import Any

import pytest

from propfirm.customers.books import CASH, RESERVE, draw_entry, sweep_cents, sweep_entry
from propfirm.customers.liability import Desk, open_profit_cents
from propfirm.customers.reserve import (
    AMBER,
    BREACH,
    NORMAL,
    RED,
    ceil_cents,
    coverage,
    k_text,
    state_of,
)
from propfirm.policy import PolicyError, ReservePolicy, load_policy, parse_policy
from tests.test_policy import edited

POLICY = ReservePolicy(
    on=True,
    sweep={"lite": Fraction(72, 100), "one-step": Fraction(66, 100), "instant": Fraction(27, 100)},
)


def desk(
    certain: int = 0, opened: int = 0, expected: int = 0, floating: int = 0, largest: int = 0
) -> Desk:
    return Desk(
        as_of=0,
        certain_cents=certain,
        payouts_certain_cents=certain,
        commissions_cents=0,
        open_cents=opened,
        expected_cents=expected,
        funded_accounts=0,
        largest=None,
        claims=(),
        unread=(),
        open_profit_cents=floating,
        largest_customer_cents=largest,
    )


def test_the_policy_file_sets_the_base_model_shares() -> None:
    reserve = load_policy().reserve
    assert reserve.on
    assert reserve.sweep == {
        "lite": Fraction(72, 100),
        "one-step": Fraction(66, 100),
        "instant": Fraction(27, 100),
    }
    assert (reserve.amber_below, reserve.red_below, reserve.breach_below) == (
        Fraction(13, 10),
        Fraction(11, 10),
        Fraction(1),
    )


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("sweep_percent", {"lite": "72", "one-step": "66"}, "no share for products on sale"),
        (
            "sweep_percent",
            {"lite": "72", "one-step": "66", "instant": "27", "gold": "5"},
            "unknown",
        ),
        ("sweep_percent", {"lite": "0", "one-step": "66", "instant": "27"}, "between 0"),
        ("sweep_percent", {"lite": "101", "one-step": "66", "instant": "27"}, "between 0"),
        ("sweep_factor", "0.9", "at least 1"),
        ("red_below", "1.4", "amber_below >= red_below"),
        ("breach_below", "0", "breach_below > 0"),
        ("release_above", "1.2", "at least amber_below"),
        ("extra", "1", "unknown keys"),
    ],
)
def test_bad_reserve_settings_are_refused(key: str, value: object, match: str) -> None:
    def change(data: dict[str, Any]) -> None:
        data["reserve"][key] = value

    with pytest.raises(PolicyError, match=match):
        parse_policy(edited(change))


def test_without_a_reserve_table_the_reserve_is_off() -> None:
    def change(data: dict[str, Any]) -> None:
        del data["reserve"]

    assert not parse_policy(edited(change)).reserve.on


def test_the_sweep_rounds_up_to_the_cent() -> None:
    assert sweep_cents(5500, Fraction(72, 100)) == 3960
    assert sweep_cents(10001, Fraction(27, 100)) == 2701  # 2700.27 -> 2701
    assert sweep_cents(1, Fraction(27, 100)) == 1
    assert sweep_cents(0, Fraction(27, 100)) == 0


def test_a_sweep_moves_cash_to_the_reserve_once_per_order() -> None:
    e = sweep_entry({"ord": "ORD1", "price_cents": 5500, "paid_at": 7}, Fraction(72, 100))
    assert e is not None
    assert e.entry_id == "reserve-sweep:ORD1"
    assert [(p.account, p.amount) for p in e.postings] == [(RESERVE, 3960), (CASH, -3960)]
    assert sweep_entry({"ord": "T", "price_cents": 0, "paid_at": 7}, Fraction(1, 2)) is None


def test_a_paid_payout_is_drawn_from_the_reserve() -> None:
    e = draw_entry({"po": "PO1", "trader_amount_cents": 80000, "paid_at": 9})
    assert e.entry_id == "reserve-draw:PO1"
    assert [(p.account, p.amount) for p in e.postings] == [(CASH, 80000), (RESERVE, -80000)]


def test_open_profit_counts_only_what_the_closed_balance_has_not_earned() -> None:
    # baseline 100,000; closed 102,000; equity 105,000; 80 %: 3,000 x 0.8
    assert open_profit_cents(10_500_000, 10_200_000, 10_000_000, 0, "80") == 240_000
    # under water on the balance: only equity above the baseline counts
    assert open_profit_cents(10_100_000, 9_900_000, 10_000_000, 0, "80") == 80_000
    # an open loss is not a claim
    assert open_profit_cents(10_100_000, 10_200_000, 10_000_000, 0, "80") == 0
    # removed profit (V) moves the line
    assert open_profit_cents(10_500_000, 10_200_000, 10_000_000, 400_000, "80") == 80_000
    # rounded up: 1 cent at 92.5 % is 0.925 -> 1
    assert open_profit_cents(10_000_001, 10_000_000, 10_000_000, 0, "92.5") == 1


@pytest.mark.parametrize(
    ("k", "state"),
    [
        (None, NORMAL),
        (Fraction(13, 10), NORMAL),
        (Fraction(129999, 100000), AMBER),
        (Fraction(11, 10), AMBER),
        (Fraction(109, 100), RED),
        (Fraction(1), RED),
        (Fraction(99, 100), BREACH),
        (Fraction(0), BREACH),
        (Fraction(-1, 2), BREACH),
    ],
)
def test_the_four_states_at_their_edges(k: Fraction | None, state: str) -> None:
    assert state_of(k, POLICY) == state


def test_k_is_shown_rounded_down() -> None:
    assert k_text(Fraction(129999, 100000)) == "1.29"
    assert k_text(Fraction(13, 10)) == "1.30"
    assert k_text(Fraction(1, 3)) == "0.33"
    assert k_text(None) is None
    assert ceil_cents(Fraction(1, 3)) == 1


def test_r_star_weighs_each_tier() -> None:
    # certain 1,000 + open 200 + expected 300; open profit 400 x 0.5;
    # evaluations 600 x 1.5; largest customer 500
    c = coverage(
        520_000,
        desk(100_000, 20_000, 30_000, 40_000, 50_000),
        60_000,
        3,
        POLICY,
    )
    assert c.certain_cents == 150_000
    assert c.required_cents == 150_000 + 20_000 + 90_000 + 50_000
    assert c.k == Fraction(520_000, 310_000)
    assert c.state == NORMAL
    assert c.releasable_cents == 520_000 - 403_000  # above 1.3 x R*


def test_r_star_rounds_up_and_a_short_reserve_is_red_then_breach() -> None:
    c = coverage(100, desk(floating=1), 0, 0, POLICY)  # 0.5 cent -> 1
    assert c.required_cents == 1
    assert coverage(105, desk(100), 0, 0, POLICY).state == RED
    assert coverage(99, desk(100), 0, 0, POLICY).state == BREACH
    assert coverage(-10, desk(100), 0, 0, POLICY).state == BREACH
    assert coverage(0, desk(), 0, 0, POLICY).state == NORMAL  # nothing owed
    assert coverage(0, desk(), 0, 0, POLICY).releasable_cents == 0


def test_each_state_adds_its_limits_to_the_ones_before() -> None:
    from propfirm.customers.reserve import Limits, limits

    assert limits(NORMAL) == Limits(False, False, "open", False, False)
    assert limits(AMBER) == Limits(True, True, "capped", False, False)
    assert limits(RED) == Limits(True, True, "stopped", True, False)
    assert limits(BREACH) == Limits(True, True, "stopped", True, True)


def test_the_instant_cap_is_the_trailing_daily_average_rounded_up() -> None:
    from propfirm.customers.reserve import instant_cap

    assert instant_cap(0) == 1  # capped, never stopped, in amber
    assert instant_cap(30) == 1
    assert instant_cap(31) == 2
    assert instant_cap(300) == 10


def test_top_ups_come_from_the_owners_and_releases_go_to_cash() -> None:
    from propfirm.customers.books import OWNER_EQUITY, reserve_move_entry

    row = {"rsm": "RSM1", "kind": "topup", "amount_cents": 500, "decided_at": 3}
    up = reserve_move_entry(row)
    assert (up.entry_id, up.kind) == ("reserve-topup:RSM1", "reserve_topup")
    assert [(p.account, p.amount) for p in up.postings] == [(RESERVE, 500), (OWNER_EQUITY, -500)]
    down = reserve_move_entry({**row, "kind": "release"})
    assert (down.entry_id, down.kind) == ("reserve-release:RSM1", "reserve_release")
    assert [(p.account, p.amount) for p in down.postings] == [(CASH, 500), (RESERVE, -500)]
