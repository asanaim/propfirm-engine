"""Admin-owned settings: the overlay on the policy file, the settings
service, and the admin-only API views."""

import asyncio
import copy
import dataclasses
import json
import tomllib
from collections.abc import Sequence
from fractions import Fraction
from typing import Any

import httpx
import pytest

from propfirm.api.app import Deps, create_app
from propfirm.journal.inputs import AddRuleSet, Input
from propfirm.journal.machine import CODEC, Machine
from propfirm.policy import DEFAULT_POLICY, PolicyError, load_policy, parse_policy
from propfirm.rules.products import MaxLossKind, Stage
from propfirm.service.gateway import Outcome
from propfirm.service.setup import policy_changes, setup_from_policy
from propfirm.settings.authority import Refused, check_approver
from propfirm.settings.overlay import (
    CountryGating,
    PolicyPage,
    Setting,
    SettingChange,
    StandIn,
    apply_changes,
    from_json,
    limits_rule_set,
    stage_limits,
    stand_in_in_force,
    to_json,
)
from propfirm.settings.service import Settings, SettingsError
from propfirm.settings.site import SiteSection, rendered
from propfirm.settings.store import ChangeStatus, Proposal, ProposalDecided
from propfirm.staff.model import ROLES, Access, Tier, legacy_access
from tests.auth_rig import FakeCustomers, bearer, verifier

BASE = load_policy()
T0 = 1_790_000_000_000
REALM = DEFAULT_POLICY.parent / "keycloak" / "propfirm-realm.json"


def change(n: int, setting: Setting) -> SettingChange:
    return SettingChange(n, setting, "admin-1", "admin-1@example.com", "test", T0 + n)


ONE_STEP_S1 = stage_limits("one-step", "s1", "8", "3", "5", "static")
GATING_ON = CountryGating(True, frozenset({"BD"}))


# --- the overlay ----------------------------------------------------------------------


def test_no_changes_is_the_file() -> None:
    assert apply_changes(BASE, []) == BASE


def test_the_latest_change_of_each_setting_wins() -> None:
    later = stage_limits("one-step", "s1", "9", "4", "6", "trailing")
    gating_off = CountryGating(False, frozenset({"IN"}))
    policy = apply_changes(
        BASE,
        [change(4, gating_off), change(1, GATING_ON), change(2, ONE_STEP_S1), change(3, later)],
    )
    assert policy.access.country_gating is False
    assert policy.access.blocked_countries == {"IN"}
    assert policy.access.min_age == BASE.access.min_age  # not an admin setting
    s1 = policy.product("one-step").rule_set(Stage.S1)
    assert s1 is not None and s1.rsv_id == "one-step-s1-v2-a3"
    assert (s1.profit_target, s1.daily_loss, s1.max_loss) == (
        Fraction(9, 100),
        Fraction(4, 100),
        Fraction(6, 100),
    )
    assert s1.max_loss_kind is MaxLossKind.TRAILING
    base_s1 = BASE.product("one-step").rule_set(Stage.S1)
    assert base_s1 is not None
    # the stage's other numbers still come from the file
    assert (s1.min_trading_days, s1.consistency, s1.sizes_cents) == (
        base_s1.min_trading_days,
        base_s1.consistency,
        base_s1.sizes_cents,
    )
    others = [r for p in policy.products for r in p.rule_sets if r.rsv_id != s1.rsv_id]
    assert others == [r for r in BASE.rule_sets() if r.rsv_id != "one-step-s1-v2"]


def test_a_file_version_of_a_changed_stage_keeps_the_admin_limits() -> None:
    raw: dict[str, Any] = tomllib.loads(DEFAULT_POLICY.read_text("utf-8"))
    data = copy.deepcopy(raw)
    one_step = next(p for p in data["products"] if p["id"] == "one-step")
    one_step["stages"][0].update(version=3, min_trading_days=5)
    policy = apply_changes(parse_policy(data), [change(7, ONE_STEP_S1)])
    s1 = policy.product("one-step").rule_set(Stage.S1)
    assert s1 is not None and s1.rsv_id == "one-step-s1-v3-a7"
    assert (s1.min_trading_days, s1.profit_target) == (5, Fraction(8, 100))


def test_a_change_for_a_stage_the_file_no_longer_has_has_no_effect() -> None:
    gone = stage_limits("no-such", "s1", "8", "3", "5", "static")
    assert apply_changes(BASE, [change(1, gone)]) == BASE


def test_invalid_limits_for_a_stage_are_refused() -> None:
    funded = BASE.product("one-step").rule_set(Stage.FUNDED)
    s1 = BASE.product("one-step").rule_set(Stage.S1)
    assert funded is not None and s1 is not None
    with pytest.raises(ValueError, match="funded"):
        limits_rule_set(funded, stage_limits("one-step", "funded", "8", "3", "5", "static"), 1)
    # 0.0001 % of a $25,000 account is 2.5 cents: not whole cents
    with pytest.raises(ValueError, match="whole cents"):
        limits_rule_set(s1, stage_limits("one-step", "s1", "8", "0.0001", "5", "static"), 1)
    with pytest.raises(ValueError, match="must not exceed max_loss"):
        limits_rule_set(s1, stage_limits("one-step", "s1", "8", "6", "5", "static"), 1)


