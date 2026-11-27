"""The policy in force, and the admin changes to it.

`Settings.current()` is the policy file with the applied admin changes
(`apply_changes`); the API and checkout read it per request, cached until a
change is applied. Who may call the change methods is decided by the API:
only the `admin` role.

A stage-limits change, in order:
1. the limits are checked against the stage (a new rule set must be valid);
2. the change is recorded (its number names the new version);
3. AddRuleSet goes to the engine; once journaled (or found already
   journaled by an earlier attempt) the change is marked applied;
4. no answer in time leaves it waiting: `publish_pending` retries, and
   until then the previous limits stay in force.

Maker-checker [staff spec 5a-2; outline M07, M25; rule AC00]: a change
is first a proposal (`propose`); it takes effect only when a second
person approves it (`approve`: never its maker, within
`proposal_expiry_hours`), and is then recorded in the maker's name with
the approver's. Who may propose and approve each kind is
`propfirm.settings.authority`: stage limits and the stand-in are owner
level (AC01), country gating admin level. Another approver may reject it;
the maker may withdraw it. The limits are checked when proposed and again
when approved.
"""

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from propfirm.journal.inputs import AddRuleSet, Input
from propfirm.policy import Policy
from propfirm.rules.products import RuleSet
from propfirm.service.gateway import Outcome
from propfirm.settings.authority import Refused, check_approver, check_proposer
from propfirm.settings.methods import (
    MethodSecret,
    PaymentMethod,
    methods_in_force,
    secrets_in_force,
)
from propfirm.settings.notices import Notices
from propfirm.settings.overlay import (
    CountryGating,
    PageVersion,
    Setting,
    SettingChange,
    StageLimits,
    StandIn,
    apply_changes,
    limits_rule_set,
    notices,
    policy_pages,
    site_mode,
    site_sections,
    stand_in_in_force,
)
from propfirm.settings.site import SiteMode, SiteSection
from propfirm.settings.store import ChangeStatus, Proposal, ProposalDecided
from propfirm.staff.model import Tier

log = logging.getLogger(__name__)


class Records(Protocol):
    async def record(
        self,
        setting: Setting,
        author: str,
        author_email: str | None,
        reason: str,
        ts: int,
        *,
        applied: bool,
    ) -> SettingChange: ...

    async def mark_applied(self, change_id: int, journal_seq: int | None, ts: int) -> None: ...

    async def version(self) -> int: ...

    async def applied_changes(self) -> Sequence[SettingChange]: ...

    async def unapplied_changes(self) -> Sequence[SettingChange]: ...

    async def history(self, limit: int = 100) -> Sequence[ChangeStatus]: ...


class Proposals(Protocol):
    async def propose(
        self, setting: Setting, author: str, author_email: str | None, reason: str, ts: int
    ) -> Proposal: ...

    async def proposal(self, pid: int) -> Proposal | None: ...

    async def proposals(self, open_only: bool = True, limit: int = 100) -> Sequence[Proposal]: ...

    async def decide(self, pid: int, decision: str, by: str, reason: str, ts: int) -> None: ...

    async def approve(
        self, proposal: Proposal, approver: str, reason: str, ts: int, *, applied: bool
    ) -> SettingChange: ...


class Acceptances(Protocol):
    """Who accepted which version of which policy page (rule AC10)."""

    async def accept(self, subject: str, slug: str, version: int, ts: int) -> None: ...

    async def accepted(self, subject: str) -> set[tuple[str, int]]: ...


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...


