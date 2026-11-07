"""The back office's customer and account lists, and employees' own to-do
notes [7 Nov 2026]."""

import asyncio

import pytest

from propfirm.service.feeder import now_ms
from propfirm.staff.directory import Owner
from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()


def test_staff_list_customers_and_accounts(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, owners=OWNERS) as p:
            assert (await p.call("POST", "/me", body=ADULT)).status_code == 201
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            acc = (await p.call("POST", order["pay_url"])).json()["acc"]
            other = {**ADULT, "full_name": "Zoe Other"}
            assert (await p.call("POST", "/me", "u2", body=other)).status_code == 201

            listed = await p.call("GET", "/staff/customers", "kc-owner-a", None, NONE)
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            assert [(r["cif"], r["accounts"]) for r in rows] == [
                ("CIF00000002", 0),
                ("CIF00000001", 1),
            ]
            found = await p.call("GET", "/staff/customers?q=zoe", "kc-owner-a", None, NONE)
            assert [r["full_name"] for r in found.json()] == ["Zoe Other"]
            wild = await p.call("GET", "/staff/customers?q=%25", "kc-owner-a", None, NONE)
            assert wild.json() == []  # a % is text, not a wildcard

            one = (
                await p.call("GET", "/staff/customers/CIF00000001", "kc-owner-a", None, NONE)
            ).json()
            assert [a["acc"] for a in one["account_list"]] == [acc]
            assert one["orders"][0]["ord"] == order["ord"]

            accounts = (await p.call("GET", "/staff/accounts", "kc-owner-a", None, NONE)).json()
            assert [(a["acc"], a["stage"], a["cif"]) for a in accounts] == [
                (acc, "s1", "CIF00000001")
            ]
            assert accounts[0]["full_name"] == ADULT["full_name"]
            funded = await p.call("GET", "/staff/accounts?stage=funded", "kc-owner-a", None, NONE)
            assert funded.json() == []
            # traders never see the lists
            assert (await p.call("GET", "/staff/customers")).status_code == 403
            assert (await p.call("GET", "/staff/accounts")).status_code == 403

    asyncio.run(scenario())


def test_employees_keep_their_own_notes(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        rig = platform(database_url, valkey_url, pg_schema, start_engine=False, owners=OWNERS)
        async with rig as p:
            due = now_ms() + 86_400_000
            note = {"text": "Call the auditor", "due_at": due}
            added = await p.call("POST", "/staff/me/notes", "kc-owner-a", note, NONE)
            assert added.status_code == 201, added.text
            note_id = added.json()["id"]
            todo = (await p.call("GET", "/staff/me", "kc-owner-a", None, NONE)).json()["todo"]
            mine = [t for t in todo if t["kind"] == "note"]
            assert [(t["ref"], t["title"], t["due_at"]) for t in mine] == [
                (str(note_id), "Call the auditor", due)
            ]
            # another employee neither sees nor ticks it
            theirs = (await p.call("GET", "/staff/me", "kc-owner-b", None, NONE)).json()["todo"]
            assert all(t["kind"] != "note" for t in theirs)
            assert (
                await p.call("POST", f"/staff/me/notes/{note_id}/done", "kc-owner-b", None, NONE)
            ).status_code == 404
            done = await p.call("POST", f"/staff/me/notes/{note_id}/done", "kc-owner-a", None, NONE)
            assert done.status_code == 200
            assert (
                await p.call("POST", f"/staff/me/notes/{note_id}/done", "kc-owner-a", None, NONE)
            ).status_code == 404
            todo = (await p.call("GET", "/staff/me", "kc-owner-a", None, NONE)).json()["todo"]
            assert all(t["kind"] != "note" for t in todo)
            past = await p.call(
                "POST", "/staff/me/notes", "kc-owner-a", {"text": "x", "due_at": 1}, NONE
            )
            assert past.status_code == 422
            assert (
                await p.call("POST", "/staff/me/notes", "kc-owner-a", {"text": "  "}, NONE)
            ).status_code == 422
            assert (
                await p.call("POST", "/staff/me/notes", body={"text": "trader"})
            ).status_code == 403

    asyncio.run(scenario())
