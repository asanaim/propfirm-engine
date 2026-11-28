"""The cohort alarm through real records (D-14.33): a referred Lite
challenge passes both stages; with a tiny bound its product and affiliate
cohorts alarm once, hold the cohort's payouts and pause the affiliate's
commission, open a case on the affiliate, and staff clear them."""

import asyncio
from dataclasses import replace
from fractions import Fraction

import asyncpg
import pytest

from propfirm.customers.cohorts import month_of
from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_corrections import project
from tests.integration.test_customers import ADULT, LITE_10K, platform
from tests.integration.test_progression import Market, quick_lite

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_cohort_alarm(database_url: str, valkey_url: str, pg_schema: str) -> None:
    base = quick_lite()
    cohorts = replace(
        base.cohorts,
        min_resolved=1,
        significance=Fraction(1, 2),
        bounds={**base.cohorts.bounds, "lite": Fraction(1, 100)},
    )
    policy = replace(base, cohorts=cohorts)

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, policy) as p:
            alarms = p.cohorts
            assert alarms is not None and p.referrals is not None
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", "ref", {**ADULT, "full_name": "Affiliate"})
            code = (await p.call("GET", "/me/referrals", "ref")).json()["code"]
            await p.call("POST", "/me", "u1", {**ADULT, "ref": code})
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            s1 = (await p.call("POST", order["pay_url"], "u1")).json()["acc"]

            # not resolved yet: nothing to count
            await project(p, database_url)
            assert await alarms.results() == []

            await market.pass_stage(s1)
            assert await p.lifecycle.poll(now_ms()) == ["PRG00000001"]
            assert await p.lifecycle.advance(now_ms()) == 1
            await market.pass_stage("ACC00000002")
            await project(p, database_url)

            (result,) = await alarms.results()
            assert (result.product_id, result.passed, result.referrer) == (
                "lite",
                True,
                "CIF00000001",
            )
            raised = await alarms.watch(now_ms())
            assert sorted((t.kind, t.key) for t in raised) == [
                ("affiliate", "CIF00000001:lite"),
                ("product", f"lite:{month_of(result.bought_at)}"),
            ]
            assert await alarms.watch(now_ms()) == []  # every six hours at most
            later = now_ms() + 7 * 3_600_000
            assert await alarms.watch(later) == []  # still open: not raised again

            assert await alarms.held(s1) and await alarms.held("ACC00000002")
            # the affiliate's commission is paused while the alarm is open
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 0

            seen = (await p.call("GET", "/staff/cohorts", "admin-1", None, ADMIN)).json()
            lite = next(c for c in seen["cohorts"] if c["kind"] == "product")
            assert (lite["resolved"], lite["passed"], lite["rate"], lite["bound"]) == (
                1,
                1,
                "100.0 %",
                "1.0 %",
            )
            assert lite["alarmed"] and len(seen["alarms"]) == 2

            conn = await asyncpg.connect(database_url)
            try:
                alert = await conn.fetchrow(
                    f"SELECT cif, kind, severity FROM {p.config.customer_schema}.alerts "
                    f"WHERE source = 'cohort'"
                )
            finally:
                await conn.close()
            assert alert is not None
            assert tuple(alert) == ("CIF00000001", "fraud", "high")

            for a in seen["alarms"]:
                url = f"/staff/cohorts/{a['id']}/clear"
                body = {"reason": "a strong trader; reviewed"}
                asked = await p.call("POST", url, "admin-1", body, ADMIN)
                assert asked.json() == {"alarm": a["id"], "status": "requested"}, asked.text
                assert await alarms.held(s1)  # one person is not enough
                same = await p.call("POST", url, "admin-1", body, ADMIN)
                assert (same.status_code, same.json()["detail"]["code"]) == (409, "same_person")
                done = await p.call("POST", url, "admin-2", {"reason": "agreed"}, ADMIN)
                assert done.json() == {"alarm": a["id"], "status": "cleared"}, done.text
                again = await p.call("POST", url, "admin-2", body, ADMIN)
                assert again.status_code == 404
            listed = (await p.call("GET", "/staff/cohorts", "admin-1", None, ADMIN)).json()
            assert {(a["requested_by"], a["cleared_by"]) for a in listed["alarms"]} == {
                ("admin-1", "admin-2")
            }
            assert not await alarms.held(s1)
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 1  # resumes

    asyncio.run(scenario())


def test_an_instant_lock_inside_the_day_counts_at_once(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            alarms = p.cohorts
            assert alarms is not None
            acc = await funded_instant(p)  # +300 USD on 5,000: the 5 % trail locks
            await project(p, database_url)
            view = await p.live.account(acc)
            assert view is not None and view["max_floor_cents"] >= 500_000
            (result,) = await alarms.results()  # no daily reset since: the view says so
            assert (result.product_id, result.passed) == ("instant", True)

    asyncio.run(scenario())