def test_stored_form_round_trips_exactly() -> None:
    limits = stage_limits("one-step", "s1", "8.25", "3", "5.5", "trailing")
    assert limits.profit_target == Fraction(33, 400) and limits.max_loss == Fraction(11, 200)
    funded = stage_limits("instant", "funded", None, "3", "5", "trailing")
    for setting in (GATING_ON, limits, funded):
        kind, value = to_json(setting)
        assert from_json(kind, json.loads(json.dumps(value))) == setting
    assert to_json(limits)[1]["profit_target_percent"] == "8.25"


@pytest.mark.parametrize(
    "args",
    [
        ("one-step", "s3", "8", "3", "5", "static"),
        ("one-step", "s1", "8", "3", "5", "rolling"),
        ("one-step", "s1", "8.0.1", "3", "5", "static"),
        ("one-step", "s1", "8", "-3", "5", "static"),
        ("one-step", "s1", "8", "3", "", "static"),
    ],
)
def test_malformed_limits_are_refused(args: tuple[Any, ...]) -> None:
    with pytest.raises(PolicyError):
        stage_limits(*args)


def test_an_engine_started_after_a_change_publishes_only_what_it_lacks() -> None:
    machine = Machine(CODEC.encode(setup_from_policy(BASE)), {1: bytes(32)})
    in_force = apply_changes(BASE, [change(7, ONE_STEP_S1)])
    (item,) = policy_changes(in_force, machine, T0)
    assert isinstance(item, AddRuleSet) and item.rule_set.rsv_id == "one-step-s1-v2-a7"
    machine.use_price_key(1)
    machine.apply("1-0", item)
    assert policy_changes(in_force, machine, T0) == []


# --- the settings service ---------------------------------------------------------------


class MemoryRecords:
    def __init__(self) -> None:
        self.changes: list[SettingChange] = []
        self.applied: dict[int, tuple[int | None, int]] = {}

    async def record(
        self,
        setting: Setting,
        author: str,
        author_email: str | None,
        reason: str,
        ts: int,
        *,
        applied: bool,
    ) -> SettingChange:
        made = SettingChange(len(self.changes) + 1, setting, author, author_email, reason, ts)
        self.changes.append(made)
        if applied:
            self.applied[made.id] = (None, ts)
        return made

    async def mark_applied(self, change_id: int, journal_seq: int | None, ts: int) -> None:
        self.applied.setdefault(change_id, (journal_seq, ts))

    async def version(self) -> int:
        return len(self.applied)

    async def applied_changes(self) -> Sequence[SettingChange]:
        return [c for c in self.changes if c.id in self.applied]

    async def unapplied_changes(self) -> Sequence[SettingChange]:
        return [c for c in self.changes if c.id not in self.applied]

    async def history(self, limit: int = 100) -> Sequence[ChangeStatus]:
        out = []
        for c in reversed(self.changes[-limit:]):
            seq, at = self.applied.get(c.id, (None, None))
            out.append(ChangeStatus(c, at, seq))
        return out


class MemoryProposals:
    """Maker-checker proposals, recording approved changes in `records`."""

    def __init__(self, records: MemoryRecords) -> None:
        self.records = records
        self.items: dict[int, Proposal] = {}

    async def propose(
        self, setting: Setting, author: str, author_email: str | None, reason: str, ts: int
    ) -> Proposal:
        pid = len(self.items) + 1
        self.items[pid] = Proposal(
            pid, setting, author, author_email, reason, ts, None, None, None, None, None
        )
        return self.items[pid]

    async def proposal(self, pid: int) -> Proposal | None:
        return self.items.get(pid)

    async def proposals(self, open_only: bool = True, limit: int = 100) -> Sequence[Proposal]:
        found = [p for p in self.items.values() if not open_only or p.decision is None]
        return sorted(found, key=lambda p: -p.id)[:limit]

    async def decide(self, pid: int, decision: str, by: str, reason: str, ts: int) -> None:
        p = self.items[pid]
        if p.decision is not None:
            raise ProposalDecided("decided")
        self.items[pid] = dataclasses.replace(
            p, decision=decision, decided_by=by, decision_reason=reason, decided_at=ts
        )

    async def approve(
        self, proposal: Proposal, approver: str, reason: str, ts: int, *, applied: bool
    ) -> SettingChange:
        await self.decide(proposal.id, "approved", approver, reason, ts)
        made = SettingChange(
            len(self.records.changes) + 1,
            proposal.setting,
            proposal.author,
            proposal.author_email,
            proposal.reason,
            ts,
            approver,
            proposal.id,
        )
        self.records.changes.append(made)
        if applied:
            self.records.applied[made.id] = (None, ts)
        self.items[proposal.id] = dataclasses.replace(self.items[proposal.id], change_id=made.id)
        return made


