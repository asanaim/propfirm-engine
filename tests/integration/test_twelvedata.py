"""The Twelve Data connector against a local WebSocket server and Valkey, and
the licensed-data purge against PostgreSQL and Valkey [P2 step 6]."""

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import pytest
import valkey.asyncio as avalkey
from websockets.asyncio.server import ServerConnection, serve

from propfirm.journal.postgres import JournalLockedError, PostgresJournal
from propfirm.journal.stream import InputStreamWriter, decode_input
from propfirm.market.twelvedata import SymbolMap
from propfirm.market.types import PriceUpdate
from propfirm.service.config import ServiceConfig
from propfirm.service.projector import Projector
from propfirm.service.purge import PurgeRefused, purge
from propfirm.service.twelvedata import Connector, connector_url, websocket

pytestmark = pytest.mark.integration

T = 1_762_400_000
KEY = "test-key-not-real"


def test_the_connector_streams_prices_into_the_engines_input_stream(valkey_url: str) -> None:
    stream = f"test:inputs:{uuid.uuid4().hex}"
    seen_paths: list[str] = []
    received: list[Any] = []

    async def handler(ws: ServerConnection) -> None:
        assert ws.request is not None
        seen_paths.append(ws.request.path)
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"event": "subscribe-status", "status": "ok",
                                  "success": [{"symbol": "EUR/USD"}, {"symbol": "XAU/USD"}],
                                  "fails": []}))  # fmt: skip
        for n, (symbol, value) in enumerate(
            [("EUR/USD", "1.08215"), ("XAU/USD", "2650.4"), ("EUR/USD", "1.08216")]
        ):
            await ws.send(f'{{"event":"price","symbol":"{symbol}","timestamp":{T + n},'
                          f'"price":{value}}}')  # fmt: skip
        await ws.wait_closed()

    async def run_once(client: avalkey.Valkey, port: int, until: int) -> None:
        url = connector_url(KEY, f"ws://127.0.0.1:{port}/v1/quotes/price")
        m = SymbolMap({"EUR/USD": "EUR/USD", "XAU/USD": "XAU/USD"})
        c = Connector(client, stream, m, websocket(url))
        stop = asyncio.Event()
        task = asyncio.create_task(c.run(stop))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if await client.xlen(stream) >= until:
                break
        stop.set()
        await asyncio.wait_for(task, 10)

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        try:
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                await run_once(client, port, 3)
                await run_once(client, port, 6)  # a restart: numbers continue
            entries = await client.xrange(stream)
            updates = [decode_input(fields) for _, fields in entries]
            assert all(isinstance(u, PriceUpdate) for u in updates)
            got = [(u.symbol, u.seq, u.price, u.source_ts) for u in updates]  # type: ignore[union-attr]
            assert got == [
                ("EUR/USD", 1, 108215, T * 1000),
                ("XAU/USD", 1, 265040, (T + 1) * 1000),
                ("EUR/USD", 2, 108216, (T + 2) * 1000),
                ("EUR/USD", 3, 108215, T * 1000),
                ("XAU/USD", 2, 265040, (T + 1) * 1000),
                ("EUR/USD", 4, 108216, (T + 2) * 1000),
            ]
            assert seen_paths[0] == f"/v1/quotes/price?apikey={KEY}"
            assert received[0] == {"action": "subscribe",
                                   "params": {"symbols": "EUR/USD,XAU/USD"}}  # fmt: skip
        finally:
            await client.delete(stream, f"{stream}:seq:twelve_data")
            keys = [k async for k in client.scan_iter(match=f"{stream}:trial:*")]
            if keys:
                await client.delete(*keys)
            await client.aclose()

    asyncio.run(scenario())


def test_the_purge_waits_for_the_engine_then_removes_licensed_prices(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    read = f"{pg_schema}_read"
    stream = f"test:inputs:{uuid.uuid4().hex}"
    config = ServiceConfig(database_url=database_url, valkey_url=valkey_url, schema=pg_schema,
                           read_schema=read, stream=stream, group="engine")  # fmt: skip
    prefix = config.live_prefix

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        try:
            await InputStreamWriter(client, stream).publish(
                PriceUpdate("EUR/USD", 1, T * 1000, T * 1000, 108215)
            )
            await client.set(f"{prefix}:head", "{}")
            await client.xgroup_create(stream, "engine", id="0")
            with pytest.raises(PurgeRefused):  # the engine has not read it yet
                await purge(config)
            [[_, entries]] = await client.xreadgroup("engine", "e1", {stream: ">"})
            with pytest.raises(PurgeRefused):  # read, not acknowledged
                await purge(config)
            await client.xack(stream, "engine", entries[0][0])

            journal = await PostgresJournal.connect(database_url, pg_schema)
            await journal.close()  # creates the schema
            projector = await Projector.connect(database_url, pg_schema, read)
            await projector.close()
            out = await purge(config)
            assert out["stream"] == 1
            assert set(out["journal"]) == {"prices", "price_keys", "snapshots"}
            assert out["projections"] == {"bars": 0, "account_events": 0}
            assert out["live"] == 1
            assert await client.xlen(stream) == 0
            assert await client.exists(f"{prefix}:head") == 0

            # a running engine holds the journal: nothing is deleted, the
            # stream included
            await InputStreamWriter(client, stream).publish(
                PriceUpdate("EUR/USD", 2, T * 1000, T * 1000, 108215)
            )
            [[_, entries]] = await client.xreadgroup("engine", "e1", {stream: ">"})
            await client.xack(stream, "engine", entries[0][0])
            running = await PostgresJournal.connect(database_url, pg_schema)
            try:
                with pytest.raises(JournalLockedError):
                    await purge(config)
            finally:
                await running.close()
            assert await client.xlen(stream) == 1  # untouched
        finally:
            await client.delete(stream)
            await client.aclose()
            conn = await asyncpg.connect(database_url)
            try:
                await conn.execute(f"DROP SCHEMA IF EXISTS {read} CASCADE")
            finally:
                await conn.close()

    asyncio.run(scenario())
