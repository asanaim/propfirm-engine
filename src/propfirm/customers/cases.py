"""Integrity, fraud and compliance cases (outline M16, M17, M20), rebuilt
from an append-only history.

One case per customer at a time: an alert about a customer who already has
a case not yet closed joins that case; otherwise it opens a new one
(CAS00000001). Alerts come from employees and from the platform's own
checks (a screening hit, the same verified identity on two customers).

Every case has a severity (the highest of its alerts), a due time from the
policy's service levels, an assigned employee, notes, a decision and its
audit trail. The decision:
- cleared, or warning: one employee, and the case closes;
- actioned: one or more actions, and a second employee must confirm
  (four eyes); then the actions are carried out and the case closes:
  - remove_profit (an account, an amount): profit removed for a violation
    (V in the payable profit; it is never paid);
  - void_account (an account): the engine closes its positions and locks it;
  - block_customer: the customer can no longer buy, onboard or be paid.
Nobody handles a case about themselves, and the confirming employee is not
the one who proposed. New evidence (an alert) on a proposed decision sends
the case back to open; a closed case can be reopened on evidence (appeal).

While a customer has a case not closed: no funded review is approved, no
funded contract is issued, and no payout is eligible. Customers never see
cases (no tipping off): they see only that something is held for review.

Events, in the order they can happen:
- opened: the customer, the first alert's kind, severity and due time;
- alert: an alert joined (id, source, kind, severity, account, due time);
- assigned, note;
- closed: cleared or warning (who, why);
- proposed: actioned, with its actions (who, why);
- rejected: the second employee sends it back (who, why);
- confirmed: the second employee confirms (who, why, and `as`: confirm,
  or authorise, which a void or profit removal needs: an admin's step
  [decided 31 Oct 2026]; confirmations recorded before carry no `as`);
- applied: one action carried out (and its result);
- reopened (who, why).
The `cases` status columns are a cache of `case_state`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

CASE_KINDS = (
    "opened",
    "alert",
    "assigned",
    "note",
    "closed",
    "proposed",
    "rejected",
    "confirmed",
    "applied",
    "reopened",
)
CASE_STATUSES = ("open", "decision_pending", "actioning", "closed")
ALERT_KINDS = ("integrity", "fraud", "aml")
SEVERITIES = ("low", "medium", "high")
OUTCOMES = ("cleared", "warning", "actioned")
ACTIONS = ("remove_profit", "void_account", "block_customer")
# actions on an account's money: a T2 or above proposes (passes), an admin
# authorises [decided 31 Oct 2026]
AUTHORISED = ("remove_profit", "void_account")
CONFIRM_STEPS = ("confirm", "authorise")


def needs_authorisation(actions: Iterable[Mapping[str, Any]]) -> bool:
    return any(a.get("action") in AUTHORISED for a in actions)


class CaseError(ValueError):
    """Events that no valid case produces."""


@dataclass(frozen=True, slots=True)
class CaseEvent:
    cas: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CaseState:
    """The cached columns of `cases`."""

    status: str
    severity: str
    due_at: int
    assignee: str | None
    outcome: str | None
    proposed_by: str | None
    pending_actions: tuple[int, ...]  # indexes of confirmed actions not yet carried out
    closed_at: int | None


def action_key(action: Mapping[str, Any]) -> tuple[str, str]:
    return str(action["action"]), str(action.get("acc") or "")


def validate_actions(actions: object) -> list[dict[str, Any]]:
    """Actions as stored: each named once (per account); amounts positive."""
    if not isinstance(actions, list) or not actions:
        raise CaseError("an actioned decision needs at least one action")
    seen: set[tuple[str, str]] = set()
    out = []
    for action in actions:
        if not isinstance(action, Mapping) or action.get("action") not in ACTIONS:
            raise CaseError(f"unknown action {action!r}")
        name = action["action"]
        acc = action.get("acc")
        if name in ("remove_profit", "void_account") and not isinstance(acc, str):
            raise CaseError(f"{name} needs an account")
        if name == "block_customer" and acc is not None:
            raise CaseError("block_customer names no account")
        if name == "remove_profit":
            amount = action.get("amount_cents")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
                raise CaseError("remove_profit needs a positive amount_cents")
        if action_key(action) in seen:
            raise CaseError(f"{name} is listed twice")
        seen.add(action_key(action))
        out.append(dict(action))
    return out


def case_state(cas: str, events: Iterable[CaseEvent]) -> CaseState:
    status: str | None = None
    severity = "low"
    due_at = 0
    assignee: str | None = None
    outcome: str | None = None
    proposed_by: str | None = None
    actions: list[dict[str, Any]] = []
    pending: list[int] = []
    closed_at: int | None = None

    def refuse(event: CaseEvent) -> CaseError:
        return CaseError(f"{cas}: {event.kind} cannot follow status {status}")

    def raise_severity(level: str, due: int) -> None:
        nonlocal severity, due_at
        if level not in SEVERITIES:
            raise CaseError(f"{cas}: severity {level!r}")
        if SEVERITIES.index(level) > SEVERITIES.index(severity):
            severity = level
        due_at = min(due_at, due) if due_at else due

    for event in events:
        if event.cas != cas or event.kind not in CASE_KINDS:
            raise CaseError(f"{cas}: unexpected event {event.cas} {event.kind}")
        d = event.detail
        match event.kind:
            case "opened":
                if status is not None:
                    raise refuse(event)
                status = "open"
                raise_severity(str(d["severity"]), int(d["due_at"]))
            case "alert":
                if status not in ("open", "decision_pending"):
                    raise refuse(event)
                raise_severity(str(d["severity"]), int(d["due_at"]))
                if status == "decision_pending":  # new evidence: decide again
                    status, outcome, proposed_by, actions = "open", None, None, []
            case "assigned":
                if status == "closed" or status is None:
                    raise refuse(event)
                assignee = str(d["to"])
            case "note":
                if status is None:
                    raise refuse(event)
            case "closed":
                by_one = status == "open" and d["outcome"] in ("cleared", "warning")
                carried_out = status == "actioning" and not pending and d["outcome"] == "actioned"
                if not (by_one or carried_out):
                    raise refuse(event)
                status, outcome, closed_at = "closed", str(d["outcome"]), event.ts
            case "proposed":
                if status != "open":
                    raise refuse(event)
                actions = validate_actions(d["actions"])
                status, outcome, proposed_by = "decision_pending", "actioned", str(d["by"])
            case "rejected":
                if status != "decision_pending" or d["by"] == proposed_by:
                    raise refuse(event)
                status, outcome, proposed_by, actions = "open", None, None, []
            case "confirmed":
                if status != "decision_pending" or d["by"] == proposed_by:
                    raise refuse(event)
                step = d.get("as")
                if step is not None and step not in CONFIRM_STEPS:
                    raise refuse(event)
                if step == "confirm" and needs_authorisation(actions):
                    raise refuse(event)  # an admin authorises a void or profit removal
                status, pending = "actioning", list(range(len(actions)))
            case "applied":
                index = d["index"]
                if status != "actioning" or index not in pending:
                    raise refuse(event)
                if action_key(d["action"]) != action_key(actions[index]):
                    raise refuse(event)
                pending.remove(index)
            case "reopened":
                if status != "closed":
                    raise refuse(event)
                status, outcome, proposed_by, actions, closed_at = "open", None, None, [], None
    if status is None:
        raise CaseError(f"{cas}: no opened event")
    return CaseState(
        status, severity, due_at, assignee, outcome, proposed_by, tuple(pending), closed_at
    )


def proposed_actions(events: Iterable[CaseEvent]) -> list[dict[str, Any]]:
    """The actions waiting for the second employee (none when no decision is
    pending)."""
    proposed: list[dict[str, Any]] = []
    for event in events:
        if event.kind == "proposed":
            proposed = validate_actions(event.detail["actions"])
        elif event.kind in ("rejected", "reopened", "confirmed", "alert"):
            proposed = []
    return proposed


def confirmed_actions(events: Iterable[CaseEvent]) -> list[dict[str, Any]]:
    """The actions of the decision in force (proposed, then confirmed)."""
    actions: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []
    for event in events:
        if event.kind == "proposed":
            proposed = validate_actions(event.detail["actions"])
        elif event.kind == "confirmed":
            actions = proposed
        elif event.kind in ("rejected", "reopened"):
            proposed = []
            if event.kind == "reopened":
                actions = []
    return actions
