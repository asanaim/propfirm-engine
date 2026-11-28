"""Behaviour detectors (D-14.29, D-14.30): the pure rules."""

from fractions import Fraction
from typing import Any

import pytest

from propfirm.customers.detectors import (
    Leg,
    findings,
    gaps,
    hedged,
    hedges,
    legs_from,
    median,
    reentries,
)
from propfirm.customers.payouts import merged_day_pnl
from propfirm.customers.request_rate import Counter
from propfirm.policy import PolicyError, load_policy, parse_policy
from propfirm.rules.clock import NEW_YORK_17
from propfirm.trading.hours import new_york_ms
from tests.test_policy import edited

S = 1_000  # one second in ms
W = 120 * S


def leg(acc: str, side: str, opened: int, closed: int, cif: str = "CIF1", pid: str = "") -> Leg:
    return Leg(acc, cif, pid or f"{acc}-{opened}", "EUR/USD", side, opened, closed)


def test_a_hedge_needs_two_accounts_one_symbol_opposite_sides() -> None:
    a = leg("ACC1", "buy", 0, 600 * S)
    assert hedged(a, leg("ACC2", "sell", 10 * S, 600 * S), W, 50)
    assert not hedged(a, leg("ACC1", "sell", 10 * S, 600 * S), W, 50)  # same account
    assert not hedged(a, leg("ACC2", "buy", 10 * S, 600 * S), W, 50)  # same side
    other = Leg("ACC2", "CIF1", "p", "GBP/USD", "sell", 10 * S, 600 * S)
    assert not hedged(a, other, W, 50)  # another symbol


def test_the_opening_window_is_inclusive() -> None:
    a = leg("ACC1", "buy", 0, 1_000 * S)
    assert hedged(a, leg("ACC2", "sell", W, 1_000 * S), W, 50)
    assert not hedged(a, leg("ACC2", "sell", W + 1, 1_000 * S), W, 50)


def test_overlap_is_half_the_shorter_hold_exactly() -> None:
    # a: 0 to 100 s; b: 60 s to 200 s. Shorter hold 100 s, overlap 40 s
    a = leg("ACC1", "buy", 0, 100 * S)
    assert not hedged(a, leg("ACC2", "sell", 60 * S, 200 * S), W, 50)
    # b: 50 s to 200 s: overlap 50 s, exactly half
    assert hedged(a, leg("ACC2", "sell", 50 * S, 200 * S), W, 50)
    # no overlap at all, even with zero length holds
    assert not hedged(leg("ACC1", "buy", 0, 0), leg("ACC2", "sell", 0, 0), W, 50)


def test_hedges_only_inside_a_group() -> None:
    legs = [
        leg("ACC1", "buy", 0, 600 * S, cif="CIF1"),
        leg("ACC2", "sell", 5 * S, 600 * S, cif="CIF2"),
        leg("ACC3", "sell", 5 * S, 600 * S, cif="CIF3"),
    ]
    groups = {"CIF2": "CIF1"}  # CIF1 and CIF2 in one cluster; CIF3 alone
    found = hedges(legs, lambda c: groups.get(c, c), W, 50)
    assert [(h.a.acc, h.b.acc) for h in found] == [("ACC1", "ACC2")]


def test_three_pairs_on_one_pair_of_accounts_make_a_finding() -> None:
    legs = []
    for i in range(3):
        t = i * 3_600 * S
        legs += [leg("ACC2", "buy", t, t + 600 * S), leg("ACC1", "sell", t + S, t + 600 * S)]
    pairs = hedges(legs, lambda c: c, W, 50)
    assert len(pairs) == 3
    assert all(h.a.acc == "ACC1" for h in pairs)  # ordered by account
    assert findings(pairs[:2], 3) == []
    (f,) = findings(pairs, 3)
    assert (f.a_acc, f.b_acc) == ("ACC1", "ACC2")
    mine, theirs = f.detail("ACC1"), f.detail("ACC2")
    assert mine["key"] == "hedging:ACC2" and theirs["key"] == "hedging:ACC1"
    assert mine["pairs"] == 3 and mine["symbols"] == ["EUR/USD"]
    first = mine["examples"][0]["positions"]
    assert first == ["ACC1-1000", "ACC2-0"]
    assert theirs["examples"][0]["positions"] == ["ACC2-0", "ACC1-1000"]


