"""The staff page against PostgreSQL, through the API: duties acknowledged,
notifications by permission, messages on a record, the to-do list."""

import asyncio
from typing import Any

import pytest

from propfirm.staff.directory import Owner
from tests.integration.test_customers import Platform, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
STAFF = ("staff",)
ADMIN = ("admin",)


async def ok(p: Platform, method: str, url: str, who: str, body: object = None,
             roles: tuple[str, ...] = STAFF) -> Any:  # fmt: skip
    response = await p.call(method, url, who, body, roles)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_duties_notifications_and_messages(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, start_engine=False, owners=OWNERS
        ) as p:
            assert p.desk is not None
            now = p.clock()

            # duties: each sheet that applies, acknowledged once per version
            page = await ok(p, "GET", "/staff/me", "staff-1")
            sheets = [d["sheet"] for d in page["duties"]]
            assert "payouts" in sheets and "risk" in sheets  # legacy staff: every role
            assert {t["kind"] for t in page["todo"]} == {"duties"}
            await ok(p, "POST", "/staff/me/duties/payouts/ack", "staff-1")
            await ok(p, "POST", "/staff/me/duties/payouts/ack", "staff-1")  # once only
            again = await ok(p, "GET", "/staff/me", "staff-1")
            payouts = next(d for d in again["duties"] if d["sheet"] == "payouts")
            assert payouts["acknowledged"] and payouts["acknowledged_version"] == 1
            assert "payouts" not in {t["ref"] for t in again["todo"]}
            owner = await p.call("POST", "/staff/me/duties/owner/ack", "staff-1", None, STAFF)
            assert owner.status_code == 422  # not this login's sheet

            # notifications reach the holders of a permission, until acknowledged or closed
            first = await p.desk.notify(
                "news.desk", "news_source", "news-source:bls", "Not read: bls", "add", now, True
            )
            await p.desk.notify("payout.authorise", "test", "PO00000001", "Authorise", "", now)
            seen = [n["id"] for n in await ok(p, "GET", "/staff/notifications", "staff-1")]
            assert seen == [first]  # staff (T2) hold news.desk, not payout.authorise
            admin = await ok(p, "GET", "/staff/notifications", "admin-1", roles=ADMIN)
            assert len(admin) == 2
            trader = await p.call("GET", "/staff/notifications", "u1")
            assert trader.status_code == 403
            await ok(p, "POST", f"/staff/notifications/{first}/ack", "staff-1")
            assert await ok(p, "GET", "/staff/notifications", "staff-1") == []
            done = await ok(p, "GET", "/staff/notifications?all=true", "staff-1")
            assert done[0]["acknowledged_at"] is not None
            assert len(await ok(p, "GET", "/staff/notifications", "staff-2")) == 1
            assert await p.desk.close("news-source:bls", "source read again", now) == 1
            assert await ok(p, "GET", "/staff/notifications", "staff-2") == []
            unknown = await p.call("POST", "/staff/notifications/999/ack", "staff-1", None, STAFF)
            assert unknown.status_code == 404

            # messages: a thread on one record, append-only
            url = "/staff/threads/CAS00000001"
            await ok(p, "POST", url, "staff-1", {"body": "  Checking the IP overlap.  "})
            await ok(p, "POST", url, "admin-1", {"body": "Agreed, same device."}, ADMIN)
            thread = await ok(p, "GET", url, "staff-2")
            assert [(m["subject"], m["body"]) for m in thread] == [
                ("staff-1", "Checking the IP overlap."),
                ("admin-1", "Agreed, same device."),
            ]
            assert (await ok(p, "GET", "/staff/threads", "staff-2"))[0]["ref"] == "CAS00000001"
            bad = await p.call(
                "POST", "/staff/threads/XYZ00000001", "staff-1", {"body": "x"}, STAFF
            )
            assert bad.status_code == 422
            assert (await p.call("GET", url, "u1")).status_code == 403  # traders never

            # the to-do list: a grant waiting for this owner's approval
            body = {"subject": "kc-new", "name": "New", "kind": "staff", "tier": 1,
                    "reason": "hired"}  # fmt: skip
            joined = await ok(p, "POST", "/staff/members", "kc-owner-a", body, ())
            grt = joined["grants"][0]["grt"]
            todo = await ok(p, "GET", "/staff/me/todo", "kc-owner-b", roles=())
            assert ("grant", grt) in {(t["kind"], t["ref"]) for t in todo}
            mine = await ok(p, "GET", "/staff/me/todo", "kc-owner-a", roles=())
            assert grt not in {t["ref"] for t in mine}  # never one's own request

    asyncio.run(scenario())
