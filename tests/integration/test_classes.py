"""Daily trader classes through real records (D-14.28): the job classes
funded accounts into `account_class_changes`, respects a class set by
staff, and a class C customer cannot buy Instant."""

import asyncio

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_corrections import project
from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
INSTANT = {"product_id": "instant", "size_cents": 500_000}


def test_daily_classes(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            jobs = p.daily_classes
            assert jobs is not None
            acc = await funded_instant(p)  # u1, CIF00000001
            await p.call("POST", "/me", "u2", {**ADULT, "full_name": "Second"})

            await project(p, database_url)
            # the one trade was a short hold for all the profit: class C
            t0 = now_ms()
            assert await jobs.run(t0) == 1
            listed = (await p.call("GET", "/staff/classes", "admin-1", None, ADMIN)).json()
            assert listed["counts"] == {"A": 0, "B": 0, "C": 1, "D": 0}
            assert listed["accounts"][0]["reason"].startswith("daily: short_holds; ")

            # a trades link between u1 and u2: a second reason, recorded
            conn = await asyncpg.connect(database_url)
            try:
                await conn.execute(
                    f"INSERT INTO {p.config.customer_schema}.cluster_links "
                    f"(a_cif, b_cif, reason, evidence, first_seen, last_seen) "
                    f"VALUES ('CIF00000001', 'CIF00000002', 'trades', '{{}}', $1, $1)",
                    now_ms(),
                )
            finally:
                await conn.close()
            assert await jobs.run(t0 + 2) == 0  # once a day
            assert await jobs.run(now_ms(), every_ms=0) == 1
            listed = (await p.call("GET", "/staff/classes", "admin-1", None, ADMIN)).json()
            assert listed["counts"] == {"A": 0, "B": 0, "C": 1, "D": 0}
            (row,) = listed["accounts"]
            assert (row["acc"], row["class"], row["set_by"]) == (acc, "C", "system")
            assert row["reason"].startswith("daily: short_holds,trades_cluster; ")
            assert '"trades": 1' in row["reason"]

            # C: no Instant purchase; an evaluation is still sold
            refused = await p.call("POST", "/orders", "u1", INSTANT)
            assert (refused.status_code, refused.json()["detail"]["code"]) == (
                403,
                "instant_not_offered",
            )
            assert (await p.call("POST", "/orders", "u1", LITE_10K)).status_code == 201
            # u2 has no class C account: Instant is sold
            assert (await p.call("POST", "/orders", "u2", INSTANT)).status_code == 201

            # staff weigh the link and set the account back to B: the next run keeps it
            back = await p.call(
                "PUT",
                f"/staff/accounts/{acc}/class",
                "admin-1",
                {"class": "B", "reason": "reviewed: a shared signal group"},
                ADMIN,
            )
            assert back.status_code == 200, back.text
            assert await jobs.run(now_ms(), every_ms=0) == 0
            seen = (
                await p.call("GET", f"/staff/accounts/{acc}/class", "admin-1", None, ADMIN)
            ).json()
            assert seen["class"] == "B"
            assert [(h["class"], h["set_by"]) for h in seen["history"]] == [
                ("C", "system"),
                ("C", "system"),
                ("B", "admin-1"),
            ]
            unclassed = (
                await p.call("GET", "/staff/accounts/ACC00000009/class", "admin-1", None, ADMIN)
            ).json()
            assert unclassed == {"class": "B", "history": []}

    asyncio.run(scenario())