class Engine:
    """Answers AddRuleSet like the engine; `down` leaves it unanswered."""

    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.known: set[str] = set()
        self.sent: list[Input] = []
        self.seq = 100

    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome:
        self.sent.append(item)
        if self.down:
            return Outcome(f"{len(self.sent)}-0", None, [])
        assert isinstance(item, AddRuleSet)
        self.seq += 1
        rsv_id = item.rule_set.rsv_id
        if rsv_id in self.known:
            event = {"type": "InputRejected", "reason": "duplicate_rule_set", "ref": rsv_id}
        else:
            self.known.add(rsv_id)
            event = {"type": "RuleSetAdded", "rsv_id": rsv_id}
        return Outcome(f"{len(self.sent)}-0", self.seq, [event])

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome:
        return Outcome(entry_id, None, [])


def test_new_limits_are_in_force_once_the_engine_has_them() -> None:
    async def scenario() -> None:
        engine = Engine()
        settings = Settings(BASE, MemoryRecords(), engine)
        assert await settings.current() == BASE
        status, rule_set = await settings.set_stage_limits(
            "admin-1", None, ONE_STEP_S1, "launch offer", T0
        )
        assert rule_set.rsv_id == "one-step-s1-v2-a1"
        assert (status.applied_at, status.journal_seq) == (T0, 101)
        s1 = (await settings.current()).product("one-step").rule_set(Stage.S1)
        assert s1 == rule_set
        assert engine.sent == [AddRuleSet(rule_set, T0)]

    asyncio.run(scenario())


def test_while_the_engine_is_down_the_previous_limits_stay_in_force() -> None:
    async def scenario() -> None:
        engine = Engine(down=True)
        records = MemoryRecords()
        settings = Settings(BASE, records, engine)
        status, rule_set = await settings.set_stage_limits("admin-1", None, ONE_STEP_S1, "r", T0)
        assert status.applied_at is None and status.journal_seq is None
        assert await settings.current() == BASE  # checkout keeps selling the old version
        assert await settings.publish_pending(T0 + 1) == 0
        engine.down = False
        engine.known.add(rule_set.rsv_id)  # journaled by an earlier attempt after all
        assert await settings.publish_pending(T0 + 2) == 1
        assert await settings.publish_pending(T0 + 3) == 0  # nothing left
        current = (await settings.current()).product("one-step").rule_set(Stage.S1)
        assert current == rule_set
        assert records.applied[1] == (101, T0 + 2)

    asyncio.run(scenario())


def test_limits_the_stage_cannot_take_are_refused_before_recording() -> None:
    async def scenario() -> None:
        records = MemoryRecords()
        settings = Settings(BASE, records, Engine())
        funded = stage_limits("one-step", "funded", "8", "3", "5", "static")
        with pytest.raises(SettingsError) as invalid:
            await settings.set_stage_limits("admin-1", None, funded, "r", T0)
        assert invalid.value.code == "invalid"
        unknown = stage_limits("instant", "s1", "8", "3", "5", "static")  # instant has no s1
        with pytest.raises(SettingsError) as missing:
            await settings.set_stage_limits("admin-1", None, unknown, "r", T0)
        assert missing.value.code == "unknown"
        assert records.changes == []

    asyncio.run(scenario())


def test_an_engine_refusal_is_reported_and_leaves_the_change_waiting() -> None:
    class Refusing(Engine):
        async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome:
            return Outcome("1-0", 5, [{"type": "InputRejected", "reason": "malformed"}])

    async def scenario() -> None:
        records = MemoryRecords()
        settings = Settings(BASE, records, Refusing())
        with pytest.raises(SettingsError) as refused:
            await settings.set_stage_limits("admin-1", None, ONE_STEP_S1, "r", T0)
        assert (refused.value.code, str(refused.value)) == ("engine_refused", "malformed")
        assert len(records.changes) == 1 and records.applied == {}
        assert await settings.publish_pending(T0) == 0  # logged, not raised
        assert await settings.current() == BASE

    asyncio.run(scenario())


def test_country_gating_is_in_force_at_once() -> None:
    async def scenario() -> None:
        settings = Settings(BASE, MemoryRecords(), Engine(down=True))
        status = await settings.set_country_gating("admin-1", None, GATING_ON, "C8 advice", T0)
        assert status.applied_at == T0
        access = (await settings.current()).access
        assert access.country_gating and access.blocked_countries == {"BD"}

    asyncio.run(scenario())


# --- admin-only API views --------------------------------------------------------------


