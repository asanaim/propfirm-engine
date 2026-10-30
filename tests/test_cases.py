"""Cases without a database: the history fold, actions, the void input in
the engine, and what cases do to payout eligibility."""

import re
from dataclasses import replace
from typing import Any

import pytest

from propfirm.customers.cases import (
    CASE_KINDS,
    CASE_STATUSES,
    CaseError,
    CaseEvent,
    CaseState,
    case_state,
    confirmed_actions,
    validate_actions,
)
from propfirm.customers.payouts import FAIL, PASS, eligibility
from propfirm.customers.store import _MIGRATIONS
from propfirm.journal.inputs import (
    InputRejected,
    InputRejectReason,
    OpenAccount,
    PlaceOrder,
    VoidAccount,
)
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.types import PriceUpdate
from propfirm.policy import DEFAULT_POLICY, PolicyError, load_policy, parse_policy
from propfirm.rules.events import AccountVoided
from propfirm.service.setup import setup_from_policy
from propfirm.trading.events import CloseReason, OrderRejected, PositionClosed, RejectReason
from propfirm.trading.orders import OrderRequest, OrderType, Side
from tests.test_payouts import NOW, POLICY, funded

CAS = "CAS00000001"
type Step = tuple[str, dict[str, Any]]


def fold(*steps: Step) -> CaseState:
    return case_state(CAS, [CaseEvent(CAS, k, n, d) for n, (k, d) in enumerate(steps, start=1)])


OPENED: Step = ("opened", {"cif": "CIF00000001", "kind": "fraud", "severity": "low", "due_at": 900})
ALERT: Step = ("alert", {"alr": "ALR00000001", "severity": "low", "due_at": 900})
VOID = {"action": "void_account", "acc": "ACC00000003"}
REMOVE = {"action": "remove_profit", "acc": "ACC00000003", "amount_cents": 12_000}
BLOCK = {"action": "block_customer"}
PROPOSED: Step = ("proposed", {"by": "s1", "actions": [REMOVE, VOID, BLOCK]})


def applied(index: int, action: dict[str, Any]) -> Step:
    return ("applied", {"index": index, "action": action, "result": {}})


# --- the history ----------------------------------------------------------------------


def test_cleared_by_one_employee() -> None:
    state = fold(OPENED, ALERT, ("assigned", {"to": "s1"}), ("note", {"text": "checked"}))
    assert (state.status, state.assignee, state.severity, state.due_at) == (
        "open",
        "s1",
        "low",
        900,
    )
    closed = fold(OPENED, ALERT, ("closed", {"by": "s1", "outcome": "cleared"}))
    assert (closed.status, closed.outcome, closed.closed_at) == ("closed", "cleared", 3)
    assert fold(OPENED, ALERT, ("closed", {"by": "s1", "outcome": "warning"})).outcome == "warning"


def test_actions_need_a_second_employee_then_are_carried_out() -> None:
    pending = fold(OPENED, ALERT, PROPOSED)
    assert (pending.status, pending.proposed_by, pending.outcome) == (
        "decision_pending",
        "s1",
        "actioned",
    )
    with pytest.raises(CaseError):
        fold(OPENED, ALERT, PROPOSED, ("confirmed", {"by": "s1"}))  # not the proposer
    carrying = fold(OPENED, ALERT, PROPOSED, ("confirmed", {"by": "s2"}))
    assert (carrying.status, carrying.pending_actions) == ("actioning", (0, 1, 2))
    done = (
        OPENED,
        ALERT,
        PROPOSED,
        ("confirmed", {"by": "s2"}),
        applied(1, VOID),
        applied(0, REMOVE),
    )
    assert fold(*done).pending_actions == (2,)
    with pytest.raises(CaseError):
        fold(*done, ("closed", {"by": "platform", "outcome": "actioned"}))  # one left
    closed = fold(*done, applied(2, BLOCK), ("closed", {"by": "platform", "outcome": "actioned"}))
    assert (closed.status, closed.outcome) == ("closed", "actioned")
    with pytest.raises(CaseError):
        fold(*done, applied(1, VOID))  # carried out twice
    with pytest.raises(CaseError):
        fold(*done, applied(2, REMOVE))  # another action than the one at that index


