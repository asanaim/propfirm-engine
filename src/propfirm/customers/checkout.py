"""Registration, ordering, payment and account provisioning.

Registration creates a CIF only after the access checks pass (outline M03,
M04): minimum age from the date of birth, and the country gate when it is
switched on (policy file, or an admin change). A refusal is recorded
without a CIF. Every request reads the policy in force (`PolicySource`).

Ordering: AML screening at the first purchase (CC-6), then the price from
the policy (pricing), then an invoice from the payment provider.

What may be bought (review D4, owner decisions of 12 Nov 2026), checked
before the invoice:
- the product and the size are on sale (`catalogue`);
- the allocation cap (`scaling.max_allocation_cents`, $400,000): the sizes
  of the trader's committed capital plus the new order may not exceed it.
  Committed: every order whose account is open or has passed and waits for
  its next stage, every order paid and not yet opened, and every order not
  yet paid for up to `catalogue.unpaid_hold_hours`. A breached or voided
  account no longer counts;
- one Instant account per trader (`catalogue.one_instant_funding_per_
  trader`): none of the trader's committed orders is a product sold
  straight to a funded account.

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
An order for a funded account (Instant) opens its account only after
funded onboarding: Tier 2 verification and the signed agreement
(`customers.funding`). Until then it stays paid, with its onboarding.
"""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Protocol

from propfirm.access.gating import age_on, check_country
from propfirm.customers.casework import Casework
from propfirm.customers.pricing import DiscountKind, PricingError, price
from propfirm.customers.providers import PaymentProvider, Screener
from propfirm.customers.store import (
    CifRecord,
    CustomerStore,
    OrderRecord,
    RetakeTakenError,
    TrialTakenError,
)
from propfirm.journal.inputs import Input, OpenAccount
from propfirm.policy import (
    AddonOffer,
    Policy,
    PolicySource,
    ProductPolicy,
    instant_funding,
    stage_of,
)
from propfirm.rules.addons import is_trial, ordered, with_addons
from propfirm.rules.products import Stage
from propfirm.service.gateway import Outcome


class Submitter(Protocol):
    async def publish(self, item: Input) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class AccountStatuses(Protocol):
    """The engine's live view of an account (its `status`)."""

    async def account(self, account_id: str) -> dict[str, Any] | None: ...


# an account in one of these still holds its capital: open, or passed and
# waiting for its next stage
_HOLDING = ("active", "passed")