def api(
    method: str,
    url: str,
    body: object = None,
    *,
    roles: tuple[str, ...] = ("admin",),
    engine: Engine | None = None,
    records: MemoryRecords | None = None,
    signed_in: bool = True,
    proposals: MemoryProposals | None = None,
    user: str = "admin-1",
    tiers: dict[str, Tier] | None = None,
    acceptances: MemoryAcceptances | None = None,
    customers: FakeCustomers | None = None,
) -> httpx.Response:
    """Without `proposals`, the policy runs without maker-checker (the
    direct path); with them, as shipped (a second person approves).
    `tiers`: firm-wide tiers from a staff directory (owners, admins); others
    keep their Keycloak roles."""
    base = BASE
    if proposals is None:
        staff = dataclasses.replace(BASE.staff, settings_need_second_admin=False)
        base = dataclasses.replace(BASE, staff=staff)
    records = records if records is not None else MemoryRecords()
    settings = Settings(
        base,
        records,
        engine or Engine(),
        proposals=proposals,
        acceptances=acceptances if acceptances is not None else MemoryAcceptances(),
    )
    deps = Deps(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        engine or Engine(),
        verifier(),
        customers or FakeCustomers(),
        policy=settings,
        settings=settings,
        clock=lambda: T0,
        staff=None if tiers is None else TierStaff(tiers),  # type: ignore[arg-type]
    )
    app = create_app(deps)
    headers = bearer(user, roles) if signed_in else {}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await client.request(method, url, json=body, headers=headers)

    return asyncio.run(go())


class MemoryAcceptances:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, int], int] = {}

    async def accept(self, subject: str, slug: str, version: int, ts: int) -> None:
        self.rows.setdefault((subject, slug, version), ts)

    async def accepted(self, subject: str) -> set[tuple[str, int]]:
        return {(slug, v) for (who, slug, v) in self.rows if who == subject}


class TierStaff:
    """A staff directory that knows only firm-wide tiers."""

    def __init__(self, tiers: dict[str, Tier]) -> None:
        self._tiers = tiers

    async def access(self, actor: Any, now: int) -> Access:
        tier = self._tiers.get(actor.subject)
        if tier is None:
            return legacy_access(actor.subject, frozenset(actor.roles))
        return Access(actor.subject, "directory", tier, frozenset(ROLES))


OWNERS = {"owner-a": Tier.OWNER, "owner-b": Tier.OWNER, "admin-1": Tier.ADMIN,
          "admin-2": Tier.ADMIN}  # fmt: skip

GATING = {"country_gating": True, "blocked_countries": ["BD", "IN"], "reason": "counsel C8"}
LIMITS = {
    "profit_target_percent": "8",
    "daily_loss_percent": "3",
    "max_loss_percent": "5",
    "max_loss_kind": "static",
    "reason": "launch offer",
}
LIMITS_URL = "/admin/products/one-step/stages/s1/limits"
ADMIN_CALLS = [
    ("GET", "/admin/access", None),
    ("PUT", "/admin/access/country-gating", GATING),
    ("PUT", LIMITS_URL, LIMITS),
    ("GET", "/admin/settings/changes", None),
]


@pytest.mark.parametrize(("method", "url", "body"), ADMIN_CALLS)
@pytest.mark.parametrize("roles", [("trader",), ("staff",), ("trader", "staff")])
def test_settings_are_admin_only(
    method: str, url: str, body: object, roles: tuple[str, ...]
) -> None:
    records = MemoryRecords()
    assert api(method, url, body, roles=roles, records=records).status_code == 403
    assert api(method, url, body, signed_in=False, records=records).status_code == 401
    assert records.changes == []


def test_admin_view_shows_the_gating_switch() -> None:
    assert api("GET", "/admin/access").json() == {
        "country_gating": False,
        "blocked_countries": ["BD", "IN"],
        "min_age": 18,
    }


def test_admin_changes_gating_and_the_history_shows_who_and_why() -> None:
    records = MemoryRecords()
    changed = api("PUT", "/admin/access/country-gating", GATING, records=records)
    assert changed.status_code == 200, changed.text
    assert changed.json() == {
        "id": 1,
        "kind": "country_gating",
        "value": {"country_gating": True, "blocked_countries": ["BD", "IN"]},
        "author": "admin-1",
        "author_email": "admin-1@example.com",
        "reason": "counsel C8",
        "created_at": T0,
        "status": "applied",
        "applied_at": T0,
        "journal_seq": None,
        "approved_by": None,
        "proposal_id": None,
    }
    assert api("GET", "/admin/access", records=records).json()["country_gating"] is True
    (entry,) = api("GET", "/admin/settings/changes", records=records).json()
    assert entry["id"] == 1 and entry["reason"] == "counsel C8"


def test_admin_changes_stage_limits() -> None:
    records = MemoryRecords()
    changed = api("PUT", LIMITS_URL, LIMITS, records=records, tiers=OWNERS, user="owner-a")
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["status"], body["rsv_id"]) == ("applied", "one-step-s1-v2-a1")
    assert body["change"]["value"] == {
        "product_id": "one-step",
        "stage": "s1",
        "profit_target_percent": "8",
        "daily_loss_percent": "3",
        "max_loss_percent": "5",
        "max_loss_kind": "static",
    }
    assert body["change"]["journal_seq"] == 101
    public = api("GET", "/catalogue", records=records, signed_in=False).json()
    public_s1 = next(p for p in public if p["id"] == "one-step")["stages"][0]
    assert public_s1["rsv_id"] == "one-step-s1-v2-a1"
    assert (public_s1["profit_target_percent"], public_s1["max_loss_percent"]) == ("8", "5")


