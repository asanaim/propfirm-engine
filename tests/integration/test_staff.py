"""The staff directory against PostgreSQL, through the API: owners, joiners,
grants with four eyes, step-up, end dates, leavers, apprentices."""

import asyncio
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from propfirm.staff.directory import Owner
from propfirm.staff.model import DAY_MS, add_months
from tests.integration.test_customers import Platform, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()  # directory logins: the Keycloak roles do not matter
OK = {"reason": "ok"}


async def as_(p: Platform, who: str, method: str, url: str, body: object = None, **kw: Any) -> Any:
    return await p.call(method, url, who, body, NONE, **kw)


async def ok(p: Platform, who: str, method: str, url: str, body: object = None) -> Any:
    response = await as_(p, who, method, url, body)
    assert response.status_code in (200, 201), response.text
    return response.json()


async def me(p: Platform, who: str) -> Any:
    return await ok(p, who, "GET", "/staff/me")


async def trained(p: Platform, assessor: str, sid: str, section: str) -> None:
    body = {"section": section, "note": "course and test passed"}
    await ok(p, assessor, "POST", f"/staff/members/{sid}/training", body)


async def join(p: Platform, by: str, approver: str, subject: str, **fields: Any) -> str:
    """Add a member and approve every grant asked for them."""
    body = {"subject": subject, "name": subject.title(), "kind": "staff", "reason": "hired"}
    joined = await ok(p, by, "POST", "/staff/members", {**body, **fields})
    if fields.get("kind", "staff") != "apprentice":
        for section in fields.get("roles", []):  # the training gate (5c)
            await trained(p, approver, joined["sid"], section)
    for g in joined["grants"]:
        await ok(p, approver, "POST", f"/staff/grants/{g['grt']}/approve", OK)
    sid: str = joined["sid"]
    return sid