def test_legs_from_events() -> None:
    def ev(kind: str, acc: str, pid: str, ts: int, **kw: Any) -> dict[str, Any]:
        return {"type": kind, "account_id": acc, "position_id": pid, "ts": ts, **kw}

    events = [
        ev("PositionOpened", "ACC1", "P1", 10, symbol="EUR/USD", side="buy", volume=100),
        ev("PositionClosed", "ACC1", "P1", 20, remaining_volume=50, pnl_cents=300),  # partial
        ev("PositionClosed", "ACC1", "P1", 30, remaining_volume=0, pnl_cents=-100),
        ev("PositionOpened", "ACC2", "P2", 15, symbol="EUR/USD", side="sell", volume=50),
        ev("PositionOpened", "ACC9", "P9", 15, symbol="EUR/USD", side="sell"),  # no owner
        # every other event of an account's history is skipped (it has no
        # position id: reading it broke payout eligibility after D-14.30)
        {"type": "OrderAccepted", "account_id": "ACC1", "ts": 12},
        {"type": "CommissionCharged", "account_id": "ACC1", "ts": 13, "amount_cents": 5},
    ]
    legs = legs_from(events, {"ACC1": "CIF1", "ACC2": "CIF2"}, now=99)
    assert legs == [
        Leg("ACC1", "CIF1", "P1", "EUR/USD", "buy", 10, 30, 100, 200),
        Leg("ACC2", "CIF2", "P2", "EUR/USD", "sell", 15, 99, 50, None),  # still open
    ]


def test_the_policy_file_turns_hedging_on() -> None:
    policy = load_policy().detectors
    assert policy.hedging and policy.hedging_window_seconds == 120
    assert (policy.hedging_overlap_percent, policy.hedging_matches) == (50, 3)

    def change(data: dict[str, Any]) -> None:
        data["detectors"]["hedging_overlap_percent"] = 101

    with pytest.raises(PolicyError, match="from 1 to 100"):
        parse_policy(edited(change))


# --- gap trading and consistency gaming (D-14.30) -----------------------------------

H = 3_600 * S
FRI_CLOSE = new_york_ms("2026-11-27 17:00")  # forex closes Fri 17:00 New York
SUN_OPEN = new_york_ms("2026-11-29 17:00")
FOREX = {i.symbol.name: i.hours for i in load_policy().trading.instruments}


def closed_leg(
    acc: str, opened: int, closed: int, result: int, volume: int = 100, pid: str = ""
) -> Leg:
    return Leg(
        acc, "CIF1", pid or f"{acc}-{opened}", "EUR/USD", "buy", opened, closed, volume, result
    )


def test_gap_held_through_the_weekend() -> None:
    hours = FOREX.get
    assert FOREX["EUR/USD"] is not None
    held = closed_leg("ACC1", FRI_CLOSE - 2 * H, SUN_OPEN + H, 5_000)
    early = closed_leg("ACC1", FRI_CLOSE - 2 * H - 1, SUN_OPEN + H, 5_000)
    flat = closed_leg("ACC1", FRI_CLOSE - H, FRI_CLOSE - 1, 5_000)  # closed before
    loss = closed_leg("ACC1", FRI_CLOSE - H, SUN_OPEN + H, -5_000)
    found = gaps([held, early, flat, loss], hours, [], 2 * H, 5 * 60 * S, Fraction(2))
    assert [(g.leg, g.reason, g.at) for g in found] == [(held, "break", FRI_CLOSE)]


