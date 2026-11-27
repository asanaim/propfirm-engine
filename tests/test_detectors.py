"""Behaviour detectors (D-14.29): hedging across accounts, the pure rules."""

from typing import Any

import pytest

from propfirm.customers.detectors import Leg, findings, hedged, hedges, legs_from
from propfirm.policy import PolicyError, load_policy, parse_policy
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
        ev("PositionOpened", "ACC1", "P1", 10, symbol="EUR/USD", side="buy"),
        ev("PositionClosed", "ACC1", "P1", 20, remaining_volume=50),  # partial
        ev("PositionClosed", "ACC1", "P1", 30, remaining_volume=0),
        ev("PositionOpened", "ACC2", "P2", 15, symbol="EUR/USD", side="sell"),
        ev("PositionOpened", "ACC9", "P9", 15, symbol="EUR/USD", side="sell"),  # no owner
    ]
    legs = legs_from(events, {"ACC1": "CIF1", "ACC2": "CIF2"}, now=99)
    assert legs == [
        Leg("ACC1", "CIF1", "P1", "EUR/USD", "buy", 10, 30),
        Leg("ACC2", "CIF2", "P2", "EUR/USD", "sell", 15, 99),  # still open
    ]


def test_the_policy_file_turns_hedging_on() -> None:
    policy = load_policy().detectors
    assert policy.hedging and policy.hedging_window_seconds == 120
    assert (policy.hedging_overlap_percent, policy.hedging_matches) == (50, 3)

    def change(data: dict[str, Any]) -> None:
        data["detectors"]["hedging_overlap_percent"] = 101

    with pytest.raises(PolicyError, match="from 1 to 100"):
        parse_policy(edited(change))
