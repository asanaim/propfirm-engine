"""Apprentices against PostgreSQL, through the API [staff spec 5c]: masked
reads, proposals carried out by the mentor, the training gate."""

import asyncio
from typing import Any

import pytest

from propfirm.staff.directory import Owner
from propfirm.staff.masking import MASK
from propfirm.staff.model import add_months
from tests.integration.test_customers import ADULT, Platform, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()
OK = {"reason": "ok"}


async def ok(p: Platform, who: str, method: str, url: str, body: object = None,
             roles: tuple[str, ...] = NONE) -> Any:  # fmt: skip
    response = await p.call(method, url, who, body, roles)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_an_apprentice_reads_masked_and_acts_only_through_the_mentor(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False,
                            owners=OWNERS) as p:  # fmt: skip
            # an integrity lead (trained), who mentors an apprentice in integrity
            body: dict[str, Any] = {"subject": "kc-lead", "name": "Lead", "kind": "staff",
                    "sections": {"integrity": 3}, "reason": "hired"}  # fmt: skip
            lead = await ok(p, "kc-owner-a", "POST", "/staff/members", body)
            await ok(p, "kc-owner-a", "POST", f"/staff/members/{lead['sid']}/training",
                     {"section": "integrity", "note": "passed"})  # fmt: skip
            await ok(p, "kc-owner-b", "POST", f"/staff/grants/{lead['grants'][0]['grt']}/approve",
                     OK)  # fmt: skip
            term = add_months(p.clock(), 4)
            body = {"subject": "kc-app", "name": "Learner", "kind": "apprentice",
                    "mentor": lead["sid"], "sections": {"integrity": 0}, "ends_at": term,
                    "reason": "intake"}  # fmt: skip
            app = await ok(p, "kc-lead", "POST", "/staff/members", body)
            for g in app["grants"]:
                await ok(p, "kc-owner-a", "POST", f"/staff/grants/{g['grt']}/approve", OK)
            me = await ok(p, "kc-app", "GET", "/staff/me")
            assert me["access"]["sections"] == {"integrity": "T0"}

            # a customer and a case about them
            await ok(p, "u1", "POST", "/me", ADULT, ("trader",))
            alert = {"cif": "CIF00000001", "kind": "fraud", "severity": "high", "reason": "ip"}
            case = await ok(p, "kc-lead", "POST", "/staff/cases", alert)
            cas = case["cas"]

            # masked reads: the same record, personal data hidden
            event = {"event_id": "2026-11-20-test", "title": "Test", "scope": "currency",
                     "currency": "USD", "ts": p.clock() + 86_400_000,
                     "reason": "example"}  # fmt: skip
            await p.call(
                "POST", "/staff/news/events", "kc-owner-a", event, NONE
            )  # sent, maybe pending
            full = await ok(p, "kc-lead", "GET", "/staff/news")
            masked = await ok(p, "kc-app", "GET", "/staff/news")
            assert full["actions"][0]["author_email"] == "kc-owner-a@example.com"
            assert masked["actions"][0]["author_email"] == MASK
            assert masked["actions"][0]["event_id"] == full["actions"][0]["event_id"]
            assert (
                await p.call("GET", f"/staff/cases/{cas}", "kc-app", None, NONE)
            ).status_code == 200

            # never act directly: propose, the mentor decides
            note = await p.call("POST", f"/staff/cases/{cas}/notes", "kc-app", {"text": "x"}, NONE)
            assert note.status_code == 403
            prop = await ok(p, "kc-app", "POST", "/staff/apprentice/proposals", {
                "action": "case.note", "target": cas,
                "body": {"text": "Same device as CIF00000002."}})  # fmt: skip
            wrong = {"action": "case.decide", "target": cas, "body": {"outcome": "maybe"}}
            bad = await p.call("POST", "/staff/apprentice/proposals", "kc-app", wrong, NONE)
            assert bad.status_code == 422
            todo = await ok(p, "kc-lead", "GET", "/staff/me/todo")
            assert ("apprentice_proposal", str(prop["id"])) in {(t["kind"], t["ref"]) for t in todo}
            other = await p.call("POST", f"/staff/apprentice/proposals/{prop['id']}/approve",
                                 "kc-other", OK, ("staff",))  # fmt: skip
            assert other.status_code == 403  # not the mentor
            done = await ok(p, "kc-lead", "POST",
                            f"/staff/apprentice/proposals/{prop['id']}/approve", OK)  # fmt: skip
            assert (done["status"], done["decided_by"]) == ("approved", "kc-lead")
            record = await ok(p, "kc-lead", "GET", f"/staff/cases/{cas}")
            notes = [h for h in record["history"] if h["kind"] == "note"]
            assert notes[-1]["detail"]["text"] == (
                f"[proposed by {app['sid']}] Same device as CIF00000002."
            )
            again = await p.call("POST", f"/staff/apprentice/proposals/{prop['id']}/reject",
                                 "kc-lead", OK, NONE)  # fmt: skip
            assert again.status_code == 409
            mine = await ok(p, "kc-app", "GET", "/staff/apprentice/proposals")
            assert [m["status"] for m in mine] == ["approved"]
            staff = await p.call("POST", "/staff/apprentice/proposals", "kc-lead", {
                "action": "case.note", "target": cas, "body": {"text": "x"}}, NONE)  # fmt: skip
            assert staff.status_code == 403  # staff act themselves

    asyncio.run(scenario())