def test_stage_limits_while_the_engine_is_down_answer_202() -> None:
    records = MemoryRecords()
    pending = api("PUT", LIMITS_URL, LIMITS, records=records, engine=Engine(down=True),
                  tiers=OWNERS, user="owner-a")  # fmt: skip
    assert pending.status_code == 202
    assert pending.json()["status"] == "pending" and pending.json()["change"]["status"] == "pending"
    (entry,) = api("GET", "/admin/settings/changes", records=records).json()
    assert entry["status"] == "pending"


@pytest.mark.parametrize(
    ("url", "body", "status"),
    [
        (LIMITS_URL, {**LIMITS, "max_loss_percent": 5}, 422),  # a number, not text
        (LIMITS_URL, {**LIMITS, "max_loss_percent": "5.0e1"}, 422),
        (LIMITS_URL, {**LIMITS, "max_loss_percent": "100"}, 422),  # not below 100 %
        (LIMITS_URL, {**LIMITS, "daily_loss_percent": "6"}, 422),  # above the 5 % drawdown
        (LIMITS_URL, {**LIMITS, "max_loss_kind": "rolling"}, 422),
        (LIMITS_URL, {**LIMITS, "reason": "  "}, 422),
        (LIMITS_URL, {k: v for k, v in LIMITS.items() if k != "reason"}, 422),
        (LIMITS_URL, {**LIMITS, "min_trading_days": 2}, 422),  # not an admin setting here
        ("/admin/products/one-step/stages/funded/limits", LIMITS, 422),  # funded: no target
        ("/admin/products/instant/stages/s1/limits", LIMITS, 404),  # instant has no s1
        ("/admin/products/no-such/stages/s1/limits", LIMITS, 404),
        ("/admin/products/one-step/stages/s9/limits", LIMITS, 422),
        ("/admin/access/country-gating", {**GATING, "blocked_countries": ["bd"]}, 422),
        ("/admin/access/country-gating", {**GATING, "country_gating": "yes"}, 422),
        ("/admin/access/country-gating", {**GATING, "country_gating": 1}, 422),
    ],
)
def test_admin_changes_the_api_refuses(url: str, body: Any, status: int) -> None:
    records = MemoryRecords()
    assert (
        api("PUT", url, body, records=records, tiers=OWNERS, user="owner-a").status_code == status
    )
    assert records.changes == []


def test_funded_limits_take_no_profit_target() -> None:
    funded = {**LIMITS, "profit_target_percent": None}
    changed = api("PUT", "/admin/products/one-step/stages/funded/limits", funded, tiers=OWNERS,
                  user="owner-a")  # fmt: skip
    assert changed.status_code == 200, changed.text
    assert changed.json()["rsv_id"] == "one-step-funded-v1-a1"


# --- maker-checker: a second admin approves every settings change ----------------------


def test_shipped_policy_needs_a_second_admin_for_settings() -> None:
    assert BASE.staff.settings_need_second_admin is True
    assert BASE.staff.proposal_expiry_hours == 72


def test_a_gating_change_waits_for_a_second_admin() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    proposed = api("PUT", "/admin/access/country-gating", GATING, records=records,
                   proposals=proposals)  # fmt: skip
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["status"] == "proposed" and body["proposal"]["status"] == "open"
    assert body["proposal"]["expires_at"] == T0 + 72 * 3_600_000
    assert records.changes == []  # nothing in force yet
    assert (
        api("GET", "/admin/access", records=records, proposals=proposals).json()["country_gating"]
        is False
    )
    url = "/admin/settings/proposals/1"
    own = api("POST", f"{url}/approve", {"reason": "ok"}, records=records, proposals=proposals)
    assert (own.status_code, own.json()["detail"]["code"]) == (403, "four_eyes")
    approved = api("POST", f"{url}/approve", {"reason": "checked"}, records=records,
                   proposals=proposals, user="admin-2")  # fmt: skip
    assert approved.status_code == 200, approved.text
    change = approved.json()["change"]
    assert (change["author"], change["approved_by"], change["proposal_id"]) == (
        "admin-1",
        "admin-2",
        1,
    )
    assert (
        api("GET", "/admin/access", records=records, proposals=proposals).json()["country_gating"]
        is True
    )
    again = api("POST", f"{url}/approve", {"reason": "x"}, records=records, proposals=proposals,
                user="admin-3")  # fmt: skip
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "decided")


def test_stage_limits_are_checked_when_proposed_and_published_when_approved() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    bad = api("PUT", LIMITS_URL, {**LIMITS, "daily_loss_percent": "6"}, user="owner-a", **kw)
    assert bad.status_code == 422 and proposals.items == {}
    proposed = api("PUT", LIMITS_URL, LIMITS, user="owner-a", **kw)
    assert proposed.status_code == 202 and proposed.json()["status"] == "proposed"
    approved = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"},
                   user="owner-b", **kw)  # fmt: skip
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["rsv_id"]) == (
        "applied",
        "one-step-s1-v2-a1",
    )