def test_gap_at_a_release_needs_an_outsized_volume() -> None:
    release = FRI_CLOSE - 10 * H
    events = [(release, frozenset({"EUR/USD"})), (release - H, frozenset({"XAU/USD"}))]
    normal = [closed_leg("ACC1", release - 5 * H + i, release - 4 * H, 100) for i in range(3)]
    big = closed_leg("ACC1", release - 5 * 60 * S, release + H, 9_000, volume=201)
    at_two = closed_leg("ACC1", release + 5 * 60 * S, release + H, 9_000, volume=200)
    late = closed_leg("ACC1", release + 5 * 60 * S + 1, release + H, 9_000, volume=900)
    found = gaps(
        [*normal, big, at_two, late], lambda _: None, events, 2 * H, 5 * 60 * S, Fraction(2)
    )
    # median volume 150.5: only 900 is above twice it, and it opened too late
    assert found == []
    found = gaps([*normal, big], lambda _: None, events, 2 * H, 5 * 60 * S, Fraction(2))
    assert [(g.leg, g.reason, g.median_volume) for g in found] == [(big, "release", Fraction(100))]


def test_median_is_exact() -> None:
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == Fraction(5, 2)
    with pytest.raises(ValueError):
        median([])


def test_a_reentry_crosses_the_day_boundary_within_the_window() -> None:
    boundary = NEW_YORK_17.next_boundary(FRI_CLOSE - 10 * H)
    first = closed_leg("ACC1", boundary - H, boundary - 30 * S, 1_000, pid="P1")
    second = closed_leg("ACC1", boundary + 30 * S, boundary + H, 1_000, pid="P2")
    slow = closed_leg("ACC1", boundary + 31 * S, boundary + H, 1_000, pid="P3")
    (r,) = reentries([first, second], NEW_YORK_17.next_boundary, 60 * S)
    assert (r.first, r.second, r.boundary) == (first, second, boundary)
    assert r.days(NEW_YORK_17.day_start) == (
        NEW_YORK_17.day_start(boundary - 1),
        NEW_YORK_17.day_start(boundary),
    )
    assert reentries([first, slow], NEW_YORK_17.next_boundary, 60 * S) == []
    other_side = Leg("ACC1", "CIF1", "P4", "EUR/USD", "sell", boundary + S, boundary + H, 100, 1)
    assert reentries([first, other_side], NEW_YORK_17.next_boundary, 60 * S) == []
    same_day = closed_leg("ACC1", boundary - 20 * S, boundary - 10 * S, 1, pid="P5")
    assert reentries([first, same_day], NEW_YORK_17.next_boundary, 60 * S) == []


def test_merged_days_add_up_under_the_earliest() -> None:
    days = [(1, 100), (2, 300), (3, 50), (4, -20)]
    assert merged_day_pnl(days, []) == days
    assert merged_day_pnl(days, [(2, 1), (3, 2)]) == [(1, 450), (4, -20)]


def test_the_policy_file_turns_gap_and_reentry_on() -> None:
    policy = load_policy().detectors
    assert policy.gap and policy.reentry
    assert (policy.gap_before_break_minutes, policy.gap_release_minutes) == (120, 5)
    assert policy.gap_size_multiple == 2 and policy.reentry_seconds == 60

    def change(data: dict[str, Any]) -> None:
        data["detectors"]["gap_size_multiple"] = "0.5"

    with pytest.raises(PolicyError, match="gap_size_multiple at least 1"):
        parse_policy(edited(change))


# --- request rate (D-14.31) ------------------------------------------------------------


def test_the_counter_resets_at_the_trading_day_boundary() -> None:
    counter = Counter()
    boundary = NEW_YORK_17.next_boundary(FRI_CLOSE - 10 * H)
    assert counter.add("ACC1", boundary - 1) == (NEW_YORK_17.day_start(boundary - 1), 1)
    assert counter.add("ACC1", boundary - 1)[1] == 2
    assert counter.add("ACC2", boundary - 1)[1] == 1  # per account
    assert counter.add("ACC1", boundary) == (boundary, 1)  # a new trading day


def test_the_policy_file_turns_the_request_rate_on() -> None:
    policy = load_policy().detectors
    assert policy.rate and policy.rate_orders_per_day == 2_000
    assert (policy.rate_repeats, policy.rate_repeat_days) == (2, 30)
