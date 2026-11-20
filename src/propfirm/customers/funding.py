"""Funded onboarding, the service (outline F6): verification, checks,
contract, signature and the funded account. The steps and their record are
described in `customers.onboarding`.

Every step is safe to repeat and to run in several processes at once: each
one is recorded only if the onboarding is still in the status the step
expects (checked under the row's lock), and `advance` (the API's background
loop) retries whatever a crash or an unanswered call left undone.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from propfirm.access.gating import age_on, check_country
from propfirm.customers.casework import Casework
from propfirm.customers.checkout import Checkout, opened, refusal_reason
from propfirm.customers.kyc import KycDecision, KycError, KycProvider
from propfirm.customers.onboarding import (
    MAX_KYC_ATTEMPTS,
    funded_rule_set,
    render_contract,
    same_name,
)
from propfirm.customers.onboarding_store import (
    KycResult,
    OnboardingRecord,
    OnboardingStateError,
    OnboardingStore,
)
from propfirm.customers.providers import Screener
from propfirm.customers.store import CifRecord, CustomerStore
from propfirm.journal.inputs import Input, OpenAccount
from propfirm.policy import PolicySource
from propfirm.rules.addons import addons_of, base_id
from propfirm.service.gateway import Outcome

log = logging.getLogger(__name__)
KYC_POLL_AFTER_MS = 60_000  # ask the provider about a case with no webhook after this


class Submitter(Protocol):
    async def publish(self, item: Input) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


@dataclass(frozen=True, slots=True)
class Signature:
    """What the signer sends and what the request shows about them."""

    subject: str  # the login
    typed_name: str
    sha256: str  # of the contract text they read
    ip: str | None
    user_agent: str | None


class Onboarding:
    def __init__(
        self,
        store: OnboardingStore,
        customers: CustomerStore,
        policy: PolicySource,
        kyc: KycProvider,
        screener: Screener,
        gateway: Submitter,
        checkout: Checkout | None,
        *,
        sandbox: bool,
        callback_url: str | None = None,
        wait_s: float = 5.0,
        cases: Casework | None = None,
        held: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """`checkout` opens Instant accounts (None: Instant onboardings stop
        after signing). Outside the sandbox, a contract whose firm name or
        governing law is not set is never issued."""
        self._store = store
        self._customers = customers
        self._policy = policy
        self._kyc = kyc
        self._screener = screener
        self._gateway = gateway
        self._checkout = checkout
        self._sandbox = sandbox
        self._callback = callback_url
        self._wait_s = wait_s
        self._cases = cases
        self._held = held  # whether new funded accounts wait (reserve red)

    @property
    def store(self) -> OnboardingStore:
        return self._store

    @property
    def kyc(self) -> KycProvider:
        return self._kyc

    # --- the background loop ----------------------------------------------------------

    async def advance(self, ts: int) -> None:
        """Start onboardings for approved funded reviews, then finish every
        step a crash or a missing answer left undone."""
        policy = await self._policy.current()
        funded = {}
        for product in policy.products:
            rule_set = funded_rule_set(product)
            if rule_set is not None:
                funded[product.id] = rule_set.rsv_id
        await self._store.from_progressions(funded, ts)
        for case in await self._store.open_cases(ts - KYC_POLL_AFTER_MS):
            if case.provider != self._kyc.name:
                continue
            try:
                await self.kyc_update(case.provider, case.session_id, ts)
            except KycError:
                log.warning("identity check %s: provider not answering", case.kyc)
        for record in await self._store.with_status("checks"):
            await self.run_checks(record.onb, ts)
        for record in await self._store.with_status("contract"):
            await self.issue_contract(record.onb, ts)
        for record in await self._store.with_status("signed"):
            await self.provision(record.onb, ts)

    # --- verification -----------------------------------------------------------------

    async def start_verification(self, onb: str, cif: CifRecord, ts: int) -> OnboardingRecord:
        """Open (or return) the customer's identity check. A customer with an
        approved check already reuses it and goes straight to the checks."""
        record = await self._owned(onb, cif)
        if cif.status != "registered":
            raise OnboardingStateError("blocked", "this customer cannot continue")
        if record.status not in ("verification", "verification_declined"):
            raise OnboardingStateError("not_verifying", f"{onb} is {record.status}")
        open_case = record.status == "verification" and record.kyc is not None
        if open_case and await self._store.result(str(record.kyc)) is None:
            return record  # a session is open: the same link
        prior = await self._store.approved_case(cif.cif)
        if prior is not None and record.status == "verification" and record.kyc is None:
            detail = {"kyc": prior.kyc, "reused": True}
            await self._store.append(onb, "kyc_approved", ts, detail, when=("verification",))
            await self.run_checks(onb, ts)
            return await self._get(onb)
        if record.kyc_attempts >= MAX_KYC_ATTEMPTS:
            await self._store.append(
                onb,
                "rejected",
                ts,
                {"reason": "verification_attempts"},
                when=("verification_declined",),
            )
            raise OnboardingStateError("attempts", "no identity check attempts left")
        session = await self._kyc.start(onb, self._callback)
        return await self._store.new_case(
            onb, session.provider, session.session_id, session.url, ts
        )

    async def kyc_update(self, provider: str, session_id: str, ts: int) -> bool:
        """Read a session's outcome from the provider and apply it (webhooks
        and the poll both end here). True when a final outcome was new."""
        case = await self._store.case_by_session(provider, session_id)
        if case is None:
            return False
        decision = await self._kyc.decision(session_id)
        return await self.kyc_result(case.kyc, decision, ts)

    async def kyc_result(self, kyc: str, decision: KycDecision, ts: int) -> bool:
        if decision.result is None:
            return False
        new = await self._store.record_result(kyc, decision, ts)
        for onb in await self._store.waiting_on(kyc):
            if decision.result == "approved":
                await self._store.append(
                    onb, "kyc_approved", ts, {"kyc": kyc}, when=("verification",)
                )
                await self.run_checks(onb, ts)
            else:
                detail = {"kyc": kyc, "provider": dict(decision.detail)}
                await self._store.append(onb, "kyc_declined", ts, detail, when=("verification",))
        return new

    # --- checks -------------------------------------------------------------------------

    async def run_checks(self, onb: str, ts: int) -> OnboardingRecord:
        """Screening, age and country on the verified identity; a verified
        identity that differs from the customer record goes to review."""
        record = await self._get(onb)
        if record.status != "checks" or record.kyc is None:
            return record
        result = await self._store.result(record.kyc)
        cif = await self._cif(record.cif)
        assert result is not None and result.result == "approved" and result.full_name
        policy = await self._policy.current()
        screening = await self._screener.screen(
            result.full_name,
            (result.date_of_birth or cif.date_of_birth).isoformat(),
            result.document_country or cif.residence_country,
        )
        await self._customers.record_screening(
            cif.cif, screening.provider, screening.result, ts, screening.detail
        )
        await self._store.append(
            onb,
            "screened",
            ts,
            {"provider": screening.provider, "result": screening.result},
            when=("checks",),
        )
        refusals = []
        if screening.result != "clear":
            refusals.append("screening_hit")
            await self._customers.block(cif.cif, cif.subject, ts, {"reason": "screening_hit"})
            if self._cases is not None:
                hit = {"onboarding": onb, "provider": screening.provider}
                await self._cases.alert(cif.cif, None, "screening", "aml", "high", hit, ts)
        if result.date_of_birth is not None:
            age = age_on(result.date_of_birth, datetime.fromtimestamp(ts / 1000, UTC).date())
            if age < policy.access.min_age:
                refusals.append(f"age:{age}<{policy.access.min_age}")
        gate = check_country(
            policy.access, declared=cif.residence_country, document=result.document_country
        )
        refusals += gate.reasons
        if refusals:
            detail: dict[str, Any] = {"reason": refusals[0], "reasons": refusals}
            await self._store.append(onb, "rejected", ts, detail, when=("checks",))
            return await self._get(onb)
        if self._cases is not None and result.date_of_birth is not None:
            others = [
                other
                for other, name in await self._store.verified_elsewhere(
                    cif.cif, result.date_of_birth
                )
                if name and same_name(name, result.full_name)
            ]
            if others:  # the same person as another customer: multi-accounting?
                same: dict[str, Any] = {
                    "onboarding": onb,
                    "kyc": record.kyc,
                    "same_identity_as": others,
                }
                await self._cases.alert(
                    cif.cif, None, "duplicate_identity", "fraud", "medium", same, ts
                )
        differences = _differences(cif, result)
        if differences:
            await self._store.append(
                onb, "review_required", ts, {"differences": differences}, when=("checks",)
            )
            return await self._get(onb)
        await self._store.append(onb, "cleared", ts, {}, when=("checks",))
        return await self.issue_contract(onb, ts)

    async def decide(
        self, onb: str, approve: bool, subject: str, email: str | None, reason: str, ts: int
    ) -> OnboardingRecord:
        if approve and self._cases is not None:
            record = await self._get(onb)
            if await self._cases.has_open_case(record.cif):
                raise OnboardingStateError("open_case", "the customer has a case under review")
        await self._store.decide(onb, approve, subject, email, reason, ts)
        if approve:
            return await self.issue_contract(onb, ts)
        return await self._get(onb)

    # --- contract and signature -------------------------------------------------------

    async def issue_contract(self, onb: str, ts: int) -> OnboardingRecord:
        record = await self._get(onb)
        if record.status != "contract" or record.kyc is None:
            return record
        if self._cases is not None and await self._cases.has_open_case(record.cif):
            log.info("%s waits: the customer has a case under review", onb)
            return record
        policy = await self._policy.current()
        if not policy.contract.complete and not self._sandbox:
            log.warning("%s waits: the contract's firm name or governing law is not set", onb)
            return record
        try:
            product = policy.product(record.product_id)
        except KeyError:
            log.warning("%s waits: product %s is not in the policy", onb, record.product_id)
            return record
        base = base_id(record.rsv_id)
        rule_set = next((r for r in product.rule_sets if r.rsv_id == base), None)
        if rule_set is None:
            rule_set = funded_rule_set(product)
        if rule_set is not None and addons_of(record.rsv_id):
            rule_set = policy.with_addons(rule_set, addons_of(record.rsv_id))  # D-13.18
        if rule_set is None or rule_set.rsv_id != record.rsv_id:
            log.warning("%s waits: rule set %s is not in the policy", onb, record.rsv_id)
            return record
        acc = record.acc
        if acc is None:
            if record.source == "order":
                acc = await self._customers.assign_account(record.ord, ts)
                await self._store.record_account(onb, acc, ts)
            else:
                acc = await self._store.assign_account(onb, ts)
        result = await self._store.result(record.kyc)
        assert result is not None and result.full_name
        contract_id = await self._store.next_contract_id()
        text = render_contract(
            policy,
            contract_id=contract_id,
            issued_ts=ts,
            trader_name=result.full_name,
            cif=record.cif,
            kyc_case=record.kyc,
            acc=acc,
            product=product,
            rule_set=rule_set,
            size_cents=record.size_cents,
        )
        await self._store.issue_contract(
            onb, contract_id, text.template, text.body, text.sha256, text.draft, ts
        )
        return await self._get(onb)

    async def sign(
        self, onb: str, cif: CifRecord, signature: Signature, ts: int
    ) -> OnboardingRecord:
        """Sign the issued contract as the verified person."""
        record = await self._owned(onb, cif)
        if cif.status != "registered":
            raise OnboardingStateError("blocked", "this customer cannot continue")
        if cif.subject != signature.subject:
            raise OnboardingStateError("not_signer", "only the customer signs")
        if record.status != "signature" or record.contract is None or record.kyc is None:
            raise OnboardingStateError("not_signing", f"{onb} is {record.status}")
        contract = await self._store.contract(record.contract)
        result = await self._store.result(record.kyc)
        assert contract is not None and result is not None and result.full_name
        if signature.sha256 != contract.sha256:
            raise OnboardingStateError("other_text", "this is not the text of the contract")
        if not same_name(signature.typed_name, result.full_name):
            raise OnboardingStateError("name", "type your name as on your identity document")
        detail = {
            "contract": contract.contract,
            "sha256": contract.sha256,
            "kyc": record.kyc,
            "subject": signature.subject,
            "typed_name": signature.typed_name,
            "ip": signature.ip,
            "user_agent": signature.user_agent,
        }
        signed = await self._store.append(onb, "signed", ts, detail, when=("signature",))
        if signed is None:
            raise OnboardingStateError("not_signing", f"{onb} is no longer waiting to be signed")
        return await self.provision(onb, ts)

    # --- the funded account -------------------------------------------------------------

    async def provision(self, onb: str, ts: int) -> OnboardingRecord:
        record = await self._get(onb)
        if record.status != "signed" or record.acc is None:
            return record
        if record.source != "order" and self._held is not None and await self._held():
            return record  # D-14.23: new funded accounts queue in red; retried each round
        if record.source == "order":
            if self._checkout is None:
                return record
            order = await self._checkout.provision(record.ord, ts)
            if order.status == "provisioned":
                await self._store.mark_provisioned(onb, ts, None)
            elif order.status == "failed":
                detail = {"reason": order.failure or "not_opened", "journal_seq": None}
                await self._store.append(onb, "failed", ts, detail, when=("signed",))
            return await self._get(onb)
        entry_id = await self._gateway.publish(
            OpenAccount(record.acc, record.rsv_id, record.size_cents, ts)
        )
        await self._store.append(  # before the wait
            onb,
            "provision_attempted",
            ts,
            {"acc": record.acc, "entry_id": entry_id},
            when=("signed",),
        )
        outcome = await self._gateway.outcome(entry_id, self._wait_s)
        if outcome.seq is not None:
            if opened(outcome.events, record.acc):
                await self._store.mark_provisioned(onb, ts, outcome.seq)
            else:
                failed: dict[str, Any] = {
                    "reason": refusal_reason(outcome.events),
                    "journal_seq": outcome.seq,
                }
                await self._store.append(onb, "failed", ts, failed, when=("signed",))
        return await self._get(onb)

    # --- helpers ----------------------------------------------------------------------

    async def _get(self, onb: str) -> OnboardingRecord:
        record = await self._store.onboarding(onb)
        if record is None:
            raise OnboardingStateError("unknown", f"no onboarding {onb}")
        return record

    async def _owned(self, onb: str, cif: CifRecord) -> OnboardingRecord:
        record = await self._store.onboarding(onb)
        if record is None or record.cif != cif.cif:
            raise OnboardingStateError("unknown", f"no onboarding {onb}")
        return record

    async def _cif(self, cif: str) -> CifRecord:
        found = await self._customers.cif(cif)
        assert found is not None
        return found


def _differences(cif: CifRecord, result: KycResult) -> list[str]:
    found = []
    if result.full_name is None or not same_name(cif.full_name, result.full_name):
        found.append("full_name")
    if result.date_of_birth is not None and result.date_of_birth != cif.date_of_birth:
        found.append("date_of_birth")
    return found