class SettingsError(Exception):
    """A change refused (the code says why: invalid, unknown, engine_refused;
    for proposals: four_eyes, decided, expired, not_maker)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Settings:
    def __init__(
        self,
        base: Policy,
        records: Records,
        gateway: Submitter,
        wait_s: float = 5.0,
        proposals: Proposals | None = None,
        acceptances: Acceptances | None = None,
    ) -> None:
        self._base = base
        self._acceptances = acceptances
        self._records = records
        self._proposals = proposals
        self._gateway = gateway
        self._wait_s = wait_s
        self._cached: tuple[int, Policy] | None = None

    @property
    def base(self) -> Policy:
        """The policy file as loaded (the starting values)."""
        return self._base

    async def current(self) -> Policy:
        version = await self._records.version()
        if self._cached is None or self._cached[0] != version:
            policy = apply_changes(self._base, await self._records.applied_changes())
            self._cached = (version, policy)
        return self._cached[1]

    async def version(self) -> int:
        """The settings version now in force: the number of the last
        recorded change (0: the policy file as shipped)."""
        return await self._records.version()

    async def history(self, limit: int = 100) -> Sequence[ChangeStatus]:
        return await self._records.history(limit)

    # --- policy pages (rule AC10) ---------------------------------------------------

    async def pages(self) -> dict[str, list[PageVersion]]:
        """Every published version of every policy page, by slug."""
        return policy_pages(await self._records.applied_changes())

    async def latest_pages(self) -> list[PageVersion]:
        return [versions[-1] for _, versions in sorted((await self.pages()).items())]

    def _accepts(self) -> Acceptances:
        if self._acceptances is None:
            raise SettingsError("unknown", "policy acceptances are not available")
        return self._acceptances

    async def pending_pages(self, subject: str) -> list[PageVersion]:
        """The latest version of each page that `subject` has not accepted:
        a new version asks again."""
        latest = await self.latest_pages()
        if not latest:
            return []
        done = await self._accepts().accepted(subject)
        return [p for p in latest if (p.slug, p.version) not in done]

    async def accept_page(self, subject: str, slug: str, version: int, ts: int) -> PageVersion:
        """Record acceptance of the page's latest version (only the latest)."""
        versions = (await self.pages()).get(slug)
        if not versions:
            raise SettingsError("unknown", f"no policy page {slug!r}")
        if versions[-1].version != version:
            raise SettingsError("stale", f"version {versions[-1].version} is the one in force")
        await self._accepts().accept(subject, slug, version, ts)
        return versions[-1]

    async def site(self) -> dict[str, SiteSection]:
        """The website text approved for each section (rule AC07)."""
        return site_sections(await self._records.applied_changes())

    async def notices(self) -> Notices:
        """Notification templates in force (rule AC12)."""
        return notices(await self._records.applied_changes())

    async def mode(self) -> SiteMode:
        """Maintenance and registration in force (rule AC17)."""
        return site_mode(await self._records.applied_changes())

    async def methods(self) -> dict[str, PaymentMethod]:
        """Payment and withdrawal methods in force (rule AC13)."""
        return methods_in_force(
            c.setting for c in sorted(await self._records.applied_changes(), key=lambda c: c.id)
        )

    async def secrets(self) -> dict[tuple[str, str], MethodSecret]:
        """Each method's secrets in force, encrypted (rule AC14)."""
        return secrets_in_force(
            c.setting for c in sorted(await self._records.applied_changes(), key=lambda c: c.id)
        )

    async def stand_in(self, now: int) -> StandIn | None:
        """The owners' stand-in approver in force at `now`, if any."""
        return stand_in_in_force(await self._records.applied_changes(), now)

    async def set_country_gating(
        self, author: str, email: str | None, setting: CountryGating, reason: str, ts: int
    ) -> ChangeStatus:
        """Record a gating change; it is in force at once (no engine input)."""
        change = await self._records.record(setting, author, email, reason, ts, applied=True)
        return ChangeStatus(change, ts, None)

    async def set_stage_limits(
        self, author: str, email: str | None, setting: StageLimits, reason: str, ts: int
    ) -> tuple[ChangeStatus, RuleSet]:
        """Record and publish new limits; return the change (applied, or
        pending while the engine has not answered) and its rule set."""
        base = self._base_rule_set(setting)
        if base is None:
            raise SettingsError(
                "unknown", f"no stage {setting.stage.value} in product {setting.product_id!r}"
            )
        try:
            limits_rule_set(base, setting, 0)
        except (TypeError, ValueError) as exc:
            raise SettingsError("invalid", str(exc)) from exc
        change = await self._records.record(setting, author, email, reason, ts, applied=False)
        rule_set = limits_rule_set(base, setting, change.id)
        seq = await self._publish(change.id, rule_set, ts)
        return ChangeStatus(change, None if seq is None else ts, seq), rule_set

    # --- maker-checker ----------------------------------------------------------------

    def _store(self) -> Proposals:
        if self._proposals is None:
            raise SettingsError("unknown", "proposals are not available")
        return self._proposals

    def _check(self, setting: Setting) -> None:
        """A proposal must be a change that could take effect."""
        if isinstance(setting, StageLimits):
            base = self._base_rule_set(setting)
            if base is None:
                raise SettingsError(
                    "unknown", f"no stage {setting.stage.value} in product {setting.product_id!r}"
                )
            try:
                limits_rule_set(base, setting, 0)
            except (TypeError, ValueError) as exc:
                raise SettingsError("invalid", str(exc)) from exc

    async def propose(
        self,
        author: str,
        email: str | None,
        setting: Setting,
        reason: str,
        ts: int,
        *,
        tier: Tier | None,
    ) -> Proposal:
        """`tier`: the maker's firm-wide tier (owner-level changes need T5)."""
        try:
            check_proposer(setting, tier)
        except Refused as exc:
            raise SettingsError(exc.code, str(exc)) from exc
        self._check(setting)
        return await self._store().propose(setting, author, email, reason, ts)

    async def proposals(self, open_only: bool = True, limit: int = 100) -> Sequence[Proposal]:
        return await self._store().proposals(open_only, limit)

    async def _open(self, pid: int) -> Proposal:
        found = await self._store().proposal(pid)
        if found is None:
            raise SettingsError("unknown", f"no proposal {pid}")
        if found.decision is not None:
            raise SettingsError("decided", f"proposal {pid} is {found.decision}")
        return found

    async def approve(
        self, pid: int, approver: str, reason: str, ts: int, expiry_ms: int, *, tier: Tier | None
    ) -> tuple[ChangeStatus, RuleSet | None]:
        """The second person's approval: the change takes effect (gating and
        the stand-in at once; limits when the engine has the new rule set).
        `tier`: the approver's firm-wide tier."""
        proposal = await self._open(pid)
        try:
            check_approver(
                proposal.setting, proposal.author, approver, tier, await self.stand_in(ts)
            )
        except Refused as exc:
            raise SettingsError(exc.code, str(exc)) from exc
        if expiry_ms and ts - proposal.ts > expiry_ms:
            raise SettingsError("expired", f"proposal {pid} has expired; propose it again")
        self._check(proposal.setting)
        at_once = not isinstance(proposal.setting, StageLimits)  # limits wait for the engine
        try:
            change = await self._store().approve(proposal, approver, reason, ts, applied=at_once)
        except ProposalDecided as exc:
            raise SettingsError("decided", str(exc)) from exc
        if at_once:
            return ChangeStatus(change, ts, None), None
        setting = proposal.setting
        assert isinstance(setting, StageLimits)
        base = self._base_rule_set(setting)
        assert base is not None
        rule_set = limits_rule_set(base, setting, change.id)
        seq = await self._publish(change.id, rule_set, ts)
        return ChangeStatus(change, None if seq is None else ts, seq), rule_set

    async def reject(
        self, pid: int, by: str, reason: str, ts: int, *, tier: Tier | None
    ) -> Proposal:
        """Another person who could approve it rejects it."""
        proposal = await self._open(pid)
        if by == proposal.author:
            raise SettingsError("four_eyes", "the maker withdraws, another admin rejects")
        try:
            check_approver(proposal.setting, proposal.author, by, tier, await self.stand_in(ts))
        except Refused as exc:
            raise SettingsError(exc.code, str(exc)) from exc
        return await self._decide(proposal, "rejected", by, reason, ts)

    async def withdraw(self, pid: int, by: str, reason: str, ts: int) -> Proposal:
        proposal = await self._open(pid)
        if by != proposal.author:
            raise SettingsError("not_maker", "only the maker withdraws a proposal")
        return await self._decide(proposal, "withdrawn", by, reason, ts)

    async def _decide(
        self, proposal: Proposal, decision: str, by: str, reason: str, ts: int
    ) -> Proposal:
        try:
            await self._store().decide(proposal.id, decision, by, reason, ts)
        except ProposalDecided as exc:
            raise SettingsError("decided", str(exc)) from exc
        found = await self._store().proposal(proposal.id)
        assert found is not None
        return found

    async def publish_pending(self, ts: int) -> int:
        """Retry the stage-limits changes not yet applied; return how many
        are applied now."""
        done = 0
        for change in await self._records.unapplied_changes():
            setting = change.setting
            if not isinstance(setting, StageLimits):
                continue
            base = self._base_rule_set(setting)
            if base is None:
                continue  # the file no longer has this stage: no effect
            rule_set = limits_rule_set(base, setting, change.id)
            try:
                done += await self._publish(change.id, rule_set, ts) is not None
            except SettingsError:
                log.exception("setting change %d not published", change.id)
        return done

    def _base_rule_set(self, setting: StageLimits) -> RuleSet | None:
        try:
            product = self._base.product(setting.product_id)
        except KeyError:
            return None
        return product.rule_set(setting.stage)

    async def _publish(self, change_id: int, rule_set: RuleSet, ts: int) -> int | None:
        """The journal record that confirmed the rule set (RuleSetAdded, or
        the duplicate refusal of a retry); None: not journaled in time."""
        outcome = await self._gateway.submit(AddRuleSet(rule_set, ts), self._wait_s)
        if outcome.seq is None:
            return None
        if not _published(outcome.events, rule_set.rsv_id):
            raise SettingsError("engine_refused", _reason(outcome.events))
        await self._records.mark_applied(change_id, outcome.seq, ts)
        return outcome.seq


def _published(events: Sequence[Any], rsv_id: str) -> bool:
    for event in events:
        if event.get("type") == "RuleSetAdded" and event.get("rsv_id") == rsv_id:
            return True
        if (
            event.get("type") == "InputRejected"
            and event.get("reason") == "duplicate_rule_set"
            and event.get("ref") == rsv_id
        ):
            return True  # journaled by an earlier attempt; the id is this change's own
    return False


def _reason(events: Sequence[Any]) -> str:
    for event in events:
        if "reason" in event:
            return str(event["reason"])
    return "not_published"
