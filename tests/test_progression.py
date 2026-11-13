"""Stage progression: what follows a pass, the progression history, and the
employee-only review endpoints."""

import asyncio
import re
from collections.abc import Mapping

import httpx
import pytest

from propfirm.api.app import Deps, create_app
from propfirm.customers.lifecycle import plan_for
from propfirm.customers.lifecycle_store import Plan
from propfirm.customers.progression import (
    PROGRESSION_KINDS,
    ProgressionError,
    ProgressionEvent,
    ProgressionState,
    next_stage,
    progression_state,
)
from propfirm.customers.store import _MIGRATIONS, AccountRecord
from propfirm.policy import load_policy, stage_of
from propfirm.rules.products import Stage
from propfirm.settings.overlay import SettingChange, apply_changes, stage_limits
from tests.auth_rig import FakeCustomers, bearer, verifier

POLICY = load_policy()
PRG = "PRG00000001"
type Step = tuple[str, int, Mapping[str, object]]


def account(product: str, stage: str, rsv: str = "x-s1-v1") -> AccountRecord:
    return AccountRecord(
        "ACC00000001", "CIF00000001", "ORD00000001", product, rsv, 1_000_000, 0, stage
    )


# --- what follows a pass --------------------------------------------------------------


def test_the_next_stage_of_each_product() -> None:
    assert plan_for(POLICY, account("lite", "s1")) == Plan(Stage.S2, "lite-s2-v3")
    assert plan_for(POLICY, account("classic", "s1")) == Plan(Stage.S2, "classic-s2-v2")
    assert plan_for(POLICY, account("lite", "s2")) == Plan(Stage.FUNDED, None)
    assert plan_for(POLICY, account("one-step", "s1")) == Plan(Stage.FUNDED, None)
    assert plan_for(POLICY, account("instant", "funded")) == Plan(None, None, "no_next_stage")
    assert plan_for(POLICY, account("gone", "s1")) == Plan(None, None, "unknown_product")
    assert plan_for(POLICY, account("one-step", "s2")) == Plan(None, None, "no_next_stage")


def test_the_next_stage_uses_the_rules_in_force_at_the_pass() -> None:
    change = SettingChange(
        4, stage_limits("lite", "s2", "4", "3", "5", "static"), "admin-1", None, "offer", 1
    )
    in_force = apply_changes(POLICY, [change])
    assert plan_for(in_force, account("lite", "s1")) == Plan(Stage.S2, "lite-s2-v3-a4")


def test_next_stage_follows_the_product_order() -> None:
    lite = POLICY.product("lite")
    s2, funded = lite.rule_set(Stage.S2), lite.rule_set(Stage.FUNDED)
    assert next_stage(lite, Stage.S1) == s2 and next_stage(lite, Stage.S2) == funded
    assert next_stage(lite, Stage.FUNDED) is None
    assert next_stage(POLICY.product("one-step"), Stage.S2) is None  # no S2 there


def test_the_stage_a_rule_set_id_names() -> None:
    assert stage_of("lite-s1-v3") is Stage.S1
    assert stage_of("one-step-s1-v3-a7") is Stage.S1
    assert stage_of("instant-funded-v1") is Stage.FUNDED
    assert stage_of("x-s2-v1-s1-v3") is Stage.S1  # the suffix decides
    with pytest.raises(ValueError):
        stage_of("lite-s3-v1")


# --- the history ----------------------------------------------------------------------


def history(*steps: Step) -> list[ProgressionEvent]:
    return [ProgressionEvent(PRG, kind, ts, detail) for kind, ts, detail in steps]


TO_S2: Step = ("created", 1, {"to_stage": "s2", "to_rsv_id": "lite-s2-v3"})
TO_FUNDED: Step = ("created", 1, {"to_stage": "funded", "to_rsv_id": None})
ASSIGNED: Step = ("account_assigned", 2, {"acc": "ACC00000002"})
ATTEMPT: Step = ("provision_attempted", 3, {"acc": "ACC00000002", "entry_id": "1-0"})
OPENED: Step = ("provisioned", 4, {"acc": "ACC00000002", "journal_seq": 9})
APPROVED: Step = ("approved", 5, {"by": "staff-1", "reason": "clean"})
DENIED: Step = ("denied", 5, {"by": "staff-1", "reason": "copy trading"})


