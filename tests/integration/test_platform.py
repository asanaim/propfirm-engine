"""The engine as a service, end to end: inputs on Valkey, the engine process
journaling to PostgreSQL, live state in Valkey, history from the projector,
and the API on top. Then a restart: the same state comes back."""

import asyncio
import copy
import tomllib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fractions import Fraction
from typing import Any

import asyncpg
import httpx
import pytest
import valkey.asyncio as avalkey

from propfirm.api.app import Deps, create_app
from propfirm.journal.inputs import OpenAccount
from propfirm.journal.runner import Runner
from propfirm.journal.stream import InputStreamWriter
from propfirm.market.types import PriceUpdate
from propfirm.policy import DEFAULT_POLICY, Policy, PolicyError, load_policy, parse_policy
from propfirm.service.config import ServiceConfig
from propfirm.service.engine import run
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Gateway
from propfirm.service.live import LiveReader
from propfirm.service.projector import HistoryReader, Projector
from propfirm.service.setup import development_setup, setup_from_policy
from tests.auth_rig import FakeCustomers, bearer, verifier

pytestmark = pytest.mark.integration


@asynccontextmanager
async def engine(config: ServiceConfig) -> AsyncIterator[asyncio.Task[Runner]]:
    stop = asyncio.Event()
    task = asyncio.create_task(run(config, development_setup(), stop))
    try:
        yield task
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


