"""Funded onboarding (outline F6, M05, M18), rebuilt from an append-only history.

An onboarding (ONB00000001) starts when a funded account is earned: an
employee approves a funded review (a progression, step 3a), or an Instant
order is paid. It ends with the funded account open. In between:

1. Tier 2 verification: the customer completes an identity check with the
   KYC provider (Didit). A customer verified before reuses that result.
2. Checks, at that moment: AML screening; the minimum age on the verified
   date of birth; the country gate on the document country (when switched
   on). A verified name or date of birth that differs from the customer
   record goes to an employee review.
3. Contract: the account's ACC id is allocated, and the funded-trader
   agreement is generated from the policy in force; its SHA-256 is kept.
4. Signature, in the portal: only by the verified person (their login, the
   verified name typed, the contract's SHA-256 confirmed); the login, time,
   network address and device are recorded.
5. The funded account opens: for a progression, as the next account of the
   challenge; for Instant, by the order's own provisioning.

Events, in the order they can happen:
- created: source (progression or order), the funded rule set;
- kyc_requested: a provider session (case id);
- kyc_declined: the provider declined; the customer may try again, up to
  MAX_KYC_ATTEMPTS sessions;
- kyc_approved: the verified identity (case id);
- screened: the AML result at that moment;
- review_required: the verified identity differs from the customer record;
- approved, denied: an employee's decision on that review (who, why);
- rejected: verification or checks failed (reason): the end;
- cleared: every check passed;
- account_assigned: the ACC id, allocated once;
- contract_issued: contract id and SHA-256;
- signed: the signature's audit trail;
- provision_attempted, provisioned, failed: opening the account.
The `onboardings` status columns are a cache of `onboarding_state`.
"""

import hashlib
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from propfirm.policy import Policy, ProductPolicy, percent_text
from propfirm.rules.products import MaxLossKind, RuleSet

ONBOARDING_KINDS = (
    "created",
    "kyc_requested",
    "kyc_declined",
    "kyc_approved",
    "screened",
    "review_required",
    "approved",
    "denied",
    "rejected",
    "cleared",
    "account_assigned",
    "contract_issued",
    "signed",
    "provision_attempted",
    "provisioned",
    "failed",
)
STATUSES = (
    "verification",  # waiting for the customer's identity check
    "verification_declined",  # declined; may try again
    "checks",  # verified; screening and checks
    "review",  # an employee decides
    "rejected",
    "contract",  # cleared; contract being prepared
    "signature",  # contract issued; waiting for the signature
    "signed",  # the account is being opened
    "provisioned",
    "failed",
)
MAX_KYC_ATTEMPTS = 3  # [DEFAULT] identity-check sessions per onboarding
CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "config" / "contracts"


class OnboardingError(ValueError):
    """Events that no valid onboarding produces."""


@dataclass(frozen=True, slots=True)
class OnboardingEvent:
    onb: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OnboardingState:
    """The cached columns of `onboardings`."""

    status: str
    kyc: str | None  # the case in use
    kyc_attempts: int
    acc: str | None
    contract: str | None
    failure: str | None


def onboarding_state(onb: str, events: Iterable[OnboardingEvent]) -> OnboardingState:
    status: str | None = None
    kyc: str | None = None
    attempts = 0
    acc: str | None = None
    contract: str | None = None
    failure: str | None = None

    def refuse(event: OnboardingEvent) -> OnboardingError:
        return OnboardingError(f"{onb}: {event.kind} cannot follow status {status}")

    def need(event: OnboardingEvent, *allowed: str) -> None:
        if status not in allowed:
            raise refuse(event)

    for event in events:
        if event.onb != onb or event.kind not in ONBOARDING_KINDS:
            raise OnboardingError(f"{onb}: unexpected event {event.onb} {event.kind}")
        detail = event.detail
        match event.kind:
            case "created":
                if status is not None:
                    raise refuse(event)
                status = "verification"
            case "kyc_requested":
                need(event, "verification", "verification_declined")
                if attempts >= MAX_KYC_ATTEMPTS:
                    raise refuse(event)
                status, kyc, attempts = "verification", str(detail["kyc"]), attempts + 1
            case "kyc_declined":
                need(event, "verification")
                if kyc != detail["kyc"]:
                    raise refuse(event)
                status = "verification_declined"
            case "kyc_approved":
                need(event, "verification")
                reused = kyc is None and detail.get("reused") is True  # verified before
                if detail["kyc"] != kyc and not reused:
                    raise refuse(event)
                status, kyc = "checks", str(detail["kyc"])
            case "screened":
                need(event, "checks")
            case "review_required":
                need(event, "checks")
                status = "review"
            case "approved":
                need(event, "review")
                status = "contract"
            case "denied":
                need(event, "review")
                status, failure = "rejected", "denied"
            case "rejected":
                need(event, "verification", "verification_declined", "checks")
                status, failure = "rejected", str(detail["reason"])
            case "cleared":
                need(event, "checks")
                status = "contract"
            case "account_assigned":
                need(event, "contract")
                if acc is not None:
                    raise refuse(event)
                acc = str(detail["acc"])
            case "contract_issued":
                need(event, "contract")
                if acc is None:
                    raise refuse(event)
                status, contract = "signature", str(detail["contract"])
            case "signed":
                need(event, "signature")
                if detail.get("contract") != contract:
                    raise refuse(event)
                status = "signed"
            case "provision_attempted":
                need(event, "signed")
            case "provisioned":
                need(event, "signed")
                status = "provisioned"
            case "failed":
                need(event, "signed")
                status, failure = "failed", str(detail["reason"])
    if status is None:
        raise OnboardingError(f"{onb}: no created event")
    return OnboardingState(status, kyc, attempts, acc, contract, failure)


