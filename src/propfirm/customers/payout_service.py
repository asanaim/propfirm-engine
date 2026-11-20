"""Payouts, the service: wallets, requests, approvals, and the payout run
(the process is in `customers.payout_flow`).

`advance(ts)` runs every round: approved payouts are checked again and
debited by the engine (DebitPayout, refused a second time
for the same PO, so a retry after a crash or a lost answer never takes the
money twice); debited payouts are sent by the payout provider (idempotent
on the PO); sent payouts are polled until the provider confirms them.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from propfirm.analysis import ShortHold, short_hold, trades_from
from propfirm.customers.casework import Casework, Submitter
from propfirm.customers.networks import NETWORKS
from propfirm.customers.notifications import wallet_notice
from propfirm.customers.onboarding_store import OnboardingStore
from propfirm.customers.payout_flow import payout_state
from propfirm.customers.payout_store import PayoutRecord, PayoutRefused, PayoutStore, Wallet
from propfirm.customers.payouts import (
    FAIL,
    Eligibility,
    FundedAccount,
    eligibility,
    eligibility_json,
)
from propfirm.customers.providers import PayoutOrder, PayoutProvider
from propfirm.customers.store import CifRecord, CustomerStore
from propfirm.customers.transfers import WithdrawalRails, WithdrawalUnavailable
from propfirm.journal.inputs import DebitPayout
from propfirm.policy import PolicySource, parse_percent
from propfirm.rules.addons import addons_of
from propfirm.settings.notices import Notices

log = logging.getLogger(__name__)
SEND_RETRY_MS = 60_000  # a failed send is tried again after this
DEBIT_RETRY_MS = 60_000  # an unanswered debit is published again after this


class StepCount(Protocol):
    async def steps_taken(self, ord_id: str) -> int: ...


class TradeHistory(Protocol):
    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...


class LiveAccounts(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


def _failing(e: Eligibility) -> list[str]:
    return [c.name for c in e.checks if c.status == FAIL]


class NoticeSource(Protocol):
    async def notices(self) -> Notices: ...


class Payouts:
    def __init__(
        self,
        store: PayoutStore,
        customers: CustomerStore,
        live: LiveAccounts,
        policy: PolicySource,
        gateway: Submitter,
        provider: PayoutProvider,
        *,
        identity: OnboardingStore | None = None,  # Tier 2 identity checks
        cases: Casework | None = None,
        notices: NoticeSource | None = None,  # notification templates (rule AC12)
        history: TradeHistory | None = None,  # projected account events (short holds)
        scaling: StepCount | None = None,  # scaling steps taken (the split ladder)
        withdrawals: WithdrawalRails | None = None,  # withdrawal methods (D-14.25)
        wait_s: float = 5.0,
    ) -> None:
        self._store = store
        self._customers = customers
        self._live = live
        self._policy = policy
        self._gateway = gateway
        self._provider = provider
        self._identity = identity
        self._cases = cases
        self._notices = notices
        self._history = history
        self._scaling = scaling
        self._withdrawals = withdrawals
        self._wait_s = wait_s

    @property
    def store(self) -> PayoutStore:
        return self._store

    # --- eligibility ------------------------------------------------------------------

    async def eligibility(self, acc: str, now: int, *, ignore: str | None = None) -> Eligibility:
        """The account's payout eligibility now; `ignore`: a payout of the
        account not to count as pending (the one being checked again)."""
        record = await self._customers.account(acc)
        live = await self._live.account(acc)
        if record is None or live is None:
            raise PayoutRefused("unknown", f"no account {acc}")
        policy = await self._policy.current()
        order = await self._customers.order(record.ord)
        cif = await self._customers.cif(record.cif)
        assert order is not None and cif is not None
        try:
            product = policy.product(record.product_id)
        except KeyError as exc:
            raise PayoutRefused("product", f"{record.product_id} is not in the policy") from exc
        tier2 = await self._identity.approved_case(cif.cif) if self._identity else None
        pending = await self._store.in_progress(acc)
        wallet = await self._store.wallet(cif.cif)
        removed = await self._cases.removed_profit(acc) if self._cases else 0
        violations = removed - await self._store.violations_taken(acc)
        rules = live["rules"]
        account = FundedAccount(
            acc=acc,
            product=product,
            stage=rules["stage"],
            consistency=parse_percent(rules["consistency_percent"] or "", "consistency", True),
            funded_at=record.created_at,
            baseline_cents=record.size_cents,
            fee_paid_cents=order.price_cents,
            payouts_paid=await self._store.made(record.ord),
            last_payout_at=await self._store.last_debited(acc),
            status=live["status"],
            locked=live["locked"],
            balance_cents=live["balance_cents"],
            equity_cents=live["equity_cents"],
            open_positions=len(live["positions"]),
            pending_orders=len(live["pending_orders"]),
            trading_day_ids=live.get("trading_day_ids", []),
            day_pnl=[(d, c) for d, c in live.get("day_pnl", [])],
            cif_status=cif.status,
            tier2_approved=tier2 is not None,
            residence_country=cif.residence_country,
            document_country=tier2.document_country if tier2 else None,
            open_case=await self._cases.has_open_case(cif.cif) if self._cases else False,
            violations_cents=violations,
            confirmed_cases=await self._cases.confirmed_cases(cif.cif) if self._cases else 0,
            scaling_steps=await self._scaling.steps_taken(record.ord) if self._scaling else 0,
            addons=addons_of(rules["rsv_id"]),
            pending_payout=pending is not None and pending.po != ignore,
            wallet_added_at=wallet.added_at if wallet else None,
            kept_floor_cents=(
                None if rules.get("floor_restarts_after_payout", True) else live["max_floor_cents"]
            ),
        )
        return eligibility(policy, account, now)

    # --- the trader -----------------------------------------------------------------

    async def _mfa(self, mfa: bool, auth_time: int | None, now: int) -> None:
        """MFA by policy, entered at most `step_up_minutes` ago (step-up)."""
        payouts = (await self._policy.current()).payouts
        if not payouts.require_mfa:
            return
        if not mfa:
            raise PayoutRefused("mfa_required", "sign in with a second factor first")
        limit = payouts.step_up_minutes * 60_000
        if limit and (auth_time is None or now - auth_time > limit):
            raise PayoutRefused(
                "reauth_required",
                f"confirm with your second factor (within {payouts.step_up_minutes} minutes)",
                max_age_s=payouts.step_up_minutes * 60,
            )

    async def add_wallet(
        self,
        cif: CifRecord,
        subject: str,
        mfa: bool,
        address: str,
        ts: int,
        auth_time: int | None = None,
        network: str = "TRC20",
    ) -> Wallet:
        """A new payout wallet replaces the one in use (after the cooling-off
        period, if one is set); the customer is emailed at the registered
        address. The network must be open (D-14.26) and the address valid
        for it."""
        await self._mfa(mfa, auth_time, ts)
        found = NETWORKS.get(network)
        if found is None or network not in (await self._policy.current()).payouts.networks:
            raise PayoutRefused("invalid", f"payouts are not sent on {network}")
        if not found.valid(address):
            raise PayoutRefused("invalid", f"not an address on {found.name}: {found.hint}")
        previous = await self._store.wallet(cif.cif)
        wording = None if self._notices is None else await self._notices.notices()
        notice = wallet_notice(
            cif.email,
            cif.full_name,
            address,
            previous.address if previous else None,
            ts,
            wording,
        )
        detail = {"mfa": mfa, "auth_time": auth_time}
        return await self._store.add_wallet(cif.cif, network, address, subject, ts, detail, notice)

    async def short_hold(self, acc: str) -> ShortHold | None:
        """The short-hold measure of the account's current payout period;
        None without trade history."""
        if self._history is None:
            return None
        record = await self._customers.account(acc)
        if record is None:
            raise PayoutRefused("unknown", f"no account {acc}")
        policy = await self._policy.current()
        since = await self._store.last_debited(acc) or record.created_at
        events: list[dict[str, Any]] = []
        after = 0
        while True:
            page = await self._history.account_events(acc, after, 1_000)
            events += page
            if len(page) < 1_000:
                break
            after = int(page[-1]["seq"])
        return short_hold(trades_from(events), policy.payouts.short_hold_seconds, since)

    async def _hold_short_holds(self, acc: str, cif: CifRecord, ts: int) -> None:
        """Raise an integrity case, once per payout period, when the period's
        profit comes mostly from trades closed within seconds; the open case
        then holds the request for review (section 1, 13 Nov 2026)."""
        if self._cases is None:
            return
        percent = (await self._policy.current()).payouts.short_hold_share_percent
        try:
            measure = await self.short_hold(acc) if percent > 0 else None
        except Exception:  # history not readable: staff still approve every payout
            log.exception("short-hold measure of %s unavailable", acc)
            return
        if measure is None:
            return
        key = f"period:{measure.since}"
        if measure.over(percent):
            # D-14.5: the account is class C from now on, and this period is reviewed
            current = await self._customers.account_class(acc)
            if current is None or current[0] not in ("C", "D"):
                reason = f"short holds: {measure.share_basis_points / 100:.2f}% of profit"
                await self._customers.set_account_class(acc, "C", reason, "system", ts)
            if not await self._cases.has_alert(acc, "short_hold", key):
                detail = {"key": key, "threshold_percent": percent, **measure.view()}
                await self._cases.alert(
                    cif.cif, acc, "short_hold", "integrity", "medium", detail, ts
                )
            return
        # a class C account has every payout reviewed, short holds or not
        current = await self._customers.account_class(acc)
        reviewed = current is not None and current[0] in ("C", "D")
        if reviewed and not await self._cases.has_alert(acc, "risk_class", key):
            assert current is not None
            detail = {"key": key, "class": current[0], "reason": current[1], **measure.view()}
            await self._cases.alert(cif.cif, acc, "risk_class", "integrity", "low", detail, ts)

    async def request(
        self,
        acc: str,
        cif: CifRecord,
        subject: str,
        mfa: bool,
        ts: int,
        auth_time: int | None = None,
        method: str | None = None,
    ) -> PayoutRecord:
        await self._mfa(mfa, auth_time, ts)
        policy = await self._policy.current()
        await self._hold_short_holds(acc, cif, ts)
        e = await self.eligibility(acc, ts)
        if not e.eligible:
            raise PayoutRefused("not_eligible", f"checks failing: {', '.join(_failing(e))}")
        if e.debit_cents <= 0:
            raise PayoutRefused("not_eligible", "no profit to pay out")
        method_id, charge = await self._method(method, e.trader_amount_cents)
        record = await self._customers.account(acc)
        wallet = await self._store.wallet(cif.cif)
        live = await self._live.account(acc)
        assert record is not None and wallet is not None and live is not None
        detail = {
            "by": subject,
            "mfa": mfa,
            "auth_time": auth_time,
            "approvals_required": policy.payouts.approvals_required,
            **(
                {"approval": "pass_authorise"}
                if policy.payouts.approval == "pass_authorise"
                else {}
            ),
            "rsv_id": live["rules"]["rsv_id"],
            "trading_during_payout": policy.payouts.trading_during_payout,
            "eligibility": eligibility_json(e),
            **({"method": method_id, "charge_cents": charge} if method_id else {}),
        }
        return await self._store.request(
            acc,
            cif.cif,
            record.ord,
            wallet.wal,
            e.trader_amount_cents,
            e.debit_cents,
            ts,
            detail,
            method_id=method_id,
            charge_cents=charge,
        )

    async def methods(self) -> list[Any]:
        """The withdrawal methods a trader may choose now (none: the default)."""
        return [] if self._withdrawals is None else await self._withdrawals.enabled()

    async def _method(self, method: str | None, amount_cents: int) -> tuple[str | None, int]:
        """The method a payout goes through and its charge, at request time
        (rule AC13): once any withdrawal method is on, one must be chosen,
        the amount must be inside its limits, and its charge is taken from
        the amount. With none on, the platform's default rail, no charge."""
        enabled = await self.methods()
        if not enabled:
            if method is not None:
                raise PayoutRefused("unknown_method", "no withdrawal method is offered")
            if self._provider.name == "none":
                raise PayoutRefused("method_unavailable", "no withdrawal method is offered yet")
            return None, 0
        chosen = next((m for m in enabled if m.method_id == method), None)
        if chosen is None:
            names = ", ".join(m.method_id for m in enabled)
            raise PayoutRefused("choose_method", f"choose a withdrawal method: {names}")
        assert self._withdrawals is not None
        try:
            await self._withdrawals.for_method(chosen.method_id)
        except WithdrawalUnavailable as exc:
            raise PayoutRefused("method_unavailable", str(exc)) from exc
        if not chosen.allows(amount_cents):
            raise PayoutRefused(
                "outside_limits",
                f"{chosen.name} takes {chosen.min_cents} to "
                f"{chosen.max_cents or 'any'} cents; this payout is {amount_cents}",
            )
        charge = chosen.charge_cents(amount_cents)
        if charge >= amount_cents:
            raise PayoutRefused("charge_too_high", "the method's charge takes the whole payout")
        return chosen.method_id, charge

    async def cancel(self, po: str, cif: CifRecord, subject: str, ts: int) -> PayoutRecord:
        payout = await self._store.payout(po)
        if payout is None or payout.cif != cif.cif:
            raise PayoutRefused("unknown", f"no payout {po}")
        done = await self._store.append(po, "cancelled", ts, {"by": subject}, when=("requested",))
        if done is None:
            raise PayoutRefused("not_allowed", "only a payout not yet approved can be cancelled")
        return done

    # --- employees --------------------------------------------------------------------

    async def next_step(self, po: str) -> str | None:
        """pass or authorise when the payout waits for that step; None when
        the payout counts approvals (or is not waiting)."""
        if await self._store.payout(po) is None:
            raise PayoutRefused("unknown", f"no payout {po}")
        return payout_state(po, await self._store.history(po)).next_step

    async def approve(
        self,
        po: str,
        staff: str,
        reason: str,
        ts: int,
        step: str | None = None,
        tier: int | None = None,
    ) -> PayoutRecord:
        """`step` (pass or authorise) and the employee's tier are recorded
        with the approval; the history refuses a step out of order."""
        detail: dict[str, Any] = {"reason": reason}
        if step is not None:
            detail["as"] = step
        if tier is not None:
            detail["tier"] = tier
        return await self._store.decide(po, "approved", staff, ts, detail)

    async def deny(self, po: str, staff: str, reason: str, ts: int) -> PayoutRecord:
        return await self._store.decide(po, "denied", staff, ts, {"reason": reason})

    # --- the payout run -------------------------------------------------------------

    async def advance(self, ts: int) -> None:
        """Move each payout one step on; a payout debited in this round is
        sent in the same round (each step is recorded before the next)."""
        await self._each(await self._store.due(), self._debit, ts)
        await self._each(await self._store.payouts(status="debited"), self._send, ts)
        await self._each(await self._store.payouts(status="sent"), self._confirm, ts)

    async def _each(
        self,
        payouts: list[PayoutRecord],
        step: Callable[[PayoutRecord, int], Awaitable[None]],
        ts: int,
    ) -> None:
        for payout in payouts:
            try:
                await step(payout, ts)
            except Exception:  # retried next round
                log.exception("payout %s: %s failed", payout.po, step.__name__)

    async def recheck(self, payout: PayoutRecord, ts: int) -> tuple[str | None, Eligibility]:
        """Why the payout can no longer be paid as approved, or None."""
        e = await self.eligibility(payout.acc, ts, ignore=payout.po)
        if not e.eligible:
            return f"checks failing: {', '.join(_failing(e))}", e
        if (e.trader_amount_cents, e.debit_cents) != (
            payout.trader_amount_cents,
            payout.debit_cents,
        ):
            return "the amount changed since the request", e
        wallet = await self._store.wallet(payout.cif)
        if wallet is None or wallet.wal != payout.wal:
            return "the payout wallet changed since the request", e
        return None, e

    async def _debit(self, payout: PayoutRecord, ts: int) -> None:
        po = payout.po
        history = await self._store.history(po)
        attempts = [e for e in history if e.kind == "debit_attempted"]
        if attempts and ts < attempts[-1].ts + DEBIT_RETRY_MS:
            return  # the last attempt may still be on its way to the engine
        if not attempts:
            reason, e = await self.recheck(payout, ts)
            if reason is not None:
                await self._store.append(
                    po,
                    "failed",
                    ts,
                    {"reason": reason, "eligibility": eligibility_json(e)},
                    when=("approved",),
                )
                return
            snapshot = {"eligibility": eligibility_json(e)}
            if await self._store.append(po, "rechecked", ts, snapshot, when=("approved",)) is None:
                return
        # once attempted, never checked again: the debit may already be in the
        # journal; the engine answers duplicate_payout to a repeat
        if await self._store.append(po, "debit_attempted", ts, {}, when=("approved",)) is None:
            return
        entry_id = await self._gateway.publish(DebitPayout(payout.acc, payout.debit_cents, po, ts))
        outcome = await self._gateway.outcome(entry_id, self._wait_s)
        if outcome.seq is None:
            return  # no answer yet: published again after DEBIT_RETRY_MS
        debited = any(e.get("type") == "PayoutDebited" for e in outcome.events)
        refused = next(
            (e.get("reason") for e in outcome.events if e.get("type") == "InputRejected"), None
        )
        detail = {"journal_seq": outcome.seq, "entry_id": entry_id}
        if debited or refused == "duplicate_payout":
            await self._store.append(po, "debited", ts, detail, when=("approved",))
        else:
            reason = f"refused by the engine: {refused}"
            await self._store.append(
                po, "failed", ts, {"reason": reason, **detail}, when=("approved",)
            )

    async def _send(self, payout: PayoutRecord, ts: int) -> None:
        history = await self._store.history(payout.po)
        last = next((e for e in reversed(history) if e.kind == "send_failed"), None)
        if last is not None and ts < last.ts + SEND_RETRY_MS:
            return
        wallet = await self._store.wallet_by_id(payout.wal)
        assert wallet is not None
        order = PayoutOrder(
            payout.po,
            payout.method_id,
            {"network": wallet.network, "address": wallet.address},
            payout.net_cents,  # the trader's amount less the method's charge
            payout.currency,
        )
        try:
            provider = await self._rail(payout)
            ref = await provider.send(order)
        except Exception as exc:
            log.warning("payout %s: sending failed: %s", payout.po, exc)
            error = {"error": type(exc).__name__, "method": payout.method_id}
            await self._store.append(payout.po, "send_failed", ts, error, when=("debited",))
            return
        detail = {"ref": ref, "provider": provider.name, "amount_cents": order.amount_cents}
        await self._store.append(payout.po, "sent", ts, detail, when=("debited",))

    async def _rail(self, payout: PayoutRecord) -> PayoutProvider:
        """The rail of the payout's method; the default without one."""
        if payout.method_id is None or self._withdrawals is None:
            return self._provider
        return await self._withdrawals.for_method(payout.method_id)

    async def _confirm(self, payout: PayoutRecord, ts: int) -> None:
        assert payout.provider_ref is not None
        found = await (await self._rail(payout)).status(payout.provider_ref)
        if found.status == "paid":
            await self._store.append(payout.po, "paid", ts, found.detail, when=("sent",))