def test_the_maker_withdraws_another_admin_rejects_and_staff_never_approve() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    for _ in range(2):
        api("PUT", "/admin/access/country-gating", GATING, records=records, proposals=proposals)
    kw: dict[str, Any] = {"records": records, "proposals": proposals}
    by_other = api("POST", "/admin/settings/proposals/1/withdraw", {"reason": "x"}, user="admin-2",
                   **kw)  # fmt: skip
    assert (by_other.status_code, by_other.json()["detail"]["code"]) == (403, "not_maker")
    withdrawn = api("POST", "/admin/settings/proposals/1/withdraw", {"reason": "typo"}, **kw)
    assert withdrawn.json()["status"] == "withdrawn"
    own_reject = api("POST", "/admin/settings/proposals/2/reject", {"reason": "x"}, **kw)
    assert own_reject.json()["detail"]["code"] == "four_eyes"
    staff = api("POST", "/admin/settings/proposals/2/approve", {"reason": "x"}, roles=("staff",),
                user="staff-1", **kw)  # fmt: skip
    assert staff.status_code == 403
    rejected = api("POST", "/admin/settings/proposals/2/reject", {"reason": "no"}, user="admin-2",
                   **kw)  # fmt: skip
    assert (rejected.json()["status"], rejected.json()["decided_by"]) == ("rejected", "admin-2")
    assert api("GET", "/admin/settings/proposals", **kw).json() == []
    every = api("GET", "/admin/settings/proposals?open=false", **kw).json()
    assert [p["status"] for p in every] == ["rejected", "withdrawn"]
    assert records.changes == []


def test_an_expired_proposal_is_not_approved() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    asyncio.run(proposals.propose(CountryGating(True, frozenset({"BD"})), "admin-1", None, "r",
                                  T0 - 72 * 3_600_000 - 1))  # fmt: skip
    late = api("POST", "/admin/settings/proposals/1/approve", {"reason": "x"}, records=records,
               proposals=proposals, user="admin-2")  # fmt: skip
    assert (late.status_code, late.json()["detail"]["code"]) == (409, "expired")
    listed = api("GET", "/admin/settings/proposals", records=records, proposals=proposals).json()
    assert listed[0]["status"] == "expired"


# --- the realm: each app's tokens carry only its view's roles ----------------------------


def test_realm_gives_each_app_only_its_roles() -> None:
    realm = json.loads(REALM.read_text("utf-8"))
    scopes = {m["client"]: set(m["roles"]) for m in realm["scopeMappings"]}
    assert scopes == {
        "portal": {"trader"},
        "backoffice": {"staff", "admin"},
        "dev-cli": {"trader", "staff", "admin"},
    }
    assert all(c["fullScopeAllowed"] is False for c in realm["clients"])
    users = {
        u["username"]: set(u["realmRoles"]) - {"default-roles-propfirm"}
        for u in realm["users"]
        if "serviceAccountClientId" not in u
    }
    # the leavers' service account: manage-users only, nothing else
    (service,) = [u for u in realm["users"] if "serviceAccountClientId" in u]
    assert service["clientRoles"] == {"realm-management": ["manage-users"]}
    assert "realmRoles" not in service
    assert users == {
        "trader1@example.com": set(),
        "trader2@example.com": set(),  # with an authenticator (TOTP)
        "staff1@example.com": {"staff"},
        "admin1@example.com": {"admin"},
    }


# --- rule AC00 and AC01: two people for every decision; products are owner level ----------

DAY = 86_400_000


def test_the_policy_file_cannot_switch_the_two_person_rule_off() -> None:
    with DEFAULT_POLICY.open("rb") as fh:
        raw = tomllib.load(fh)
    raw["staff"]["settings_need_second_admin"] = False
    with pytest.raises(PolicyError, match="AC00"):
        parse_policy(raw)
    assert BASE.staff.stand_in_max_days == 30


def test_only_an_owner_proposes_a_product_change_and_only_the_other_owner_approves() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    by_admin = api("PUT", LIMITS_URL, LIMITS, user="admin-1", **kw)
    assert (by_admin.status_code, by_admin.json()["detail"]["code"]) == (403, "owners_only")
    assert proposals.items == {}
    assert api("PUT", LIMITS_URL, LIMITS, user="owner-a", **kw).status_code == 202
    url = "/admin/settings/proposals/1"
    for user, code in (("owner-a", "four_eyes"), ("admin-2", "owners_only")):
        refused = api("POST", f"{url}/approve", {"reason": "x"}, user=user, **kw)
        assert (refused.status_code, refused.json()["detail"]["code"]) == (403, code)
    assert records.changes == []
    by_admin_reject = api("POST", f"{url}/reject", {"reason": "x"}, user="admin-2", **kw)
    assert by_admin_reject.json()["detail"]["code"] == "owners_only"
    approved = api("POST", f"{url}/approve", {"reason": "agreed"}, user="owner-b", **kw)
    assert approved.status_code == 200, approved.text
    change = approved.json()["change"]
    assert (change["author"], change["approved_by"]) == ("owner-a", "owner-b")


def test_country_gating_stays_with_admins_and_owners() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    assert (
        api("PUT", "/admin/access/country-gating", GATING, user="admin-1", **kw).status_code == 200
    )
    approved = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"},
                   user="owner-b", **kw)  # fmt: skip
    assert approved.status_code == 200, approved.text