# --- identity -------------------------------------------------------------------------


def same_name(a: str, b: str) -> bool:
    """Names match ignoring letter case and spacing (not accents)."""
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def funded_rule_set(product: ProductPolicy) -> RuleSet | None:
    for rule_set in product.rule_sets:
        if rule_set.stage.value == "funded":
            return rule_set
    return None


# --- contract -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractText:
    template: str
    body: str
    sha256: str
    draft: bool


def _usd(cents: int) -> str:
    return f"{cents // 100:,}.{cents % 100:02d}"


def _days(pay_days: tuple[str, ...]) -> str:
    named = ["the last day" if d == "last" else d for d in pay_days]
    return named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]


DISCLOSURE = {
    "simulated": (
        "All trading on this account is simulated: orders are not sent to any "
        "market, and the account holds no money of the Trader or of the Firm. "
        "Payouts are the Firm's rewards under these terms."
    ),
    "live": ("Orders on this account may be placed in real markets with the Firm's capital."),
}


def render_contract(
    policy: Policy,
    *,
    contract_id: str,
    issued_ts: int,
    trader_name: str,
    cif: str,
    kyc_case: str,
    acc: str,
    product: ProductPolicy,
    rule_set: RuleSet,
    size_cents: int,
    templates: Path = CONTRACTS_DIR,
) -> ContractText:
    """The agreement text from the policy in force; DRAFT while the firm name
    or governing law is not decided."""
    terms = policy.contract
    text = (templates / f"{terms.template}.txt").read_text(encoding="utf-8")
    draft = not terms.complete
    pending = "[NOT SET: to be decided]"
    payouts, scaling = policy.payouts, policy.scaling
    values = {
        "draft_notice": "DRAFT: not for signature outside the sandbox." if draft else "",
        "contract_id": contract_id,
        "issued_utc": datetime.fromtimestamp(issued_ts / 1000, UTC).strftime("%Y-%m-%d %H:%M"),
        "firm_name": terms.firm_name or pending,
        "trader_name": trader_name,
        "cif": cif,
        "kyc_case": kyc_case,
        "acc": acc,
        "product_name": product.name,
        "allocation_usd": _usd(size_cents),
        "rsv_id": rule_set.rsv_id,
        "max_loss_percent": percent_text(rule_set.max_loss),
        "max_loss_kind": "trailing" if rule_set.max_loss_kind is MaxLossKind.TRAILING else "static",
        "daily_loss_percent": percent_text(rule_set.daily_loss),
        "consistency": "none"
        if rule_set.consistency is None
        else f"best day at most {percent_text(rule_set.consistency)} % of total profit",
        "disclosure": DISCLOSURE[terms.account_disclosure],
        "split_percent": percent_text(product.profit_split),
        "first_after_days": str(payouts.first_after_days),
        "pay_days": _days(payouts.pay_days),
        "minimum_usd": _usd(payouts.minimum_cents),
        "approvals": str(payouts.approvals_required),
        "review_months": str(scaling.review_months),
        "min_net_profit_percent": percent_text(scaling.min_net_profit),
        "min_paid_payouts": str(scaling.min_paid_payouts),
        "scaling_step_percent": percent_text(scaling.step),
        "max_allocation_usd": _usd(scaling.max_allocation_cents),
        "governing_law": terms.governing_law or pending,
    }
    body = string.Template(text).substitute(values)
    return ContractText(terms.template, body, hashlib.sha256(body.encode()).hexdigest(), draft)