def test_a_rejection_or_new_evidence_sends_it_back() -> None:
    rejected = fold(OPENED, ALERT, PROPOSED, ("rejected", {"by": "s2"}))
    assert (rejected.status, rejected.proposed_by, rejected.outcome) == ("open", None, None)
    with pytest.raises(CaseError):
        fold(OPENED, ALERT, PROPOSED, ("rejected", {"by": "s1"}))
    high: Step = ("alert", {"alr": "ALR00000002", "severity": "high", "due_at": 400})
    reopened = fold(OPENED, ALERT, PROPOSED, high)
    assert (reopened.status, reopened.severity, reopened.due_at) == ("open", "high", 400)
    later_low: Step = ("alert", {"alr": "ALR00000003", "severity": "low", "due_at": 5_000})
    assert fold(OPENED, high, later_low).severity == "high"  # severity never drops
    assert fold(OPENED, high, later_low).due_at == 400  # nor does urgency


def test_a_closed_case_can_be_reopened() -> None:
    closed = (OPENED, ALERT, ("closed", {"by": "s1", "outcome": "cleared"}))
    again = fold(*closed, ("reopened", {"by": "s2"}))
    assert (again.status, again.outcome, again.closed_at) == ("open", None, None)


@pytest.mark.parametrize(
    "steps",
    [
        (),
        (ALERT,),
        (OPENED, OPENED),
        (OPENED, ("closed", {"by": "s1", "outcome": "actioned"})),  # actions need four eyes
        (OPENED, ("confirmed", {"by": "s2"})),
        (OPENED, ("reopened", {"by": "s2"})),
        (OPENED, ("proposed", {"by": "s1", "actions": []})),
        (OPENED, PROPOSED, PROPOSED),
        (OPENED, ("closed", {"by": "s1", "outcome": "cleared"}), ALERT),  # alerts on a closed case
        (OPENED, ("alert", {"alr": "A", "severity": "urgent", "due_at": 1})),
        (OPENED, ("escalated", {})),
    ],
)
def test_histories_no_valid_case_has_are_refused(steps: tuple[Step, ...]) -> None:
    with pytest.raises(CaseError):
        fold(*steps)


@pytest.mark.parametrize(
    "actions",
    [
        [],
        [{"action": "freeze"}],
        [{"action": "void_account"}],  # no account
        [{"action": "block_customer", "acc": "ACC00000003"}],
        [{"action": "remove_profit", "acc": "ACC00000003"}],  # no amount
        [{"action": "remove_profit", "acc": "ACC00000003", "amount_cents": 0}],
        [{"action": "remove_profit", "acc": "ACC00000003", "amount_cents": True}],
        [VOID, VOID],
    ],
)
def test_invalid_actions_are_refused(actions: list[dict[str, Any]]) -> None:
    with pytest.raises(CaseError):
        validate_actions(actions)


def test_the_actions_in_force_are_the_confirmed_ones() -> None:
    events = [
        CaseEvent(CAS, k, n, d)
        for n, (k, d) in enumerate(
            [OPENED, ("proposed", {"by": "s1", "actions": [BLOCK]}), ("rejected", {"by": "s2"})]
        )
    ]
    assert confirmed_actions(events) == []
    events += [
        CaseEvent(CAS, "proposed", 9, {"by": "s1", "actions": [VOID]}),
        CaseEvent(CAS, "confirmed", 10, {"by": "s2"}),
    ]
    assert confirmed_actions(events) == [VOID]


def test_the_migration_allows_exactly_the_kinds_and_statuses() -> None:
    sql = dict(_MIGRATIONS)[5]
    kinds = re.search(r"kind IN \(('opened'[^)]*)\)", sql)
    statuses = re.search(r"status IN \(([^)]*)\)", sql)
    assert kinds is not None and statuses is not None
    assert tuple(re.findall(r"'([a-z_]+)'", kinds.group(1))) == CASE_KINDS
    assert tuple(re.findall(r"'([a-z_]+)'", statuses.group(1))) == CASE_STATUSES


def test_service_levels() -> None:
    cases = load_policy().cases
    assert cases.due("high", 1_000) == 1_000 + 24 * 3_600_000
    assert cases.due("low", 0) == 168 * 3_600_000
    import tomllib

    data = tomllib.loads(DEFAULT_POLICY.read_text())
    data["cases"]["sla_hours"]["high"] = 200  # longer than low
    with pytest.raises(PolicyError):
        parse_policy(data)
    data["cases"]["sla_hours"] = {"low": 1, "medium": 1}
    with pytest.raises(PolicyError):
        parse_policy(data)


