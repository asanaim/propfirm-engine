"""Two people for a review decision (rule AC00, decided 8 Nov 2026).

An identity review (KYC) and a funded review are decided by two people:
the first decision is a proposal ("proposed"); a second person, never the
proposer, who gives the same decision carries it out ("approved" or
"denied", naming both). A second person who disagrees rejects the proposal
("proposal_rejected"): the review stays open for a new proposal. Every step
is an append-only event with who and why.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

PROPOSED = "proposed"
PROPOSAL_REJECTED = "proposal_rejected"
DECISIONS = {True: "approved", False: "denied"}


class Event(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def ts(self) -> int: ...

    @property
    def detail(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Proposal:
    approve: bool
    by: str
    by_email: str | None
    reason: str
    ts: int


def open_proposal(events: Iterable[Event]) -> Proposal | None:
    """The proposal waiting for a second person, if any."""
    found: Proposal | None = None
    for event in events:
        if event.kind == PROPOSED:
            d = event.detail
            found = Proposal(bool(d["approve"]), str(d["by"]), d.get("by_email"), str(d["reason"]),
                             event.ts)  # fmt: skip
        elif event.kind in (PROPOSAL_REJECTED, "approved", "denied"):
            found = None
    return found


class SameProposer(Exception):
    """The proposer tried to carry out their own proposal."""


def next_event(
    proposal: Proposal | None, approve: bool, subject: str, email: str | None, reason: str
) -> tuple[str, dict[str, Any]]:
    """The event a decision records: a proposal, the decision itself (the
    second person agrees), or the proposal's rejection (they disagree)."""
    if proposal is None:
        return PROPOSED, {"approve": approve, "by": subject, "by_email": email, "reason": reason}
    if proposal.by == subject:
        raise SameProposer("the proposer does not decide their own proposal")
    if proposal.approve != approve:
        return PROPOSAL_REJECTED, {
            "by": subject,
            "by_email": email,
            "reason": reason,
            "proposed_by": proposal.by,
        }
    return DECISIONS[approve], {
        "by": subject,
        "by_email": email,
        "reason": reason,
        "proposed_by": proposal.by,
        "proposal_reason": proposal.reason,
    }


def proposal_json(p: Proposal | None) -> dict[str, Any] | None:
    if p is None:
        return None
    decision = "approve" if p.approve else "deny"
    return {
        "decision": decision,
        "by": p.by,
        "by_email": p.by_email,
        "reason": p.reason,
        "ts": p.ts,
    }