def test_an_evaluation_stage_opens_its_account() -> None:
    assert progression_state(PRG, history(TO_S2)) == ProgressionState(
        "provisioning", "lite-s2-v3", None, None, None, None
    )
    retried = progression_state(PRG, history(TO_S2, ASSIGNED, ATTEMPT, ATTEMPT))
    assert (retried.status, retried.to_acc) == ("provisioning", "ACC00000002")
    done = progression_state(PRG, history(TO_S2, ASSIGNED, ATTEMPT, OPENED))
    assert (done.status, done.to_acc) == ("provisioned", "ACC00000002")
    failed = progression_state(
        PRG, history(TO_S2, ASSIGNED, ATTEMPT, ("failed", 4, {"reason": "unknown_rule_set"}))
    )
    assert (failed.status, failed.failure) == ("failed", "unknown_rule_set")


def test_a_funded_stage_waits_for_an_employee() -> None:
    assert progression_state(PRG, history(TO_FUNDED)).status == "review"
    approved = progression_state(PRG, history(TO_FUNDED, APPROVED))
    assert (approved.status, approved.decided_by, approved.decided_at) == ("approved", "staff-1", 5)
    assert progression_state(PRG, history(TO_FUNDED, DENIED)).status == "denied"


def test_no_next_stage_is_blocked_with_the_reason() -> None:
    blocked = progression_state(
        PRG, history(("created", 1, {"to_stage": None, "reason": "unknown_product"}))
    )
    assert (blocked.status, blocked.failure) == ("blocked", "unknown_product")


@pytest.mark.parametrize(
    "steps",
    [
        (),
        (ASSIGNED,),
        (TO_S2, TO_S2),
        (TO_S2, APPROVED),  # evaluation stages are not reviewed
        (TO_FUNDED, ASSIGNED),  # funded accounts open only after onboarding (3b)
        (TO_FUNDED, APPROVED, DENIED),  # one decision
        (TO_S2, ATTEMPT),  # attempt without an account
        (TO_S2, ASSIGNED, ASSIGNED),
        (TO_S2, ASSIGNED, OPENED, ("failed", 5, {"reason": "x"})),
        (("created", 1, {"to_stage": "s1", "to_rsv_id": "x"}),),  # never back to S1
        (TO_S2, ("refunded", 2, {})),
    ],
)
def test_histories_no_valid_progression_has_are_refused(steps: tuple[Step, ...]) -> None:
    with pytest.raises(ProgressionError):
        progression_state(PRG, history(*steps))


def test_the_migration_allows_exactly_the_progression_event_kinds() -> None:
    sql = dict(_MIGRATIONS)[9]  # the kinds as widened last (rule AC00)
    found = re.search(r"kind IN \(([^)]*)\)", sql)
    assert found is not None
    assert tuple(re.findall(r"'([a-z_]+)'", found.group(1))) == PROGRESSION_KINDS


# --- employee-only review endpoints ---------------------------------------------------


def call(method: str, url: str, roles: tuple[str, ...] | None, body: object = None) -> int:
    deps = Deps(None, None, None, verifier(), FakeCustomers())  # type: ignore[arg-type]
    app = create_app(deps)
    headers = bearer("someone", roles) if roles is not None else {}

    async def go() -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            response = await client.request(method, url, json=body, headers=headers)
            return response.status_code

    return asyncio.run(go())


STAFF_CALLS = [
    ("GET", "/staff/progressions", None),
    ("GET", f"/staff/progressions/{PRG}", None),
    ("POST", f"/staff/progressions/{PRG}/approve", {"reason": "clean record"}),
    ("POST", f"/staff/progressions/{PRG}/deny", {"reason": "copy trading"}),
]


@pytest.mark.parametrize(("method", "url", "body"), STAFF_CALLS)
def test_reviews_are_for_employees_and_admins(method: str, url: str, body: object) -> None:
    assert call(method, url, None, body) == 401
    assert call(method, url, ("trader",), body) == 403
    for roles in (("staff",), ("admin",)):  # allowed in; no lifecycle wired in this app
        assert call(method, url, roles, body) == 503


def test_review_requests_the_api_refuses() -> None:
    assert call("POST", f"/staff/progressions/{PRG}/approve", ("staff",), {}) == 422
    assert call("POST", f"/staff/progressions/{PRG}/approve", ("staff",), {"reason": " "}) == 422
    assert call("POST", "/staff/progressions/PRG1/approve", ("staff",), {"reason": "ok"}) == 422
    assert call("GET", "/staff/progressions?status=lost", ("staff",)) == 422