# --- payouts ----------------------------------------------------------------------------


def test_an_open_case_holds_payouts_and_removed_profit_is_never_paid() -> None:
    held = eligibility(POLICY, funded(open_case=True), NOW)
    cases = next(c for c in held.checks if c.name == "cases")
    assert (cases.status, cases.detail, held.eligible) == (FAIL, "held for review", False)
    clean = next(c for c in eligibility(POLICY, funded(), NOW).checks if c.name == "cases")
    assert clean.status == PASS
    removed = eligibility(POLICY, funded(violations_cents=20_000), NOW)
    assert (removed.violations_cents, removed.payable_cents, removed.trader_share_cents) == (
        20_000,
        40_000,
        32_000,
    )
    assert eligibility(POLICY, funded(violations_cents=90_000), NOW).payable_cents == 0


# --- the engine -------------------------------------------------------------------------

T0 = 1_790_000_000_000


def run(inputs: list[Any]) -> tuple[Machine, list[list[object]]]:
    machine = Machine(CODEC.encode(setup_from_policy(load_policy())), {1: bytes(32)})
    machine.use_price_key(1)
    out = []
    for n, item in enumerate(inputs, start=1):
        events = CODEC.decode(machine.apply(f"{n}-0", item).record.events)
        assert isinstance(events, tuple)
        out.append(list(events))
    return machine, out


def buy(order_id: str, ts: int) -> PlaceOrder:
    return PlaceOrder(OrderRequest(order_id, "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100), ts)


def test_voiding_closes_positions_and_locks_the_account() -> None:
    inputs = [
        OpenAccount("A", "lite-funded-v1", 1_000_000, T0),
        PriceUpdate("EUR/USD", 1, T0, T0, 110_000),
        buy("o1", T0 + 1),
        PriceUpdate("EUR/USD", 2, T0 + 2, T0 + 2, 110_100),
        VoidAccount("A", CAS, T0 + 3),
        buy("o2", T0 + 4),
        VoidAccount("A", CAS, T0 + 5),
        VoidAccount("Z", CAS, T0 + 6),
    ]
    machine, out = run(inputs)
    voided, closed = out[4][0], out[4][1]
    assert isinstance(voided, AccountVoided) and (voided.account_id, voided.case) == ("A", CAS)
    assert voided.rsv_id == "lite-funded-v1"
    assert isinstance(closed, PositionClosed) and closed.reason is CloseReason.VOID
    (refused,) = out[5]
    assert isinstance(refused, OrderRejected) and refused.reason is RejectReason.ACCOUNT_LOCKED
    (again,) = out[6]
    assert isinstance(again, InputRejected)
    assert again.reason is InputRejectReason.ACCOUNT_NOT_ACTIVE
    (unknown,) = out[7]
    assert (
        isinstance(unknown, InputRejected) and unknown.reason is InputRejectReason.UNKNOWN_ACCOUNT
    )
    assert machine.supervisor.status("A").value == "voided"
    assert machine.supervisor.engine.positions("A") == ()
    replayed, _ = run(inputs)
    assert replayed.head == machine.head
    state = machine.state()
    assert CODEC.decode(CODEC.encode(state)) == state


def test_a_voided_account_is_not_breached_afterwards() -> None:
    inputs = [
        OpenAccount("A", "lite-funded-v1", 1_000_000, T0),
        PriceUpdate("EUR/USD", 1, T0, T0, 110_000),
        VoidAccount("A", CAS, T0 + 1),
        PriceUpdate("EUR/USD", 2, T0 + 2 * 86_400_000, T0 + 2 * 86_400_000, 110_001),
    ]
    _, out = run(inputs)
    kinds = {type(e).__name__ for e in out[-1]}
    assert "AccountBreached" not in kinds and "DayStarted" not in kinds


def test_void_input_validation() -> None:
    with pytest.raises(ValueError):
        VoidAccount("", CAS, T0)
    with pytest.raises(ValueError):
        VoidAccount("A", "", T0)
    assert replace(VoidAccount("A", CAS, T0), ts=T0 + 1).ts == T0 + 1