class FundedGate(Protocol):
    """Funded onboarding, as an order sees it."""

    async def order_ready(self, ord_id: str) -> bool: ...

    async def for_order(self, order: OrderRecord, ts: int) -> object: ...


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
        funded: FundedGate | None = None,
        cases: Casework | None = None,
        live: AccountStatuses | None = None,
    ) -> None:
        """`funded` holds funded orders until onboarding is done (None: they
        open at payment, as before step 3b; tests only)."""
        self._store = store
        self._live = live  # None: open accounts are counted from the records alone
        self._policy = policy
        self._payments = payments
        self._screener = screener
        self._gateway = gateway
        self._wait_s = wait_s
        self._funded = funded
        self._cases = cases

    @property
    def adapter(self) -> str:
        """The payment provider this checkout uses (a method names it)."""
        return str(self._payments.name)

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
        self,
        cif: CifRecord,
        product_id: str,
        size_cents: int,
        code: str | None,
        ts: int,
        addons: tuple[str, ...] = (),
    ) -> tuple[OrderRecord, str]:
        """Create an order and its invoice; return (order, payment URL).
        `addons`: add-on ids (D-13.18); the account opens on its stage's
        rule set with them."""
        if cif.status != "registered":
            raise CheckoutError("blocked", "this customer cannot buy")
        policy = await self._policy.current()
        try:
            product = policy.product(product_id)
        except KeyError:
            raise CheckoutError("unknown_product", f"no product {product_id!r}") from None
        await self._check_offer(policy, cif.cif, product_id, size_cents, ts)
        offers = self._addon_offers(policy, product, addons)
        if not await self._store.screened_clear(cif.cif):
            screening = await self._screener.screen(
                cif.full_name, cif.date_of_birth.isoformat(), cif.residence_country
            )
            await self._store.record_screening(
                cif.cif, screening.provider, screening.result, ts, screening.detail
            )
            if screening.result != "clear":
                await self._store.block(cif.cif, cif.subject, ts, {"reason": "screening_hit"})
                if self._cases is not None:
                    detail = {"at": "first_purchase", "provider": screening.provider}
                    await self._cases.alert(cif.cif, None, "screening", "aml", "high", detail, ts)
                raise CheckoutError("screening", "this customer cannot buy")
        code_share: Fraction | None = None
        if code is not None:
            code_share = await self._store.code_discount(code)
            if code_share is None:
                raise CheckoutError("unknown_code", "this code is not valid")
        prior = await self._store.prior_accounts(cif.cif, product.id)
        hold_since = ts - policy.catalogue.unpaid_hold_hours * 3_600_000
        basis = None
        if code_share is None:
            basis = await self.retake_basis(cif.cif, product, size_cents, ts)
        try:
            quote = price(
                product, size_cents, prior, code_share, retake=basis is not None, addons=offers
            )
        except PricingError as exc:
            raise CheckoutError("pricing", str(exc)) from exc
        if quote.discount_kind is not DiscountKind.RETAKE:
            basis = None
        first_stage = product.rule_sets[0]
        try:
            order = await self._store.create_order(
                cif=cif.cif,
                product_id=product.id,
                size_cents=size_cents,
                list_price_cents=quote.list_price_cents,
                discount_kind=quote.discount_kind.value,
                discount=quote.discount,
                discount_code=code if quote.discount_kind is DiscountKind.CODE else None,
                price_cents=quote.price_cents,
                rsv_id=with_addons(first_stage.rsv_id, quote.addons),
                provider=self._payments.name,
                ts=ts,
                retake_of=basis,
                hold_since=hold_since,
                addon_cents=quote.addon_cents,
            )
        except RetakeTakenError:
            raise CheckoutError("retake_taken", "this breach's retake is already in use") from None
        invoice = await self._payments.create_invoice(order.ord, order.price_cents)
        stored = await self._store.set_provider_ref(order.ord, invoice.provider_ref, ts)
        return stored, invoice.pay_url

    async def start_trial(self, cif: CifRecord, ts: int) -> OrderRecord:
        """Open the customer's free trial (D-13.17): the trial product's
        first stage rules on the trial size, no payment, one per customer."""
        if cif.status != "registered":
            raise CheckoutError("blocked", "this customer cannot start a trial")
        policy = await self._policy.current()
        trial = policy.trial
        if not trial.on:
            raise CheckoutError("trial_off", "there is no free trial")
        rules = policy.trial_rule_set()
        product = policy.product(trial.product_id)
        try:
            order = await self._store.create_order(
                cif=cif.cif,
                product_id=trial.product_id,
                size_cents=trial.size_cents,
                # recorded at the size's list price, all of it off, so
                # reports show what was given away
                list_price_cents=dict(product.prices_cents)[trial.size_cents],
                discount_kind=DiscountKind.TRIAL.value,
                discount=Fraction(1),
                discount_code=None,
                price_cents=0,
                rsv_id=rules.rsv_id,
                provider="trial",
                ts=ts,
            )
        except TrialTakenError:
            raise CheckoutError("trial_used", "the free trial was already used") from None
        await self._store.mark_paid(order.ord, ts)  # nothing to pay
        return await self.provision(order.ord, ts)

    @staticmethod
    def _addon_offers(
        policy: Policy, product: ProductPolicy, addons: tuple[str, ...]
    ) -> tuple[AddonOffer, ...]:
        """The offers for `addons`; CheckoutError for one unknown, not sold
        with this product, switched off, or named twice."""
        try:
            chosen = ordered(list(addons))
        except ValueError as exc:
            raise CheckoutError("addon", str(exc)) from None
        offers = []
        for addon_id in chosen:
            offer = policy.addons.offer(addon_id)
            if offer is None or product.id not in offer.products:
                raise CheckoutError("addon", f"{addon_id} is not sold with {product.name}")
            if addon_id in policy.addons.off:
                raise CheckoutError("addon", f"{addon_id} is not on sale")
            offers.append(offer)
        return tuple(offers)

    async def retake_basis(
        self, cif: str, product: ProductPolicy, size_cents: int, now: int
    ) -> str | None:
        """The breached order a new account of `product` and `size_cents`
        may use for the retake price (D-13.6), oldest first, or None."""
        if product.retake_discount <= 0:
            return None
        for order in await self.retake_offers(cif, now):
            if order.product_id == product.id and order.size_cents == size_cents:
                return order.ord
        return None

    async def retake_offers(self, cif: str, now: int) -> list[OrderRecord]:
        """The breached orders the customer may retake at the retake price,
        oldest first. One retake at a time: none while another retake order
        is unpaid in its hold or its account still holds capital. Each
        breach gives one retake; a failed or lapsed retake order frees it.
        Products with no retake discount are left out."""
        if self._live is None:
            return []
        policy = await self._policy.current()
        rate = {p.id: p.retake_discount for p in policy.products}
        hours = policy.catalogue.unpaid_hold_hours
        live = await self._store.retakes(cif, now - hours * 3_600_000)
        held = {o.ord for o in await self.committed(cif, now, hours)}
        if held & live.keys():
            return []
        used = set(live.values())
        accounts: dict[str, list[str]] = {}
        for account in await self._store.accounts_for(cif):
            accounts.setdefault(account.ord, []).append(account.acc)
        offers = []
        for order in await self._store.orders_for(cif):
            opened = accounts.get(order.ord)
            if (
                is_trial(order.rsv_id)  # a trial gives no retake
                or rate.get(order.product_id, 0) <= 0
                or order.status != "provisioned"
                or order.ord in used
                or not opened
            ):
                continue
            view = await self._live.account(max(opened))
            if view is not None and view.get("status") == "breached":
                offers.append(order)
        return offers

    async def committed(self, cif: str, now: int, hold_hours: int) -> list[OrderRecord]:
        """The trader's orders that hold capital now (see the module text)."""
        accounts: dict[str, list[str]] = {}
        for account in await self._store.accounts_for(cif):
            accounts.setdefault(account.ord, []).append(account.acc)
        ended = await self._store.ended_orders(cif)
        held = []
        for order in await self._store.orders_for(cif):
            if order.status == "failed" or order.ord in ended:
                continue
            if order.status == "pending":
                if now - order.created_at < hold_hours * 3_600_000:
                    held.append(order)
                continue
            opened = accounts.get(order.ord, [])
            if not opened:
                held.append(order)  # paid, not opened yet (Instant: until signed)
                continue
            latest = max(opened)  # the order's newest account: its current stage
            view = await self._live.account(latest) if self._live is not None else None
            if view is None or view.get("status") in _HOLDING:
                held.append(order)
        return held

    async def _check_offer(
        self, policy: Policy, cif: str, product_id: str, size_cents: int, ts: int
    ) -> None:
        """Refuse what is not on sale, over the allocation cap, or a second
        Instant account (review D4)."""
        catalogue = policy.catalogue
        product = policy.product(product_id)
        if product.id in catalogue.products_off:
            raise CheckoutError("not_on_sale", f"{product.name} is not on sale")
        if size_cents in catalogue.sizes_off:
            raise CheckoutError("size_off", "this account size is not on sale")
        held = await self.committed(cif, ts, catalogue.unpaid_hold_hours)
        if catalogue.one_instant_funding_per_trader and instant_funding(product):
            for order in held:
                other = next((p for p in policy.products if p.id == order.product_id), None)
                if other is not None and instant_funding(other):
                    raise CheckoutError(
                        "one_instant", f"one {product.name} account at a time: {order.ord} holds it"
                    )
        cap = policy.scaling.max_allocation_cents
        total = sum(o.size_cents for o in held) + size_cents
        if total > cap:
            raise CheckoutError(
                "allocation_cap",
                f"your accounts would total ${total // 100:,}; "
                f"the most one trader may hold at once is ${cap // 100:,}",
            )

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
        funded = self._funded is not None and stage_of(order.rsv_id) is Stage.FUNDED
        if funded and self._funded is not None and not await self._funded.order_ready(order.ord):
            await self._funded.for_order(order, ts)
            return order  # stays paid until the agreement is signed
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