def stand_in(user: str, subject: str | None, until: int, **kw: Any) -> httpx.Response:
    body = {"subject": subject, "until": until, "reason": "owner B travelling"}
    return api("PUT", "/admin/owners/stand-in", body, user=user, **kw)


def test_both_owners_appoint_a_stand_in_who_approves_a_product_change() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    # an admin cannot appoint; the stand-in must be an admin, for a limited time
    assert stand_in("admin-1", "admin-2", T0 + DAY, **kw).json()["detail"]["code"] == "owners_only"
    for subject, until in (("owner-b", T0 + DAY), ("nobody", T0 + DAY), ("admin-2", T0 + 31 * DAY),
                           ("admin-2", T0)):  # fmt: skip
        refused = stand_in("owner-a", subject, until, **kw)
        assert (refused.status_code, refused.json()["detail"]["code"]) == (422, "invalid")
    assert stand_in("owner-a", "admin-2", T0 + 7 * DAY, **kw).status_code == 200
    # the appointment itself needs the other owner; the stand-in never approves it
    own = api("POST", "/admin/settings/proposals/1/approve", {"reason": "x"}, user="admin-2", **kw)
    assert own.json()["detail"]["code"] == "owners_only"
    assert api("GET", "/admin/owners/stand-in", **kw).json()["stand_in"] is None
    ok = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-b", **kw)
    assert ok.status_code == 200, ok.text
    view = api("GET", "/admin/owners/stand-in", **kw).json()
    assert view == {"stand_in": {"subject": "admin-2", "until": T0 + 7 * DAY}, "max_days": 30}
    # owner A proposes a product change; owner B is away: the stand-in approves
    assert api("PUT", LIMITS_URL, LIMITS, user="owner-a", **kw).status_code == 202
    other_admin = api("POST", "/admin/settings/proposals/2/approve", {"reason": "x"},
                      user="admin-1", **kw)  # fmt: skip
    assert other_admin.json()["detail"]["code"] == "owners_only"
    approved = api("POST", "/admin/settings/proposals/2/approve", {"reason": "checked"},
                   user="admin-2", **kw)  # fmt: skip
    assert approved.status_code == 200, approved.text
    assert approved.json()["change"]["approved_by"] == "admin-2"


def test_a_stand_in_runs_out_and_never_approves_a_stand_in_change() -> None:
    later = SettingChange(1, StandIn("admin-2", T0 + DAY), "owner-a", None, "r", T0, "owner-b", 1)
    assert stand_in_in_force([later], T0) == StandIn("admin-2", T0 + DAY)
    assert stand_in_in_force([later], T0 + DAY) is None
    ended = SettingChange(2, StandIn(None, 0), "owner-b", None, "back", T0, "owner-a", 2)
    assert stand_in_in_force([later, ended], T0) is None
    with pytest.raises(Refused) as exc:
        check_approver(StandIn(None, 0), "owner-a", "admin-2", Tier.ADMIN,
                       StandIn("admin-2", T0 + DAY))  # fmt: skip
    assert exc.value.code == "owners_only"
    kind, value = to_json(StandIn("admin-2", 5))
    assert from_json(kind, json.loads(json.dumps(value))) == StandIn("admin-2", 5)
    assert apply_changes(BASE, [later]) == BASE  # not part of the policy


def test_admins_see_the_locked_security_switches() -> None:
    switches = api("GET", "/admin/security").json()
    assert [s["name"] for s in switches][:2] == ["payouts.require_mfa", "staff.login_needs_mfa"]
    assert all(s["locked"] for s in switches)
    staff = api("GET", "/admin/security", roles=("staff",), user="staff-1")
    assert staff.status_code == 403


# --- rule AC10: versioned policy pages, accepted again after every new version ---------

TERMS = {"title": "Terms of Service", "body": "1. Simulated accounts only.", "reason": "launch"}


def test_owners_publish_a_policy_page_and_traders_accept_each_new_version() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    accepts = MemoryAcceptances()
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS,
                          "acceptances": accepts}  # fmt: skip
    trader: dict[str, Any] = {**kw, "roles": ("trader",), "user": "u1"}
    assert api("GET", "/policies", signed_in=False, **kw).json() == []
    assert api("GET", "/me/policies/pending", **trader).json() == []
    by_admin = api("PUT", "/admin/policies/terms", TERMS, user="admin-1", **kw)
    assert by_admin.json()["detail"]["code"] == "owners_only"
    assert api("PUT", "/admin/policies/terms", TERMS, user="owner-a", **kw).status_code == 200
    assert api("GET", "/policies", signed_in=False, **kw).json() == []  # not yet approved
    ok = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-b", **kw)
    assert ok.status_code == 200, ok.text
    listed = api("GET", "/policies", signed_in=False, **kw).json()
    assert listed == [{"slug": "terms", "version": 1, "title": "Terms of Service",
                       "published_at": T0}]  # fmt: skip
    page = api("GET", "/policies/terms", signed_in=False, **kw).json()
    assert page["body"] == "1. Simulated accounts only."
    assert [p["version"] for p in api("GET", "/me/policies/pending", **trader).json()] == [1]
    accepted = api("POST", "/me/policies/terms/accept", {"version": 1}, **trader)
    assert accepted.status_code == 200, accepted.text
    assert api("GET", "/me/policies/pending", **trader).json() == []
    # a new version: asked again; only the version in force can be accepted
    v2 = {**TERMS, "body": "1. Simulated accounts only.\n2. One account per person."}
    api("PUT", "/admin/policies/terms", v2, user="owner-b", **kw)
    api("POST", "/admin/settings/proposals/2/approve", {"reason": "ok"}, user="owner-a", **kw)
    assert [p["version"] for p in api("GET", "/me/policies/pending", **trader).json()] == [2]
    stale = api("POST", "/me/policies/terms/accept", {"version": 1}, **trader)
    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "stale")
    old = api("GET", "/policies/terms?version=1", signed_in=False, **kw).json()
    assert old["version"] == 1 and "One account" not in old["body"]
    assert api("GET", "/policies/privacy", signed_in=False, **kw).status_code == 404


