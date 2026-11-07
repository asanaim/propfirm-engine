"""Rule AC00 for identity and funded reviews: two people decide."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from propfirm.customers.four_eyes import (
    Proposal,
    SameProposer,
    next_event,
    open_proposal,
    proposal_json,
)
from propfirm.customers.onboarding import OnboardingError, OnboardingEvent, onboarding_state
from propfirm.customers.progression import ProgressionError, ProgressionEvent, progression_state


@dataclass(frozen=True)
class E:
    kind: str
    ts: int = 1
    detail: dict[str, Any] = field(default_factory=dict)


def proposed(approve: bool, by: str = "staff-2") -> E:
    return E("proposed", 5, {"approve": approve, "by": by, "by_email": None, "reason": "r"})


def test_the_first_decision_is_a_proposal() -> None:
    kind, detail = next_event(None, True, "staff-2", None, "clean")
    assert (kind, detail["approve"], detail["by"]) == ("proposed", True, "staff-2")


def test_the_proposer_never_carries_out_their_own_proposal() -> None:
    waiting = open_proposal([proposed(True)])
    with pytest.raises(SameProposer):
        next_event(waiting, True, "staff-2", None, "again")


def test_a_second_person_agreeing_decides_and_names_both() -> None:
    waiting = open_proposal([proposed(False)])
    kind, detail = next_event(waiting, False, "staff-1", None, "copy trading")
    assert (kind, detail["by"], detail["proposed_by"]) == ("denied", "staff-1", "staff-2")


def test_a_second_person_disagreeing_rejects_the_proposal_and_the_review_stays_open() -> None:
    waiting = open_proposal([proposed(True)])
    kind, detail = next_event(waiting, False, "staff-1", None, "not convinced")
    assert kind == "proposal_rejected" and detail["proposed_by"] == "staff-2"
    assert open_proposal([proposed(True), E(kind, 6, detail)]) is None


def test_the_open_proposal_and_its_view() -> None:
    assert open_proposal([]) is None
    assert open_proposal([proposed(True), E("approved")]) is None
    p = open_proposal([proposed(True)])
    assert p == Proposal(True, "staff-2", None, "r", 5)
    assert proposal_json(p) == {
        "decision": "approve", "by": "staff-2", "by_email": None, "reason": "r", "ts": 5,
    }  # fmt: skip


def test_proposals_keep_a_funded_review_in_review_and_only_there() -> None:
    created = ProgressionEvent("PRG1", "created", 1, {"to_stage": "funded"})
    prop = ProgressionEvent("PRG1", "proposed", 2, proposed(True).detail)
    rej = ProgressionEvent("PRG1", "proposal_rejected", 3, {"by": "staff-1"})
    assert progression_state("PRG1", [created, prop, rej]).status == "review"
    approved = ProgressionEvent("PRG1", "approved", 4, {"by": "staff-1"})
    assert progression_state("PRG1", [created, prop, approved]).status == "approved"
    with pytest.raises(ProgressionError):
        progression_state("PRG1", [created, approved, prop])


def test_proposals_keep_an_identity_review_in_review_and_only_there() -> None:
    first = [
        OnboardingEvent("ONB1", "created", 1, {}),
        OnboardingEvent("ONB1", "kyc_requested", 2, {"kyc": "KYC1"}),
        OnboardingEvent("ONB1", "kyc_approved", 3, {"kyc": "KYC1"}),
        OnboardingEvent("ONB1", "review_required", 4, {}),
    ]
    prop = OnboardingEvent("ONB1", "proposed", 5, proposed(False).detail)
    assert onboarding_state("ONB1", [*first, prop]).status == "review"
    denied = OnboardingEvent("ONB1", "denied", 6, {"by": "staff-1"})
    assert onboarding_state("ONB1", [*first, prop, denied]).status == "rejected"
    with pytest.raises(OnboardingError):
        onboarding_state("ONB1", [*first[:3], prop])
