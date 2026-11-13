"""The trader directory (rule AC19) against PostgreSQL, through the API."""

import asyncio

import asyncpg
import pytest

from propfirm.customers.directory import TraderDirectory
from tests.integration.test_cases import alert, staff
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform

pytestmark = pytest.mark.integration


def test_traders_by_status_and_one_traders_file(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def go() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            await p.engine()
            await p.call("POST", "/me", body=ADULT)  # u1 buys a challenge
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            await p.call("POST", order["pay_url"])
            await eventually(lambda: p.store.account("ACC00000001"))
            other = {**ADULT, "full_name": "Bo Newcomer"}
            await p.call("POST", "/me", "u2", body=other)  # u2 registers only

            listed = (await staff(p, "GET", "/staff/traders")).json()
            counts = listed["counts"]
            assert (counts["all"], counts["trading"], counts["no_account"]) == (2, 1, 1)
            assert (counts["funded"], counts["open_case"], counts["blocked"]) == (0, 0, 0)
            assert [t["full_name"] for t in listed["traders"]] == ["Bo Newcomer", "Ada Trader"]
            # whole numbers reach the screen as JSON numbers: a SQL sum is
            # numeric, which the API would send as text and the back office's
            # money formatter refuses (the Traders page crashed, 13 Nov)
            for t in listed["traders"]:
                for key in ("accounts", "funded", "paid_cents", "created_at"):
                    assert type(t[key]) is int, (key, t[key])

            trading = (await staff(p, "GET", "/staff/traders?segment=trading")).json()
            assert [t["cif"] for t in trading["traders"]] == ["CIF00000001"]
            found = (await staff(p, "GET", "/staff/traders?q=newcom")).json()["traders"]
            assert [t["cif"] for t in found] == ["CIF00000002"]
            # an apprentice's search matches a CIF only, never a masked field
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
            try:
                d = TraderDirectory(pool, p.config.customer_schema)
                assert await d.traders(q="newcom", cif_only=True) == []
                cif2 = await d.traders(q="cif00000002", cif_only=True)
                assert [t["cif"] for t in cif2] == ["CIF00000002"]
            finally:
                await pool.close()

            await alert(p)  # a case on CIF00000001
            cases = (await staff(p, "GET", "/staff/traders?segment=open_case")).json()
            assert [t["cif"] for t in cases["traders"]] == ["CIF00000001"]

            file = (await staff(p, "GET", "/staff/traders/CIF00000001")).json()
            assert file["trader"]["accounts"] == 1
            assert type(file["trader"]["paid_cents"]) is int
            assert [a["acc"] for a in file["accounts"]] == ["ACC00000001"]
            assert file["orders"][0]["status"] == "provisioned"
            assert file["cases"][0]["status"] == "open"
            assert {e["kind"] for e in file["events"]} >= {"registered"}

            assert (await staff(p, "GET", "/staff/traders/CIF00000099")).status_code == 404
            assert (await p.call("GET", "/staff/traders")).status_code == 403  # traders never

    asyncio.run(go())
