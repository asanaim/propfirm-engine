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
                await writer.publish(OpenAccount("A", "lite-s1-v1", 10_000_000, t0))
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
    raw["products"][0]["stages"][0].update(version=2, daily_loss_percent="4")
    raw["trading"]["config_version"] = "trading-v3"
    raw["trading"]["leverage"]["metal"] = "25"
    edited = parse_policy(raw)
    same_version = copy.deepcopy(raw)
    same_version["products"][0]["stages"][0].update(version=1)

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
            assert first.machine.supervisor.engine.config_version == "trading-v2"
            second = await life(edited)  # the journal keeps its setup; edits arrive as inputs
            engine = second.machine.supervisor.engine
            assert "lite-s1-v2" in second.machine.rule_sets
            assert engine.config_version == "trading-v3"
            assert engine.instruments["XAU/USD"].leverage == 25
            assert second.machine.rule_sets["lite-s1-v1"].daily_loss == Fraction(3, 100)
            with pytest.raises(PolicyError, match="raise the stage's version"):
                await life(parse_policy(same_version))
        finally:
            for key in [config.stream] + [
                k async for k in client.scan_iter(f"{config.live_prefix}:*")
            ]:
                await client.delete(key)
            await client.aclose()

    asyncio.run(scenario())