async def eventually(check: Any, timeout: float = 5.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = await check()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def test_trade_through_the_api_and_restart(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    config = ServiceConfig(
        database_url=database_url,
        valkey_url=valkey_url,
        schema=pg_schema,
        read_schema=f"{pg_schema}_r",
        stream=f"test:inputs:{uuid.uuid4().hex}",
        block_ms=50,
        publish_ms=1,
    )

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
        writer = InputStreamWriter(client, config.stream)
        live = LiveReader(client, config.live_prefix)
        deps = Deps(
            live=live,
            history=HistoryReader(pool, config.read_schema),
            gateway=Gateway(writer, pool, config.schema),
            verifier=verifier(),
            customers=FakeCustomers(cifs={"u": "CIF00000001"}, owners={"A": "CIF00000001"}),
        )
        transport = httpx.ASGITransport(app=create_app(deps))
        api = httpx.AsyncClient(transport=transport, base_url="http://api", headers=bearer("u"))
        try:
            t0 = now_ms()
            async with engine(config):
                await writer.publish(OpenAccount("A", "lite-s1-v3", 10_000_000, t0))
                await writer.publish(PriceUpdate("EUR/USD", 1, t0, t0, 110_000))
                await eventually(lambda: live.account("A"))
                order = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
                opened = (await api.post("/accounts/A/orders", json=order)).json()
                assert opened["status"] == "done", opened
                assert types(opened["events"]) == ["PositionOpened"]
                position_id = opened["events"][0]["position_id"]

                async def has_position() -> bool:
                    data = (await api.get("/accounts/A")).json()
                    return [p["position_id"] for p in data["positions"]] == [position_id]

                await eventually(has_position)
                await writer.publish(PriceUpdate("EUR/USD", 2, t0 + 1, now_ms(), 110_105))
                closed = await api.post(f"/accounts/A/positions/{position_id}/close", json={})
                assert types(closed.json()["events"]) == ["PositionClosed"]
                fill = closed.json()["events"][0]
                pnl = fill["pnl_cents"]
                # EUR/USD, 1 lot: $1 a point, so cents = 100 x points (exit bid - entry ask)
                assert pnl == (fill["close_price"] - fill["open_price"]) * 100 != 0
                bad = {**order, "symbol": "XAU/USD"}  # no price yet: frozen
                refused = (await api.post("/accounts/A/orders", json=bad)).json()
                assert refused["status"] == "rejected"
                assert refused["events"][0]["reason"] == "symbol_frozen"

                async def settled() -> dict[str, Any] | None:
                    data = await live.account("A")
                    return data if data and not data["positions"] else None

                before = await eventually(settled)
                assert before["balance_cents"] == 10_000_000 + pnl

            projector = await Projector.connect(database_url, config.schema, config.read_schema)
            try:
                while await projector.run_once(limit=2):
                    pass
            finally:
                await projector.close()
            history = (await api.get("/accounts/A/history")).json()
            assert types(history) == [
                "AccountOpened",
                "PositionOpened",
                "PositionClosed",
                "OrderRejected",
            ]

            await client.delete(f"{config.live_prefix}:account:A")  # live state is only a cache
            async with engine(config):
                after = await eventually(lambda: live.account("A"))
            assert after == before  # rebuilt from the journal and republished
            assert (await api.get("/health")).json()["journal_seq"] == before["journal_seq"]
        finally:
            await api.aclose()
            async with pool.acquire() as conn:
                await conn.execute(f"DROP SCHEMA IF EXISTS {config.read_schema} CASCADE")
            await pool.close()
            for key in [config.stream] + [
                k async for k in client.scan_iter(f"{config.live_prefix}:*")
            ]:
                await client.delete(key)
            await client.aclose()

    asyncio.run(scenario())


def test_trailing_orders_work_through_the_api_and_survive_a_restart(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    config = ServiceConfig(
        database_url=database_url,
        valkey_url=valkey_url,
        schema=pg_schema,
        read_schema=f"{pg_schema}_r",
        stream=f"test:inputs:{uuid.uuid4().hex}",
        block_ms=50,
        publish_ms=1,
    )

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
        writer = InputStreamWriter(client, config.stream)
        live = LiveReader(client, config.live_prefix)
        ids = iter(["pos-1", "trail-1", "entry-1"])
        deps = Deps(
            live=live,
            history=HistoryReader(pool, config.read_schema),
            gateway=Gateway(writer, pool, config.schema),
            verifier=verifier(),
            customers=FakeCustomers(cifs={"u": "CIF00000001"}, owners={"A": "CIF00000001"}),
            new_id=lambda: next(ids),
        )
        transport = httpx.ASGITransport(app=create_app(deps))
        api = httpx.AsyncClient(transport=transport, base_url="http://api", headers=bearer("u"))
        try:
            t0 = now_ms()
            async with engine(config):
                await writer.publish(OpenAccount("A", "lite-s1-v3", 10_000_000, t0))
                await writer.publish(PriceUpdate("EUR/USD", 1, t0, t0, 110_000))
                await eventually(lambda: live.account("A"))
                order = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
                assert (await api.post("/accounts/A/orders", json=order)).json()["status"] == "done"
                stop = {"distance": 200, "step": 10}
                placed = (
                    await api.put("/accounts/A/positions/pos-1/trailing-stop", json=stop)
                ).json()
                assert types(placed["events"]) == ["TrailingPlaced"], placed
                level = placed["events"][0]["level"]
                entry = {
                    "symbol": "EUR/USD",
                    "side": "sell",
                    "volume": 10,
                    "distance": 300,
                    "step": 1,
                }
                working = (await api.post("/accounts/A/trailing-orders", json=entry)).json()
                assert types(working["events"]) == ["TrailingPlaced"], working
                await writer.publish(PriceUpdate("EUR/USD", 2, now_ms(), now_ms(), 110_500))

                async def moved() -> dict[str, Any] | None:
                    data = await live.account("A")
                    orders = data["trailing_orders"] if data else []
                    ok = len(orders) == 2 and all(o["moved_seq"] == 2 for o in orders)
                    return data if ok else None

                before = await eventually(moved)
                trail, sell = before["trailing_orders"]
                assert (trail["order_id"], trail["kind"], trail["position_id"]) == (
                    "trail-1",
                    "stop",
                    "pos-1",
                )
                assert trail["level"] > level and trail["moved_at"] > trail["placed_at"]
                assert (sell["order_id"], sell["kind"], sell["side"]) == (
                    "entry-1",
                    "entry",
                    "sell",
                )

            await client.delete(f"{config.live_prefix}:account:A")
            async with engine(config):
                after = await eventually(lambda: live.account("A"))
                assert after["trailing_orders"] == before["trailing_orders"]  # replayed
                await writer.publish(PriceUpdate("EUR/USD", 3, now_ms(), now_ms(), 109_000))
                # a 1.4 % drop with little history: the sanity filter (review
                # D9) holds it until the next update confirms the move
                await writer.publish(PriceUpdate("EUR/USD", 4, now_ms(), now_ms(), 108_995))

                async def flat() -> dict[str, Any] | None:
                    data = await live.account("A")
                    return data if data and data["trailing_orders"] == [] else None

                done = await eventually(flat)
                (position,) = done["positions"]  # the trailing sell filled; the buy closed
                assert (position["position_id"], position["side"]) == ("entry-1", "sell")

            projector = await Projector.connect(database_url, config.schema, config.read_schema)
            try:
                while await projector.run_once(limit=50):
                    pass
            finally:
                await projector.close()
            history = (await api.get("/accounts/A/history")).json()
            closes = [e for e in history if e["type"] == "PositionClosed"]
            assert [(e["position_id"], e["reason"]) for e in closes] == [("pos-1", "trailing_stop")]
            opens = [e for e in history if e["type"] == "PositionOpened"]
            assert [(e["position_id"], e["order_type"]) for e in opens] == [
                ("pos-1", "market"),
                ("entry-1", "trailing"),
            ]
            assert "TrailingMoved" in types(history)
        finally:
            await api.aclose()
            async with pool.acquire() as conn:
                await conn.execute(f"DROP SCHEMA IF EXISTS {config.read_schema} CASCADE")
            await pool.close()
            for key in [config.stream] + [
                k async for k in client.scan_iter(f"{config.live_prefix}:*")
            ]:
                await client.delete(key)
            await client.aclose()

    asyncio.run(scenario())


def test_projector_waits_for_the_journal_and_takes_one_lock(
    database_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        read = f"{pg_schema}_r"
        first = await Projector.connect(database_url, pg_schema, read)
        try:
            assert await first.run_once() == 0  # no journal yet
            with pytest.raises(RuntimeError):
                await Projector.connect(database_url, pg_schema, read)
        finally:
            await first.close()
            conn = await asyncpg.connect(database_url)
            await conn.execute(f"DROP SCHEMA IF EXISTS {read} CASCADE")
            await conn.close()

    asyncio.run(scenario())


def test_policy_edits_reach_the_engine_on_restart(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    config = ServiceConfig(
        database_url=database_url,
        valkey_url=valkey_url,
        schema=pg_schema,
        read_schema=f"{pg_schema}_r",
        stream=f"test:inputs:{uuid.uuid4().hex}",
        block_ms=50,
        publish_ms=1,
    )
    shipped = load_policy()
    raw = tomllib.loads(DEFAULT_POLICY.read_text("utf-8"))
    raw["products"][0]["stages"][0].update(version=4, daily_loss_percent="4")
    raw["trading"]["config_version"] = "trading-v8"
    raw["trading"]["leverage"]["metal"] = "25"
    edited = parse_policy(raw)
    same_version = copy.deepcopy(raw)
    same_version["products"][0]["stages"][0].update(version=3)

    async def life(policy: Policy) -> Runner:
        stop = asyncio.Event()
        task = asyncio.create_task(run(config, setup_from_policy(shipped), stop, policy))
        await asyncio.sleep(0.5)
        stop.set()
        return await asyncio.wait_for(task, timeout=10)

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        try:
            first = await life(shipped)
            assert first.machine.supervisor.engine.config_version == "trading-v7"
            second = await life(edited)  # the journal keeps its setup; edits arrive as inputs
            engine = second.machine.supervisor.engine
            assert "lite-s1-v3" in second.machine.rule_sets
            assert engine.config_version == "trading-v8"
            assert engine.instruments["XAU/USD"].leverage == 25
            assert second.machine.rule_sets["lite-s1-v3"].daily_loss == Fraction(3, 100)
            with pytest.raises(PolicyError, match="raise the stage's version"):
                await life(parse_policy(same_version))
        finally:
            for key in [config.stream] + [
                k async for k in client.scan_iter(f"{config.live_prefix}:*")
            ]:
                await client.delete(key)
            await client.aclose()

    asyncio.run(scenario())


def test_prices_become_bars(database_url: str, valkey_url: str, pg_schema: str) -> None:
    """One-minute bars from the journal's price updates (open and close in
    journal order, a minute spread over projector pages), and wider bars
    built from them."""
    from propfirm.rules.clock import NEW_YORK_17

    config = ServiceConfig(
        database_url=database_url,
        valkey_url=valkey_url,
        schema=pg_schema,
        read_schema=f"{pg_schema}_r",
        stream=f"test:inputs:{uuid.uuid4().hex}",
        block_ms=50,
        publish_ms=1,
    )

    async def scenario() -> None:
        client = avalkey.Valkey.from_url(valkey_url)
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        writer = InputStreamWriter(client, config.stream)
        now = now_ms()
        m0 = now - now % 60_000 - 10 * 60_000  # ten minutes ago, on the minute
        prices = [  # (source time, price)
            (m0 + 1_000, 110_000),
            (m0 + 20_000, 110_050),
            (m0 + 30_000, 109_970),
            (m0 + 59_999, 110_010),
            (m0 + 60_000, 110_020),  # the next minute
            (m0 + 5 * 60_000 + 5, 110_100),  # the next five-minute bar
        ]
        try:
            async with engine(config):
                for seq, (ts, price) in enumerate(prices, start=1):
                    await writer.publish(PriceUpdate("EUR/USD", seq, ts, ts, price))
                await writer.publish(PriceUpdate("XAU/USD", 1, m0 + 2_000, m0 + 2_000, 400_000))
                gateway = Gateway(writer, pool, config.schema)
                last = await writer.publish(OpenAccount("A", "lite-s1-v3", 1_000_000, now))
                assert (await gateway.outcome(last, 5)).seq is not None
            projector = await Projector.connect(database_url, config.schema, config.read_schema)
            try:
                while await projector.run_once(limit=2):  # the first minute spans pages
                    pass
            finally:
                await projector.close()
            history = HistoryReader(pool, config.read_schema)
            minutes = await history.bars("EUR/USD", "1m")
            assert minutes == [
                {
                    "time": m0,
                    "open": 110_000,
                    "high": 110_050,
                    "low": 109_970,
                    "close": 110_010,
                    "updates": 4,
                },
                {
                    "time": m0 + 60_000,
                    "open": 110_020,
                    "high": 110_020,
                    "low": 110_020,
                    "close": 110_020,
                    "updates": 1,
                },
                {
                    "time": m0 + 5 * 60_000,
                    "open": 110_100,
                    "high": 110_100,
                    "low": 110_100,
                    "close": 110_100,
                    "updates": 1,
                },
            ]
            assert await history.bars("EUR/USD", "1m", limit=1) == minutes[-1:]
            assert await history.bars("EUR/USD", "1m", before=m0 + 60_000) == minutes[:1]
            five = await history.bars("EUR/USD", "5m")
            start = m0 - m0 % 300_000
            first = [b for b in five if b["time"] == start]
            assert first and first[0]["updates"] >= 4 and first[0]["open"] == 110_000
            assert sum(b["updates"] for b in five) == 6
            days = await history.bars("EUR/USD", "1d")  # 17:00 New York trading days
            assert [b["time"] for b in days] == sorted(
                {NEW_YORK_17.day_start(t) for t, _ in prices}
            )
            assert sum(b["updates"] for b in days) == 6
            gold = await history.bars("XAU/USD", "1h")
            assert [(b["open"], b["updates"]) for b in gold] == [(400_000, 1)]
            assert await history.bars("BTC/USD", "1m") == []
        finally:
            async with pool.acquire() as conn:
                await conn.execute(f"DROP SCHEMA IF EXISTS {config.read_schema} CASCADE")
            await pool.close()
            await client.delete(config.stream)
            await client.aclose()

    asyncio.run(scenario())
