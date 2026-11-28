"""Stale price through real records (D-14.36): five market buys fill below
the reference ask by more than its spread; the check keeps each as
evidence, raises one high fraud alert (a case holds payouts) and sets class
C. A connector stores reference quotes in a 10 minute window in Valkey."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import asyncpg
import pytest
import valkey.asyncio as avalkey

from propfirm.market.oanda import Quote
from propfirm.service.feeder import now_ms
from propfirm.service.reference import Connector, ReferenceReader, purge
from tests.integration.test_corrections import project
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


@contextlib.asynccontextmanager
async def no_stream() -> AsyncIterator[AsyncIterator[str]]:
    raise AssertionError("not connected in this test")
    yield  # pragma: no cover


def test_stale_fills(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            check = p.stale
            assert check is not None
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            acc = (await p.call("POST", order["pay_url"], "u1")).json()["acc"]
            await eventually(lambda: p.live.account(acc))

            fills: list[tuple[int, int]] = []  # (fill time, price) of each open
            for _ in range(5):
                body = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 1}
                done = (await p.call("POST", f"/accounts/{acc}/orders", "u1", body)).json()
                opened = next(e for e in done["events"] if e["type"] == "PositionOpened")
                fills.append((opened["ts"], opened["price"]))
                await p.call(
                    "POST", f"/accounts/{acc}/positions/{opened['position_id']}/close", "u1", {}
                )

            # the reference saw the market 50 points higher, spread 10
            prefix = f"{p.config.live_prefix}:reference"
            client = avalkey.Valkey.from_url(valkey_url)
            connector = Connector(client, no_stream, prefix)
            quotes = [Quote("EUR/USD", ts, price + 40, price + 50) for ts, price in fills]
            assert len({ts for ts, _ in fills}) == 5
            assert await connector.store(quotes, now_ms()) == 5
            reader = ReferenceReader(client, prefix)
            ts0, price0 = fills[0]
            assert await reader.at("EUR/USD", ts0, 2_000) == (ts0, price0 + 40, price0 + 50)
            assert await reader.at("EUR/USD", ts0 - 1, 2_000) is None

            await project(p, database_url)
            later = now_ms() + 6_000  # past the settling time
            assert await check.check(later, every_ms=0) == 5
            assert await check.check(later + 1, every_ms=0) == 0  # the cursor moved on

            evidence = (
                await p.call("GET", f"/staff/accounts/{acc}/stale-fills", "admin-1", None, ADMIN)
            ).json()
            assert len(evidence) == 5
            assert {e["advantage"] for e in evidence} == {50}

            conn = await asyncpg.connect(database_url)
            s = p.config.customer_schema
            try:
                alerts = await conn.fetch(
                    f"SELECT severity, kind FROM {s}.alerts "
                    f"WHERE detail->>'detector' = 'stale_price'"
                )
                assert [(a["severity"], a["kind"]) for a in alerts] == [("high", "fraud")]
                cls = await conn.fetchrow(
                    f"SELECT class, set_by FROM {s}.account_class_changes WHERE acc = $1 "
                    f"ORDER BY at DESC LIMIT 1",
                    acc,
                )
                assert cls is not None and tuple(cls) == ("C", "system")

                # the purge leaves no reference price behind
                pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
                try:
                    done = await purge(client, pool, s, prefix)
                finally:
                    await pool.close()
                assert done == {"valkey_keys": 1, "stale_fills": 5}
                assert await reader.at("EUR/USD", ts0, 2_000) is None
                with pytest.raises(ValueError):
                    await purge(client, conn, "bad;schema", prefix)
            finally:
                await conn.close()
                await client.aclose()

    asyncio.run(scenario())
