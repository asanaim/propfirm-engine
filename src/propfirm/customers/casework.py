"""Cases, the service: alerts in, decisions out (rules in `customers.cases`).

Decisions with actions take effect only after a second employee confirms
them; `carry_out` then performs each action once and records it (a void
waits for the engine's answer and is retried by `advance` if none came).
"""

import logging
from typing import Any, Protocol

from propfirm.customers.cases import OUTCOMES, CaseError, confirmed_actions, validate_actions
from propfirm.customers.cases_store import CaseRecord, CaseRefused, CaseStore
from propfirm.customers.store import CustomerStore
from propfirm.journal.inputs import Input, VoidAccount
from propfirm.policy import PolicySource
from propfirm.service.gateway import Outcome

log = logging.getLogger(__name__)


class Submitter(Protocol):
    async def publish(self, item: Input) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class Casework:
    def __init__(
        self,
        store: CaseStore,
        customers: CustomerStore,
        policy: PolicySource,
        gateway: Submitter,
        wait_s: float = 5.0,
    ) -> None:
        self._store = store
        self._customers = customers
        self._policy = policy
        self._gateway = gateway
        self._wait_s = wait_s

    @property
    def store(self) -> CaseStore:
        return self._store

    # --- alerts and what they hold back ---------------------------------------------

    async def alert(
        self,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        detail: dict[str, Any],
        ts: int,
    ) -> tuple[str, str]:
        """Record an alert; return (CAS, ALR)."""
        due = (await self._policy.current()).cases.due(severity, ts)
        return await self._store.raise_alert(cif, acc, source, kind, severity, detail, due, ts)

    async def news_violations(self, ts: int) -> list[str]:
        """Integrity alerts for news violations the engine journaled (M13):
        a fill inside a restricted window on a funded account. As FTMO, it
        can end the account: staff decide on the case (a void needs a
        second employee), nothing happens automatically."""
        policy = (await self._policy.current()).cases
        return await self._store.alert_news_violations(policy.due, ts)

    async def has_open_case(self, cif: str) -> bool:
        return await self._store.open_case(cif) is not None

    async def removed_profit(self, acc: str) -> int:
        return await self._store.removed_profit(acc)

    # --- an employee's steps --------------------------------------------------------

    async def assign(self, cas: str, staff: str, ts: int) -> CaseRecord:
        return await self._store.act(cas, "assigned", staff, ts, {"to": staff})

    async def note(self, cas: str, staff: str, text: str, ts: int) -> CaseRecord:
        return await self._store.act(cas, "note", staff, ts, {"text": text})

    async def decide(
        self,
        cas: str,
        staff: str,
        outcome: str,
        actions: list[dict[str, Any]],
        reason: str,
        ts: int,
    ) -> CaseRecord:
        """Close as cleared or warning (no actions), or propose actions for a
        second employee to confirm."""
        record = await self._get(cas)
        if outcome not in OUTCOMES:
            raise CaseRefused("invalid", f"outcome {outcome!r}")
        if outcome != "actioned":
            if actions:
                raise CaseRefused("invalid", f"{outcome} takes no actions")
            return await self._store.act(
                cas, "closed", staff, ts, {"outcome": outcome, "reason": reason}
            )
        try:
            checked = validate_actions(actions)
        except CaseError as exc:
            raise CaseRefused("invalid", str(exc)) from exc
        for action in checked:
            acc = action.get("acc")
            if acc is not None:
                account = await self._customers.account(acc)
                if account is None or account.cif != record.cif:
                    raise CaseRefused("invalid", f"{acc} is not this customer's account")
        detail = {"actions": checked, "reason": reason}
        return await self._store.act(cas, "proposed", staff, ts, detail)

    async def reject(self, cas: str, staff: str, reason: str, ts: int) -> CaseRecord:
        return await self._store.act(cas, "rejected", staff, ts, {"reason": reason})

    async def confirm(self, cas: str, staff: str, reason: str, ts: int) -> CaseRecord:
        await self._store.act(cas, "confirmed", staff, ts, {"reason": reason})
        return await self.carry_out(cas, ts)

    async def reopen(self, cas: str, staff: str, reason: str, ts: int) -> CaseRecord:
        return await self._store.act(cas, "reopened", staff, ts, {"reason": reason})

    # --- carrying out a confirmed decision ------------------------------------------

    async def carry_out(self, cas: str, ts: int) -> CaseRecord:
        record = await self._get(cas)
        actions = confirmed_actions(await self._store.history(cas))
        for index in await self._store.pending_actions(cas):
            action = actions[index]
            result = await self._perform(record, action, ts)
            if result is None:
                continue  # no engine answer yet: `advance` retries
            await self._store.applied(cas, index, action, result, ts)
        await self._store.close_carried_out(cas, ts)
        return await self._get(cas)

    async def _perform(
        self, record: CaseRecord, action: dict[str, Any], ts: int
    ) -> dict[str, Any] | None:
        match action["action"]:
            case "remove_profit":
                return {"removed_cents": action["amount_cents"]}
            case "block_customer":
                cif = await self._customers.cif(record.cif)
                assert cif is not None
                if cif.status != "blocked":
                    await self._customers.block(
                        cif.cif, cif.subject, ts, {"reason": "case", "case": record.cas}
                    )
                return {"blocked": True}
            case "void_account":
                acc = str(action["acc"])
                entry_id = await self._gateway.publish(VoidAccount(acc, record.cas, ts))
                outcome = await self._gateway.outcome(entry_id, self._wait_s)
                if outcome.seq is None:
                    return None
                types = [e.get("type") for e in outcome.events]
                rejected = next(
                    (e.get("reason") for e in outcome.events if e.get("type") == "InputRejected"),
                    None,
                )
                return {
                    "journal_seq": outcome.seq,
                    "voided": "AccountVoided" in types,
                    "refused": rejected,  # e.g. account_not_active: already breached
                }
        raise CaseRefused("invalid", f"unknown action {action!r}")

    async def advance(self, ts: int) -> None:
        """Carry out confirmed decisions a crash or a missing answer left undone."""
        for record in await self._store.cases(status="actioning"):
            try:
                await self.carry_out(record.cas, ts)
            except Exception:  # retried next round
                log.exception("carrying out %s failed", record.cas)

    async def _get(self, cas: str) -> CaseRecord:
        record = await self._store.case(cas)
        if record is None:
            raise CaseRefused("unknown", f"no case {cas}")
        return record
