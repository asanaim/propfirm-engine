"""Stage progression: what happens after the engine reports a pass.

`poll` turns new StagePassed records into progressions, deciding the next
stage from the policy in force at that moment (`plan`). `advance` opens the
next evaluation account for every progression still provisioning, the same
saga as a purchase: the ACC id allocated once, each attempt recorded before
the wait, a duplicate_account answer counted as opened (a crash after the
engine opened it), a refusal recorded as failed. `decide` records an
employee's approval or denial of a funded review.

Both `poll` and `advance` run in the API's background loop and are safe to
run in several processes at once.
"""

from typing import Protocol

from propfirm.customers.casework import Casework
from propfirm.customers.checkout import opened, refusal_reason
from propfirm.customers.lifecycle_store import (
    DecisionError,
    Plan,
    ProgressionRecord,
    ProgressionStore,
)
from propfirm.customers.progression import next_stage
from propfirm.customers.store import AccountRecord
from propfirm.journal.inputs import Input, OpenAccount
from propfirm.policy import Policy, PolicySource
from propfirm.rules.products import Stage
from propfirm.service.gateway import Outcome
from propfirm.settings.notices import Notices


class Submitter(Protocol):
    async def publish(self, item: Input) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


def plan_for(policy: Policy, account: AccountRecord) -> Plan:
    """The stage after the one `account` passed, from `policy`."""
    try:
        product = policy.product(account.product_id)
    except KeyError:
        return Plan(None, None, "unknown_product")
    following = next_stage(product, Stage(account.stage))
    if following is None:
        return Plan(None, None, "no_next_stage")
    if following.stage is Stage.FUNDED:
        return Plan(Stage.FUNDED, None)  # the funded rule set is chosen at funding
    return Plan(following.stage, following.rsv_id)


class NoticeSource(Protocol):
    async def notices(self) -> Notices: ...


class Lifecycle:
    def __init__(
        self,
        store: ProgressionStore,
        policy: PolicySource,
        gateway: Submitter,
        wait_s: float = 5.0,
        cases: Casework | None = None,
        notices: NoticeSource | None = None,
    ) -> None:
        """`cases`: no funded review is approved while the customer has a
        case not closed. `notices`: the email wording in force (rule AC12);
        the built-in wording without it."""
        self._store = store
        self._policy = policy
        self._gateway = gateway
        self._wait_s = wait_s
        self._cases = cases
        self._notices = notices

    @property
    def store(self) -> ProgressionStore:
        return self._store

    async def poll(self, ts: int) -> list[str]:
        """Record the passes journaled since the last poll; return new PRG ids."""
        policy = await self._policy.current()
        wording = None if self._notices is None else await self._notices.notices()
        return await self._store.record_passes(lambda a: plan_for(policy, a), ts, notices=wording)

    async def advance(self, ts: int) -> int:
        """Try to open the next account of every progression still
        provisioning; return how many are provisioned now."""
        done = 0
        for record in await self._store.with_status("provisioning"):
            if (await self.provision(record.prg, ts)).status == "provisioned":
                done += 1
        return done

    async def provision(self, prg: str, ts: int) -> ProgressionRecord:
        record = await self._store.progression(prg)
        if record is None:
            raise KeyError(f"unknown progression {prg!r}")
        if record.status != "provisioning":
            return record
        assert record.to_rsv_id is not None
        acc = await self._store.assign_account(prg, ts)
        entry_id = await self._gateway.publish(
            OpenAccount(acc, record.to_rsv_id, record.size_cents, ts)
        )
        await self._store.provision_attempted(prg, acc, entry_id, ts)  # before the wait
        outcome = await self._gateway.outcome(entry_id, self._wait_s)
        if outcome.seq is not None:
            if opened(outcome.events, acc):
                await self._store.mark_provisioned(prg, ts, outcome.seq)
            else:
                await self._store.mark_failed(prg, refusal_reason(outcome.events), ts, outcome.seq)
        updated = await self._store.progression(prg)
        assert updated is not None
        return updated

    async def decide(
        self, prg: str, approve: bool, subject: str, email: str | None, reason: str, ts: int
    ) -> ProgressionRecord:
        if approve and self._cases is not None:
            record = await self._store.progression(prg)
            if record is not None and await self._cases.has_open_case(record.cif):
                raise DecisionError("open_case", "the customer has a case under review")
        return await self._store.decide(prg, approve, subject, email, reason, ts)