def test_owners_grant_with_four_eyes_and_grants_end_on_their_own(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, start_engine=False, clock=clock, owners=OWNERS
        ) as p:
            assert p.staff is not None
            assert await p.staff.bootstrap(clock()) == []  # once only
            a = await me(p, "kc-owner-a")
            assert (a["access"]["tier"], a["access"]["source"], a["member"]["sid"]) == (
                "T5",
                "directory",
                "STF00000001",
            )
            assert "payout.authorise" in a["access"]["permissions"]

            # a joiner: tier and role requested by one owner, approved by the other
            body = {
                "subject": "kc-lead",
                "name": "Lead",
                "kind": "staff",
                "team": "payouts",
                "tier": 3,
                "roles": ["payouts"],
                "reason": "hired",
            }
            joined = await ok(p, "kc-owner-a", "POST", "/staff/members", body)
            assert [(g["grant"], g["value"], g["status"]) for g in joined["grants"]] == [
                ("tier", "T3", "requested"),
                ("role", "payouts", "requested"),
            ]
            tier_grant = joined["grants"][0]["grt"]
            approve = f"/staff/grants/{tier_grant}/approve"
            own = await as_(p, "kc-owner-a", "POST", approve, OK)
            assert (own.status_code, own.json()["detail"]["code"]) == (403, "four_eyes")
            waiting = (await me(p, "kc-owner-b"))["awaiting_approval"]
            assert [g["grt"] for g in waiting] == [g["grt"] for g in joined["grants"]]
            no_mfa = await as_(p, "kc-owner-b", "POST", approve, OK, mfa=False)
            assert (no_mfa.status_code, no_mfa.json()["detail"]["code"]) == (401, "mfa_required")
            gated = await as_(p, "kc-owner-b", "POST", approve, OK)  # the tier: no course needed
            assert gated.status_code == 200
            role = f"/staff/grants/{joined['grants'][1]['grt']}/approve"
            untrained = await as_(p, "kc-owner-b", "POST", role, OK)
            assert (untrained.status_code, untrained.json()["detail"]["code"]) == (409, "training")
            await trained(p, "kc-owner-b", joined["sid"], "payouts")
            await ok(p, "kc-owner-b", "POST", role, OK)
            lead = await me(p, "kc-lead")
            assert (lead["access"]["tier"], lead["access"]["roles"]) == ("T3", ["payouts"])
            assert "staff.manage" in lead["access"]["permissions"]
            assert "payout.authorise" not in lead["access"]["permissions"]

            # an admin (T4): two owners; a T3 cannot ask for it
            admin = await join(p, "kc-owner-a", "kc-owner-b", "kc-admin", tier=4)
            assert (await me(p, "kc-admin"))["access"]["tier"] == "T4"
            refused = await as_(
                p, "kc-lead", "POST", "/staff/members",
                {"subject": "kc-x", "name": "X", "kind": "staff", "tier": 4, "reason": "r"},
            )  # fmt: skip
            assert refused.status_code == 403

            # a specialist: the lead asks for the tier, an admin approves (never
            # the lead); the section (role) is proposed by an admin and approved
            # by an owner [decided 6 Nov 2026]
            body = {"subject": "kc-spec", "name": "Spec", "kind": "staff", "tier": 2,
                    "lead": joined["sid"], "reason": "hired"}  # fmt: skip
            with_role = {**body, "roles": ["payouts"]}
            no_section = await as_(p, "kc-lead", "POST", "/staff/members", with_role)
            assert no_section.status_code == 403
            spec = await ok(p, "kc-lead", "POST", "/staff/members", body)
            by_lead = await as_(
                p, "kc-lead", "POST", f"/staff/grants/{spec['grants'][0]['grt']}/approve", OK
            )
            assert by_lead.status_code == 403
            for g in spec["grants"]:
                await ok(p, "kc-admin", "POST", f"/staff/grants/{g['grt']}/approve", OK)
            await trained(p, "kc-admin", spec["sid"], "payouts")
            ask = {"sid": spec["sid"], "grant": "role", "value": "payouts", "reason": "desk"}
            section = await ok(p, "kc-admin", "POST", "/staff/grants", ask)
            approve = f"/staff/grants/{section['grt']}/approve"
            assert (await as_(p, "kc-admin", "POST", approve, OK)).status_code == 403
            await ok(p, "kc-owner-a", "POST", approve, OK)
            sid = spec["sid"]
            s = await me(p, "kc-spec")
            assert "payout.pass" in s["access"]["permissions"]
            assert (await as_(p, "kc-spec", "GET", "/staff/members")).status_code == 403
            assert (await as_(p, "kc-spec", "GET", f"/staff/members/{sid}")).status_code == 200

            # an admin power for a T2: asked by the lead, so an owner approves
            ends = clock() + 30 * DAY_MS
            power = {"sid": sid, "grant": "power", "value": "news.cancel", "ends_at": ends,
                     "reason": "cover"}  # fmt: skip
            asked = await ok(p, "kc-lead", "POST", "/staff/grants", power)
            url = f"/staff/grants/{asked['grt']}/approve"
            assert (await as_(p, "kc-admin", "POST", url, OK)).status_code == 403
            await ok(p, "kc-owner-a", "POST", url, OK)
            assert "news.cancel" in (await me(p, "kc-spec"))["access"]["permissions"]
            too_long = {**power, "value": "settings.view", "ends_at": clock() + 91 * DAY_MS}
            assert (await as_(p, "kc-lead", "POST", "/staff/grants", too_long)).status_code == 422

            # acting cover for 30 days at most, then back by itself
            cover = {"sid": sid, "grant": "acting", "value": 3, "ends_at": clock() + 7 * DAY_MS,
                     "reason": "lead on leave"}  # fmt: skip
            acting = await ok(p, "kc-lead", "POST", "/staff/grants", cover)
            await ok(p, "kc-admin", "POST", f"/staff/grants/{acting['grt']}/approve", OK)
            assert (await me(p, "kc-spec"))["access"]["tier"] == "T3"
            offset["ms"] = 7 * DAY_MS
            later = await me(p, "kc-spec")
            assert later["access"]["tier"] == "T2"
            assert next(g for g in later["grants"] if g["grt"] == acting["grt"])["status"] == (
                "expired"
            )
            offset["ms"] = 31 * DAY_MS
            assert "news.cancel" not in (await me(p, "kc-spec"))["access"]["permissions"]

            # a promotion replaces the old tier
            up = {"sid": sid, "grant": "tier", "value": 3, "reason": "promoted"}
            promoted = await ok(p, "kc-lead", "POST", "/staff/grants", up)
            await ok(p, "kc-admin", "POST", f"/staff/grants/{promoted['grt']}/approve", OK)
            detail = await ok(p, "kc-lead", "GET", f"/staff/members/{sid}")
            tiers = [(g["value"], g["status"]) for g in detail["grants"] if g["grant"] == "tier"]
            assert tiers == [("T2", "superseded"), ("T3", "approved")]

            # a leaver: no access at once, every grant ends; Keycloak roles do not bring it back
            leave = f"/staff/members/{sid}/leave"
            assert (await as_(p, "kc-lead", "POST", leave, OK)).status_code == 403  # same tier
            left = await ok(p, "kc-admin", "POST", leave, OK)
            assert left["status"] == "left"
            assert (await as_(p, "kc-spec", "GET", "/staff/me")).status_code == 403
            legacy = await p.call("GET", "/staff/payouts", "kc-spec", None, ("staff",))
            assert legacy.status_code == 403
            after = await ok(p, "kc-admin", "GET", f"/staff/members/{sid}")
            assert {g["status"] for g in after["grants"]} <= {"revoked", "superseded", "expired"}
            owner = f"/staff/members/{a['member']['sid']}/leave"
            assert (await as_(p, "kc-owner-b", "POST", owner, OK)).status_code == 403
            assert admin.startswith("STF")

            # a login not in the directory: Keycloak roles as before
            old = (await p.call("GET", "/staff/me", "kc-old", None, ("staff",))).json()
            assert (old["access"]["source"], old["access"]["tier"], old["member"]) == (
                "legacy",
                "T2",
                None,
            )

    asyncio.run(scenario())