def test_a_trader_with_policies_to_accept_cannot_buy() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS,
                          "acceptances": MemoryAcceptances()}  # fmt: skip
    api("PUT", "/admin/policies/terms", TERMS, user="owner-a", **kw)
    api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-b", **kw)
    order = {"product_id": "one-step", "size_cents": 2_500_000}
    refused = api("POST", "/orders", order, roles=("trader",), user="u1",
                  customers=FakeCustomers(cifs={"u1": "CIF00000001"}), **kw)  # fmt: skip
    assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "policies_pending")


def test_a_policy_page_is_checked() -> None:
    for slug, title, body in (("Terms", "t", "b"), ("terms", " ", "b"), ("terms", "t", "")):
        with pytest.raises(PolicyError):
            PolicyPage(slug, title, body)
    kind, value = to_json(PolicyPage("terms", "T", "B"))
    assert from_json(kind, value) == PolicyPage("terms", "T", "B")


# --- rules AC07, AC08: the website's text, figures from the products ---------------------

HERO = {
    "fields": {
        "title_line_1": "Trade the evaluation.",
        "title_line_2": "Keep up to {max_split} of the profit.",
        "lead": "Accounts from {min_size} to {max_size}, challenges from {min_price}.",
        "button": "Choose a challenge",
    },
    "reason": "launch copy",
}


def test_admins_edit_a_website_section_and_figures_come_from_the_products() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    assert api("GET", "/site", signed_in=False, **kw).json() == {}  # the site's own words
    assert api("PUT", "/admin/site/hero", HERO, user="admin-1", **kw).status_code == 200
    assert api("GET", "/site", signed_in=False, **kw).json() == {}  # not approved yet
    own = api("POST", "/admin/settings/proposals/1/approve", {"reason": "x"}, user="admin-1", **kw)
    assert own.json()["detail"]["code"] == "four_eyes"
    ok = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="admin-2", **kw)
    assert ok.status_code == 200, ok.text
    hero = api("GET", "/site", signed_in=False, **kw).json()["hero"]["fields"]
    tokens = api("GET", "/admin/site", **kw).json()["tokens"]
    assert hero["title_line_2"] == f"Keep up to {tokens['max_split']} of the profit."
    assert tokens["max_split"].endswith("%") and tokens["min_price"].startswith("$")
    assert "{" not in hero["lead"]
    editor = api("GET", "/admin/site", **kw).json()
    assert (
        editor["sections"]["hero"]["fields"]["title_line_2"]
        == "Keep up to {max_split} of the profit."
    )
    assert editor["shapes"]["how"]["max_items"] == 6


@pytest.mark.parametrize(
    "text",
    ["Keep 90% of the profit.", "Keep 90 percent.", "Only $99 today", "Up to 200K", "{nope}"],
)
def test_typed_figures_and_unknown_tokens_are_refused(text: str) -> None:
    body = {**HERO, "fields": {**HERO["fields"], "lead": text}}  # type: ignore[dict-item]
    refused = api("PUT", "/admin/site/hero", body, proposals=MemoryProposals(MemoryRecords()))
    assert refused.status_code == 422


def test_a_section_keeps_its_shape() -> None:
    steps = [{"title": f"Step {n}", "text": "Do this."} for n in range(7)]
    with pytest.raises(PolicyError, match="at most 6"):
        SiteSection("how", {"heading": "How it works", "footnote": "Simulated."}, tuple(steps))
    with pytest.raises(PolicyError, match="fields"):
        SiteSection("hero", {"lead": "x"})
    with pytest.raises(PolicyError, match="section"):
        SiteSection("footer", {})
    section = SiteSection("how", {"heading": "How", "footnote": "Simulated."},
                          ({"title": "Choose", "text": "From {min_price}."},))  # fmt: skip
    kind, value = to_json(section)
    assert from_json(kind, json.loads(json.dumps(value))) == section
    assert rendered(section, {"min_price": "$59"})["items"][0]["text"] == "From $59."
