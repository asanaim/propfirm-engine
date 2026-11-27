"""Hedging across accounts through real records (D-14.29): three hedged
pairs on two accounts of one customer raise one high fraud alert on each
account, on the customer's case, once."""

import asyncio
import json

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_corrections import project
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration


def test_hedging_across_accounts(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            jobs = p.detectors
            assert jobs is not None
            await Market(p).price(110_000)
            await p.call("POST", "/me", "u1", ADULT)
            accounts = []
            for _ in range(2):
                order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
                await p.call("POST", order["pay_url"], "u1")
            for acc in ("ACC00000001", "ACC00000002"):
                await eventually(lambda: p.live.account(acc))  # noqa: B023 awaited at once
                accounts.append(acc)

            async def open_(acc: str, side: str) -> str:
                body = {"symbol": "EUR/USD", "side": side, "type": "market", "volume": 10}
                done = (await p.call("POST", f"/accounts/{acc}/orders", "u1", body)).json()
                assert done["status"] == "done", done
                (pid,) = [e["position_id"] for e in done["events"] if e["type"] == "PositionOpened"]
                return str(pid)

            async def close(acc: str, pid: str) -> None:
                path = f"/accounts/{acc}/positions/{pid}/close"
                assert (await p.call("POST", path, "u1", {})).status_code == 200

            a, b = accounts
            for i in range(3):
                pa = await open_(a, "buy")
                pb = await open_(b, "sell")
                await close(b, pb)  # b inside a: open together all of b's hold
                await close(a, pa)
                await project(p, database_url)
                if i == 1:  # two pairs: not yet
                    assert await jobs.run(now_ms(), every_ms=0) == 0

            assert await jobs.run(now_ms(), every_ms=0) == 2
            assert await jobs.run(now_ms(), every_ms=0) == 0  # raised once

            conn = await asyncpg.connect(database_url)
            try:
                rows = await conn.fetch(
                    f"SELECT acc, cas, source, kind, severity, detail::text AS detail "
                    f"FROM {p.config.customer_schema}.alerts WHERE source = 'detector' "
                    f"ORDER BY acc"
                )
            finally:
                await conn.close()
            assert [(r["acc"], r["kind"], r["severity"]) for r in rows] == [
                (a, "fraud", "high"),
                (b, "fraud", "high"),
            ]
            assert rows[0]["cas"] == rows[1]["cas"]  # one case for the customer
            detail = json.loads(rows[0]["detail"])
            assert detail["key"] == f"hedging:{b}" and detail["pairs"] == 3
            assert detail["with_customer"] == "CIF00000001"

    asyncio.run(scenario())
