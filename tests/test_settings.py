"""Admin-owned settings: the overlay on the policy file, the settings
service, and the admin-only API views."""

import asyncio
import copy
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
from propfirm.settings.overlay import (
    CountryGating,
    Setting,
    SettingChange,
    apply_changes,
    from_json,
    limits_rule_set,
    stage_limits,
    to_json,
)
from propfirm.settings.service import Settings, SettingsError
from propfirm.settings.store import ChangeStatus
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
    assert s1 is not None and s1.rsv_id == "one-step-s1-v1-a3"
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
    assert others == [r for r in BASE.rule_sets() if r.rsv_id != "one-step-s1-v1"]


def test_a_file_version_of_a_changed_stage_keeps_the_admin_limits() -> None:
    raw: dict[str, Any] = tomllib.loads(DEFAULT_POLICY.read_text("utf-8"))
    data = copy.deepcopy(raw)
    one_step = next(p for p in data["products"] if p["id"] == "one-step")
    one_step["stages"][0].update(version=2, min_trading_days=5)
    policy = apply_changes(parse_policy(data), [change(7, ONE_STEP_S1)])
    s1 = policy.product("one-step").rule_set(Stage.S1)
    assert s1 is not None and s1.rsv_id == "one-step-s1-v2-a7"
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
        limits_rule_set(s1, stage_limits("one-step", "s1", "8", "3", "0.0001", "static"), 1)


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
    assert isinstance(item, AddRuleSet) and item.rule_set.rsv_id == "one-step-s1-v1-a7"
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
        assert rule_set.rsv_id == "one-step-s1-v1-a1"
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
) -> httpx.Response:
    settings = Settings(BASE, records or MemoryRecords(), engine or Engine())
    deps = Deps(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        engine or Engine(),
        verifier(),
        FakeCustomers(),
        policy=settings,
        settings=settings,
        clock=lambda: T0,
    )
    app = create_app(deps)
    headers = bearer("admin-1", roles) if signed_in else {}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await client.request(method, url, json=body, headers=headers)

    return asyncio.run(go())


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
    ("GET", "/admin/catalogue", None),
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


def test_admin_views_show_the_gating_switch_and_every_limit() -> None:
    assert api("GET", "/admin/access").json() == {
        "country_gating": False,
        "blocked_countries": ["BD", "IN"],
        "min_age": 18,
    }
    lite = api("GET", "/admin/catalogue").json()[0]
    assert lite["stages"][0] == {
        "stage": "s1",
        "rsv_id": "lite-s1-v1",
        "profit_target_percent": "6",
        "max_loss_percent": "6",
        "max_loss_kind": "static",
        "daily_loss_percent": "3",
        "min_trading_days": 4,
        "consistency_percent": None,
        "consistency_at_pass": False,
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
    }
    assert api("GET", "/admin/access", records=records).json()["country_gating"] is True
    (entry,) = api("GET", "/admin/settings/changes", records=records).json()
    assert entry["id"] == 1 and entry["reason"] == "counsel C8"


def test_admin_changes_stage_limits() -> None:
    records = MemoryRecords()
    changed = api("PUT", LIMITS_URL, LIMITS, records=records)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["status"], body["rsv_id"]) == ("applied", "one-step-s1-v1-a1")
    assert body["change"]["value"] == {
        "product_id": "one-step",
        "stage": "s1",
        "profit_target_percent": "8",
        "daily_loss_percent": "3",
        "max_loss_percent": "5",
        "max_loss_kind": "static",
    }
    assert body["change"]["journal_seq"] == 101
    one_step = next(
        p for p in api("GET", "/admin/catalogue", records=records).json() if p["id"] == "one-step"
    )
    assert one_step["stages"][0]["rsv_id"] == "one-step-s1-v1-a1"
    assert one_step["stages"][0]["profit_target_percent"] == "8"
    public = api("GET", "/catalogue", records=records, signed_in=False).json()
    public_s1 = next(p for p in public if p["id"] == "one-step")["stages"][0]
    assert public_s1["rsv_id"] == "one-step-s1-v1-a1" and "profit_target_percent" not in public_s1


def test_stage_limits_while_the_engine_is_down_answer_202() -> None:
    records = MemoryRecords()
    pending = api("PUT", LIMITS_URL, LIMITS, records=records, engine=Engine(down=True))
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
    assert api("PUT", url, body, records=records).status_code == status
    assert records.changes == []


def test_funded_limits_take_no_profit_target() -> None:
    funded = {**LIMITS, "profit_target_percent": None}
    changed = api("PUT", "/admin/products/one-step/stages/funded/limits", funded)
    assert changed.status_code == 200, changed.text
    assert changed.json()["rsv_id"] == "one-step-funded-v1-a1"


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
        u["username"]: set(u["realmRoles"]) - {"default-roles-propfirm"} for u in realm["users"]
    }
    assert users == {
        "trader1@example.com": set(),
        "staff1@example.com": {"staff"},
        "admin1@example.com": {"admin"},
    }
