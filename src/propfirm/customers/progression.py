"""A passed stage and what follows it, rebuilt from an append-only history.

When an evaluation account passes (the engine's StagePassed), a progression
(PRG00000001) records it and what comes next, from the product as the policy
in force defines it at that moment:
- another evaluation stage (Lite and Classic: S1 -> S2): the next account
  opens automatically on that stage's rule set in force;
- the funded stage (Lite and Classic after S2, 1-Step after S1): an
  employee reviews the pass and approves or denies it. Funded onboarding
  (Tier 2 verification, contract, signature, the funded account) follows an
  approval (P2 step 3b);
- nothing (the product or its next stage is no longer in the policy): the
  progression is blocked for an employee to resolve.

Events, in the order they can happen:
- created: the pass (balance, trading days, journal seq) and the next stage,
  with its rule set when it is an evaluation stage;
- account_assigned, provision_attempted (one per try), provisioned, failed:
  opening the next evaluation account, as for a purchase;
- approved, denied: an employee's decision on a funded review (who, why).
The `progressions` status columns are a cache of `progression_state`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from propfirm.policy import ProductPolicy
from propfirm.rules.products import RuleSet, Stage

PROGRESSION_KINDS = (
    "created",
    "account_assigned",
    "provision_attempted",
    "provisioned",
    "failed",
    "approved",
    "denied",
)
EVALUATION_STAGES = (Stage.S1, Stage.S2)


class ProgressionError(ValueError):
    """Events that no valid progression produces."""


@dataclass(frozen=True, slots=True)
class ProgressionEvent:
    prg: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProgressionState:
    """The cached columns of `progressions`."""

    status: str  # provisioning, provisioned, failed, review, approved, denied, blocked
    to_rsv_id: str | None
    to_acc: str | None
    failure: str | None
    decided_by: str | None
    decided_at: int | None


def next_stage(product: ProductPolicy, passed: Stage) -> RuleSet | None:
    """The rule set of the stage after `passed` in the product; None when the
    product has no stage after it, or does not have `passed` at all."""
    stages = [r.stage for r in product.rule_sets]
    if passed not in stages:
        return None
    index = stages.index(passed) + 1
    return product.rule_sets[index] if index < len(stages) else None


def progression_state(prg: str, events: Iterable[ProgressionEvent]) -> ProgressionState:
    status: str | None = None
    to_rsv_id: str | None = None
    to_acc: str | None = None
    failure: str | None = None
    decided_by: str | None = None
    decided_at: int | None = None

    def refuse(event: ProgressionEvent) -> ProgressionError:
        return ProgressionError(f"{prg}: {event.kind} cannot follow status {status}")

    for event in events:
        if event.prg != prg or event.kind not in PROGRESSION_KINDS:
            raise ProgressionError(f"{prg}: unexpected event {event.prg} {event.kind}")
        detail = event.detail
        match event.kind:
            case "created":
                if status is not None:
                    raise refuse(event)
                to_stage = detail.get("to_stage")
                if to_stage == Stage.S2.value:
                    status, to_rsv_id = "provisioning", str(detail["to_rsv_id"])
                elif to_stage == Stage.FUNDED.value:
                    status = "review"
                elif to_stage is None:
                    status, failure = "blocked", str(detail["reason"])
                else:
                    raise refuse(event)
            case "account_assigned":
                if status != "provisioning" or to_acc is not None:
                    raise refuse(event)
                to_acc = str(detail["acc"])
            case "provision_attempted":
                if status != "provisioning" or to_acc is None:
                    raise refuse(event)
            case "provisioned":
                if status != "provisioning" or to_acc is None:
                    raise refuse(event)
                status = "provisioned"
            case "failed":
                if status != "provisioning":
                    raise refuse(event)
                status, failure = "failed", str(detail["reason"])
            case "approved" | "denied":
                if status != "review":
                    raise refuse(event)
                status = event.kind
                decided_by, decided_at = str(detail["by"]), event.ts
    if status is None:
        raise ProgressionError(f"{prg}: no created event")
    return ProgressionState(status, to_rsv_id, to_acc, failure, decided_by, decided_at)
