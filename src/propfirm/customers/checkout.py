"""Registration, ordering, payment and account provisioning.

Registration creates a CIF only after the access checks pass (outline M03,
M04): minimum age from the date of birth, and the country gate when it is
switched on (policy file, or an admin change). A refusal is recorded
without a CIF. Every request reads the policy in force (`PolicySource`).

Ordering: AML screening at the first purchase (CC-6), then the price from
the policy (pricing), then an invoice from the payment provider.

Every step is an event in the order's append-only history
(`customers.history`); the order's status is a cache of that history.

Payment and provisioning, a saga with a retry (outline M08, M09):
1. the provider reports the payment: pending -> paid (a repeated report
   changes nothing);
2. the order gets its ACC id, allocated once;
3. OpenAccount(ACC, the product's first-stage rule set, size) goes to the
   engine; when the engine has opened the account (or had already opened
   it before a crash) the order becomes provisioned;
4. an engine refusal marks the order failed (refund is a staff action);
   no answer in time leaves it paid, and `reconcile` retries later.
"""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Protocol

from propfirm.access.gating import age_on, check_country
from propfirm.customers.pricing import DiscountKind, PricingError, price
from propfirm.customers.providers import PaymentProvider, Screener
from propfirm.customers.store import CifRecord, CustomerStore, OrderRecord
from propfirm.journal.inputs import Input, OpenAccount
from propfirm.policy import PolicySource
from propfirm.service.gateway import Outcome


class Submitter(Protocol):
    async def publish(self, item: Input) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class CheckoutError(Exception):
    """A request refused for a business reason (the message says which)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Registration:
    full_name: str
    date_of_birth: date
    residence_country: str


class Checkout:
    def __init__(
        self,
        store: CustomerStore,
        policy: PolicySource,
        payments: PaymentProvider,
        screener: Screener,
        gateway: Submitter,
        wait_s: float = 5.0,
    ) -> None:
        self._store = store
        self._policy = policy
        self._payments = payments
        self._screener = screener
        self._gateway = gateway
        self._wait_s = wait_s

    # --- registration ---------------------------------------------------------------

    async def register(
        self,
        subject: str,
        email: str | None,
        form: Registration,
        today: date,
        ts: int,
        ip_country: str | None = None,
    ) -> CifRecord:
        if not email:
            raise CheckoutError("email_required", "the login has no email address")
        name = " ".join(form.full_name.split())
        if not name:
            raise CheckoutError("name_required", "full name is required")
        access = (await self._policy.current()).access
        reasons: list[str] = []
        age = age_on(form.date_of_birth, today)
        if age < access.min_age:
            reasons.append(f"age:{age}<{access.min_age}")
        gate = check_country(access, declared=form.residence_country, ip=ip_country)
        reasons += gate.reasons
        detail: dict[str, Any] = {
            "residence_country": form.residence_country,
            "ip_country": ip_country,
            "country_gating": access.country_gating,
        }
        if reasons:
            await self._store.refused(subject, ts, {**detail, "reasons": reasons})
            raise CheckoutError("refused", "registration refused: " + ", ".join(reasons))
        return await self._store.register(
            subject, email, name, form.date_of_birth, form.residence_country, ts, detail
        )

    # --- ordering -------------------------------------------------------------------

    async def place_order(
        self, cif: CifRecord, product_id: str, size_cents: int, code: str | None, ts: int
    ) -> tuple[OrderRecord, str]:
        """Create an order and its invoice; return (order, payment URL)."""
        if cif.status != "registered":
            raise CheckoutError("blocked", "this customer cannot buy")
        try:
            product = (await self._policy.current()).product(product_id)
        except KeyError:
            raise CheckoutError("unknown_product", f"no product {product_id!r}") from None
        if not await self._store.screened_clear(cif.cif):
            screening = await self._screener.screen(
                cif.full_name, cif.date_of_birth.isoformat(), cif.residence_country
            )
            await self._store.record_screening(
                cif.cif, screening.provider, screening.result, ts, screening.detail
            )
            if screening.result != "clear":
                await self._store.block(cif.cif, cif.subject, ts, {"reason": "screening_hit"})
                raise CheckoutError("screening", "this customer cannot buy")
        code_share: Fraction | None = None
        if code is not None:
            code_share = await self._store.code_discount(code)
            if code_share is None:
                raise CheckoutError("unknown_code", "this code is not valid")
        prior = await self._store.prior_accounts(cif.cif, product.id)
        try:
            quote = price(product, size_cents, prior, code_share)
        except PricingError as exc:
            raise CheckoutError("pricing", str(exc)) from exc
        first_stage = product.rule_sets[0]
        order = await self._store.create_order(
            cif=cif.cif,
            product_id=product.id,
            size_cents=size_cents,
            list_price_cents=quote.list_price_cents,
            discount_kind=quote.discount_kind.value,
            discount=quote.discount,
            discount_code=code if quote.discount_kind is DiscountKind.CODE else None,
            price_cents=quote.price_cents,
            rsv_id=first_stage.rsv_id,
            provider=self._payments.name,
            ts=ts,
        )
        invoice = await self._payments.create_invoice(order.ord, order.price_cents)
        stored = await self._store.set_provider_ref(order.ord, invoice.provider_ref, ts)
        return stored, invoice.pay_url

    # --- payment and provisioning ---------------------------------------------------

    async def payment_received(self, provider: str, provider_ref: str, ts: int) -> OrderRecord:
        order = await self._store.order_by_ref(provider, provider_ref)
        if order is None:
            raise CheckoutError("unknown_payment", "no order for this payment")
        await self._store.mark_paid(order.ord, ts)
        return await self.provision(order.ord, ts)

    async def provision(self, ord_id: str, ts: int) -> OrderRecord:
        order = await self._store.order(ord_id)
        if order is None:
            raise CheckoutError("unknown_order", ord_id)
        if order.status != "paid":
            return order
        acc = await self._store.assign_account(order.ord, ts)
        entry_id = await self._gateway.publish(OpenAccount(acc, order.rsv_id, order.size_cents, ts))
        await self._store.provision_attempted(order.ord, acc, entry_id, ts)  # before the wait
        outcome = await self._gateway.outcome(entry_id, self._wait_s)
        if outcome.seq is not None:
            if opened(outcome.events, acc):
                await self._store.mark_provisioned(order, acc, ts, outcome.seq)
            else:
                await self._store.mark_failed(
                    order.ord, refusal_reason(outcome.events), ts, outcome.seq
                )
        updated = await self._store.order(order.ord)
        assert updated is not None
        return updated

    async def reconcile(self, ts: int) -> int:
        """Retry every paid order not yet provisioned; return how many."""
        pending = await self._store.unprovisioned()
        for order in pending:
            await self.provision(order.ord, ts)
        return len(pending)


def opened(events: Any, acc: str) -> bool:
    for event in events:
        if event.get("type") == "AccountOpened" and event.get("account_id") == acc:
            return True
        if (
            event.get("type") == "InputRejected"
            and event.get("reason") == "duplicate_account"
            and event.get("ref") == acc
        ):
            return True  # opened by an earlier attempt that crashed before recording it
    return False


def refusal_reason(events: Any) -> str:
    for event in events:
        if "reason" in event:
            return str(event["reason"])
    return "not_opened"