def test_an_apprentice_learns_then_graduates_on_a_new_login(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, start_engine=False, owners=OWNERS,
            legacy_roles=False,
        ) as p:  # fmt: skip
            now = p.clock()
            await join(p, "kc-owner-a", "kc-owner-b", "kc-admin", tier=4)
            await join(p, "kc-owner-a", "kc-admin", "kc-lead", tier=3)
            mentor = await join(p, "kc-admin", "kc-owner-a", "kc-mentor", tier=2, roles=["kyc"])

            base = {"subject": "kc-app", "name": "Learner", "kind": "apprentice",
                    "mentor": mentor, "team": "kyc", "roles": ["kyc"],
                    "reason": "intake"}  # fmt: skip
            seven = await as_(p, "kc-admin", "POST", "/staff/members",
                              {**base, "ends_at": add_months(now, 7)})  # fmt: skip
            assert seven.status_code == 422  # 3 to 6 months
            app = await join(p, "kc-admin", "kc-owner-a", "kc-app", **{
                k: v for k, v in base.items() if k not in ("subject", "name", "reason")
            }, ends_at=add_months(now, 4))  # fmt: skip
            assert app.startswith("APP")
            learner = await me(p, "kc-app")
            assert (learner["access"]["tier"], learner["access"]["permissions"]) == (
                "T0",
                ["staff.view_masked", "apprentice.propose"],
            )
            assert (await as_(p, "kc-app", "POST", "/staff/cases", {})).status_code in (403, 422)

            recommend = f"/staff/members/{app}/recommend"
            graduate = f"/staff/members/{app}/graduate"
            new_login = {"subject": "kc-app-staff", "reason": "passed"}
            early = await as_(p, "kc-admin", "POST", graduate, new_login)
            assert early.status_code == 409  # no recommendation yet
            assert (await as_(p, "kc-lead", "POST", recommend, OK)).status_code == 403
            await ok(p, "kc-mentor", "POST", recommend, OK)
            assert (await as_(p, "kc-mentor", "POST", graduate, new_login)).status_code == 403
            assert [m["sid"] for m in (await me(p, "kc-admin"))["graduations"]] == [app]
            untrained = await as_(p, "kc-admin", "POST", graduate, new_login)
            assert (untrained.status_code, untrained.json()["detail"]["code"]) == (409, "training")
            await trained(p, "kc-mentor", app, "kyc")  # the mentor (KYC T2) assesses
            staff = await ok(p, "kc-admin", "POST", graduate, new_login)
            assert (staff["from_sid"], staff["kind"], staff["lead"]) == (app, "staff", mentor)
            # the section learnt: proposed by the graduating admin, approved by
            # an owner [decided 6 Nov 2026]; no access until then
            assert (await as_(p, "kc-app-staff", "GET", "/staff/me")).status_code == 403
            waiting = await ok(p, "kc-owner-a", "GET", "/staff/grants?status=requested")
            mine = [g for g in waiting if g["sid"] == staff["sid"]]
            assert [(g["grant"], g["value"], g["requested_by"]) for g in mine] == [
                ("level", "kyc:1", "kc-admin")
            ]
            nope = await as_(p, "kc-admin", "POST", f"/staff/grants/{mine[0]['grt']}/approve", OK)
            assert nope.status_code == 403  # the proposer, and an admin
            await ok(p, "kc-owner-a", "POST", f"/staff/grants/{mine[0]['grt']}/approve", OK)
            graduate_me = await me(p, "kc-app-staff")
            assert graduate_me["access"]["tier"] == "T1"
            assert graduate_me["access"]["sections"] == {"kyc": "T1"}  # the section learnt

            # promoted inside one section, or given another [decided 6 Nov
            # 2026]: an admin proposes, an owner approves
            up = {"sid": staff["sid"], "grant": "level", "value": "kyc:2", "reason": "promoted"}
            no_lead = await as_(p, "kc-lead", "POST", "/staff/grants", up)
            assert no_lead.status_code == 403  # a lead does not propose sections
            asked = await ok(p, "kc-admin", "POST", "/staff/grants", up)
            await ok(p, "kc-owner-a", "POST", f"/staff/grants/{asked['grt']}/approve", OK)
            other = {**up, "value": "payouts:1"}
            await trained(p, "kc-admin", staff["sid"], "payouts")
            asked = await ok(p, "kc-admin", "POST", "/staff/grants", other)
            await ok(p, "kc-owner-a", "POST", f"/staff/grants/{asked['grt']}/approve", OK)
            ladders = (await me(p, "kc-app-staff"))["access"]
            assert ladders["sections"] == {"kyc": "T2", "payouts": "T1"}
            assert "onboarding.decide" in ladders["permissions"]
            assert "payout.pass" not in ladders["permissions"]  # payouts: T1 only
            assert (await as_(p, "kc-app", "GET", "/staff/me")).status_code == 403
            record = await ok(p, "kc-admin", "GET", f"/staff/members/{app}")
            assert (record["status"], record["to_sid"]) == ("graduated", staff["sid"])

            # STAFF_LEGACY_ROLES=0: Keycloak roles alone give nothing
            old = await p.call("GET", "/staff/me", "kc-old", None, ("admin",))
            assert old.status_code == 403

    asyncio.run(scenario())
