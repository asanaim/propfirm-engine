"""Automatic risk limits end to end (D-14.34): a class set on the platform
reaches the engine as one journaled input with the settings version; the
engine then refuses new exposure above the cut (class C) or any new
exposure (class D), still allows closes, and nothing is sent again while
nothing changes."""

import asyncio
import json

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_risk_limits_reach_the_engine(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            sync = p.risk
            assert sync is not None
            await Market(p).price(110_000)
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            acc = (await p.call("POST", order["pay_url"], "u1")).json()["acc"]
            await eventually(lambda: p.live.account(acc))

            async def place(side: str, volume: int) -> dict[str, object]:
                body = {"symbol": "EUR/USD", "side": side, "type": "market", "volume": volume}
                done = await p.call("POST", f"/accounts/{acc}/orders", "u1", body)
                return dict(done.json())

            def reason(done: dict[str, object]) -> object:
                events = done["events"]
                assert isinstance(events, list)
                return next((e.get("reason") for e in events if e["type"] == "OrderRejected"), None)

            # the first sync sends the empty set once; the next sends nothing
            assert await sync.sync(now_ms(), every_ms=0)
            assert not await sync.sync(now_ms(), every_ms=0)

            # class C: lot caps halved ($10,000: 5 lots per order, so 2.5)
            put = await p.call(
                "PUT",
                f"/staff/accounts/{acc}/class",
                "admin-1",
                {"class": "C", "reason": "test: watch"},
                ADMIN,
            )
            assert put.status_code == 200, put.text
            assert await sync.sync(now_ms(), every_ms=0)
            assert reason(await place("buy", 251)) == "risk_limit"
            opened = await place("buy", 250)
            assert reason(opened) is None
            events = opened["events"]
            assert isinstance(events, list)
            position = next(e["position_id"] for e in events if e["type"] == "PositionOpened")

            # class D: close only; the open position still closes
            await p.call(
                "PUT",
                f"/staff/accounts/{acc}/class",
                "admin-1",
                {"class": "D", "reason": "test: restricted"},
                ADMIN,
            )
            assert await sync.sync(now_ms(), every_ms=0)
            assert reason(await place("sell", 1)) == "close_only"
            closed = await p.call("POST", f"/accounts/{acc}/positions/{position}/close", "u1", {})
            assert any(e["type"] == "PositionClosed" for e in closed.json()["events"])

            # every set is in the journal with the settings version
            conn = await asyncpg.connect(database_url)
            try:
                rows = await conn.fetch(
                    f"SELECT input FROM {p.config.schema}.journal WHERE kind = 'SetRiskLimits' "
                    f"ORDER BY seq"
                )
            finally:
                await conn.close()
            sent = [json.loads(r["input"]) for r in rows]
            assert len(sent) == 3
            last = sent[-1]["limits"]
            assert last["policy_version"] == 0
            assert last["classes"] == [[acc, "D"]]

    asyncio.run(scenario())
