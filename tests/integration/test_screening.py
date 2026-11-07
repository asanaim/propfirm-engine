"""Pre-employment screening through the API [7 Nov 2026]: no staff access
until an admin approves; T3 records, T4 approves, four eyes; yearly."""

import asyncio
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from propfirm.staff.directory import Owner
from propfirm.staff.model import DAY_MS
from propfirm.staff.screening import checks
from tests.integration.test_customers import Platform, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()
OK = {"reason": "ok"}


async def ok(p: Platform, who: str, method: str, url: str, body: object = None) -> Any:
    response = await p.call(method, url, who, body, NONE)
    assert response.status_code in (200, 201), response.text
    return response.json()


async def screen(p: Platform, recorder: str, approver: str, sid: str) -> None:
    for c in checks():
        await ok(p, recorder, "POST", f"/staff/members/{sid}/screening/checks",
                 {"check": c.id, "result": "pass", "reference": f"demo-{c.id}"})  # fmt: skip
    await ok(p, approver, "POST", f"/staff/members/{sid}/screening/decide",
             {"decision": "approved", "note": "all clear"})  # fmt: skip


def test_no_staff_access_until_screened_then_yearly(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False,
                            clock=clock, owners=OWNERS, screening=True) as p:  # fmt: skip
            # owners are exempt: owner A joins an admin; owner B approves
            admin = await ok(p, "kc-owner-a", "POST", "/staff/members", {
                "subject": "kc-admin", "name": "Admin", "kind": "staff", "tier": 4,
                "reason": "hired"})  # fmt: skip
            await ok(p, "kc-owner-b", "POST",
                     f"/staff/grants/{admin['grants'][0]['grt']}/approve", OK)  # fmt: skip
            blocked = await p.call("GET", "/staff/me", "kc-admin", None, NONE)
            assert blocked.status_code == 403
            assert blocked.json()["detail"]["code"] == "screening_required"
            todo = await ok(p, "kc-owner-a", "GET", "/staff/me/todo")
            assert ("screening_check", admin["sid"]) in {(t["kind"], t["ref"]) for t in todo}
            # owner A records every check; the same owner may not approve (four eyes)
            for c in checks():
                await ok(p, "kc-owner-a", "POST", f"/staff/members/{admin['sid']}/screening/checks",
                         {"check": c.id, "result": "pass", "reference": "ref"})  # fmt: skip
            approve = {"decision": "approved", "note": "x"}
            url = f"/staff/members/{admin['sid']}/screening/decide"
            same = await p.call("POST", url, "kc-owner-a", approve, NONE)
            assert same.status_code == 403 and same.json()["detail"]["code"] == "four_eyes"
            todo = await ok(p, "kc-owner-b", "GET", "/staff/me/todo")
            assert ("screening_approve", admin["sid"]) in {(t["kind"], t["ref"]) for t in todo}
            view = await ok(p, "kc-owner-b", "POST",
                            f"/staff/members/{admin['sid']}/screening/decide",
                            {"decision": "approved", "note": "all clear"})  # fmt: skip
            assert view["status"] == "approved" and view["valid_until"] > clock()
            assert (await p.call("GET", "/staff/me", "kc-admin", None, NONE)).status_code == 200

            # a lead (T3) records; the admin approves; a T2 may not record
            lead = await ok(p, "kc-admin", "POST", "/staff/members", {
                "subject": "kc-lead", "name": "Lead", "kind": "staff", "tier": 3,
                "reason": "hired"})  # fmt: skip
            await ok(p, "kc-owner-a", "POST", f"/staff/grants/{lead['grants'][0]['grt']}/approve",
                     OK)  # fmt: skip
            await screen(p, "kc-owner-a", "kc-admin", lead["sid"])
            spec = await ok(p, "kc-lead", "POST", "/staff/members", {
                "subject": "kc-spec", "name": "Spec", "kind": "staff", "tier": 2,
                "lead": lead["sid"], "reason": "hired"})  # fmt: skip
            await ok(p, "kc-admin", "POST", f"/staff/grants/{spec['grants'][0]['grt']}/approve",
                     OK)  # fmt: skip
            await screen(p, "kc-lead", "kc-admin", spec["sid"])
            body = {"check": "identity", "result": "pass", "reference": "x"}
            t2 = await p.call("POST", f"/staff/members/{lead['sid']}/screening/checks", "kc-spec",
                              body, NONE)  # fmt: skip
            assert t2.status_code == 403
            own = await p.call("POST", f"/staff/members/{lead['sid']}/screening/checks", "kc-lead",
                               body, NONE)  # fmt: skip
            assert own.status_code == 403
            # approval needs every check passed
            failed = {"check": "references", "result": "fail", "reference": "one reference only"}
            await ok(p, "kc-lead", "POST", f"/staff/members/{spec['sid']}/screening/checks", failed)
            url = f"/staff/members/{spec['sid']}/screening/decide"
            refused = await p.call("POST", url, "kc-admin", approve, NONE)
            assert refused.status_code == 409, refused.text  # references failed
            # Restricted: a T2 does not see another's screening
            seen = await p.call("GET", f"/staff/members/{lead['sid']}/screening", "kc-spec",
                                None, NONE)  # fmt: skip
            assert seen.status_code == 403

            # a year later the approval expires: access closes until rescreened
            offset["ms"] = 366 * DAY_MS
            expired = await p.call("GET", "/staff/me", "kc-lead", None, NONE)
            assert expired.status_code == 403
            assert (await p.call("GET", "/staff/me", "kc-owner-a", None, NONE)).status_code == 200

    asyncio.run(scenario())
