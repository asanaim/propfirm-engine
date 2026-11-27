"""Request rate through the API (D-14.31): above the day's limit order
requests are refused with 429; the second throttled day in 30 days raises
an alert, which opens a case."""

import asyncio
import json
from dataclasses import replace

import asyncpg
import pytest

from propfirm.policy import load_policy
from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform

pytestmark = pytest.mark.integration

DAY = 86_400_000


def test_request_rate(database_url: str, valkey_url: str, pg_schema: str) -> None:
    base = load_policy()
    policy = replace(base, detectors=replace(base.detectors, rate_orders_per_day=2))
    offset = [0]

    def clock() -> int:
        return now_ms() + offset[0]

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, policy, clock=clock) as p:
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            await p.call("POST", order["pay_url"], "u1")
            acc = "ACC00000001"
            await eventually(lambda: p.live.account(acc))

            async def cancel() -> int:
                r = await p.call("DELETE", f"/accounts/{acc}/orders/NOPE", "u1")
                return r.status_code

            async def alerts(conn: asyncpg.Connection) -> list[asyncpg.Record]:
                return await conn.fetch(
                    f"SELECT severity, kind, detail::text AS detail FROM "
                    f"{p.config.customer_schema}.alerts WHERE source = 'detector'"
                )

            assert 429 not in (await cancel(), await cancel())
            refused = await p.call("DELETE", f"/accounts/{acc}/orders/NOPE", "u1")
            assert refused.status_code == 429
            assert refused.json()["detail"]["code"] == "request_rate"
            assert await cancel() == 429  # the rest of the day too
            # other routes are not counted
            assert (await p.call("GET", f"/accounts/{acc}", "u1")).status_code == 200

            conn = await asyncpg.connect(database_url)
            try:
                days = await conn.fetch(
                    f"SELECT acc, requests FROM {p.config.customer_schema}.rate_days"
                )
                assert [(d["acc"], d["requests"]) for d in days] == [(acc, 3)]
                assert await alerts(conn) == []  # once is not a case

                offset[0] = DAY  # the next trading day: counted afresh
                assert 429 not in (await cancel(), await cancel())
                assert await cancel() == 429
                (alert,) = await alerts(conn)
                assert (alert["severity"], alert["kind"]) == ("medium", "integrity")
                detail = json.loads(alert["detail"])
                assert detail["detector"] == "request_rate" and len(detail["days"]) == 2
            finally:
                await conn.close()

    asyncio.run(scenario())
