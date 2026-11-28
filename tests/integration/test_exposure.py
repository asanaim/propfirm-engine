"""The exposure desk through real records (D-14.32): an open funded
position is revalued under its scenarios against the free reserve; a
breach records a lot cap cut on the winning side with the settings
version, shown to staff, and is not recorded twice."""

import asyncio

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_corrections import project
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_exposure_desk(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            desk = p.exposure
            assert desk is not None
            acc = await funded_instant(p)
            await project(p, database_url)
            # nothing open: nothing to revalue
            empty = (await p.call("GET", "/staff/exposure", "admin-1", None, ADMIN)).json()
            assert empty["groups"] == [] and empty["accounts"] == 0

            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
            opened = await p.call("POST", f"/accounts/{acc}/orders", "u1", trade)
            assert opened.json()["status"] == "done"

            seen = (await p.call("GET", "/staff/exposure", "admin-1", None, ADMIN)).json()
            assert seen["accounts"] == 1
            by_name = {g["name"]: g for g in seen["groups"]}
            eur = by_name["EUR/USD"]
            # no daily history in a fresh rig: the forex stress move, upward
            assert eur["scenario"] == "EUR/USD +stress" and eur["loss_cents"] > 0
            assert "USD" in by_name  # the USD bloc moves EUR/USD too
            free = seen["free_reserve_cents"]
            assert free is not None and eur["limit_cents"] == free * 25 // 100
            # one Instant sale's sweep cannot carry a 1 lot position at 80 %
            assert eur["level"] == "quarter"

            events = await desk.watch(now_ms())
            assert ("EUR/USD", "buy", "raise") in events
            assert all(side == "buy" for symbol, side, _ in events if symbol == "EUR/USD")
            assert await desk.watch(now_ms()) == []  # every five minutes at most

            after = (await p.call("GET", "/staff/exposure", "admin-1", None, ADMIN)).json()
            cut = next(c for c in after["cuts"] if c["symbol"] == "EUR/USD")
            assert (cut["side"], cut["level"], cut["classes"]) == (
                "buy",
                "quarter",
                ["A", "B", "C"],
            )
            move = next(m for m in after["moves"] if m["symbol"] == "EUR/USD")
            assert move["event"] == "raise" and move["policy_version"] == 0

    asyncio.run(scenario())
