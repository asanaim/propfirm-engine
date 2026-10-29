"""Registration, checkout and provisioning against PostgreSQL, Valkey and a
running engine, through the API with signed tokens."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction
from typing import Any

import asyncpg
import httpx
import pytest
import valkey.asyncio as avalkey

from propfirm.api.app import Deps, create_app
from propfirm.customers.checkout import Checkout
from propfirm.customers.providers import SandboxPayments, SandboxScreener
from propfirm.customers.store import CustomerStore, migrate
from propfirm.journal.stream import InputStreamWriter
from propfirm.market.types import PriceUpdate
from propfirm.policy import Policy, load_policy
from propfirm.service.config import ServiceConfig
from propfirm.service.engine import run
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Gateway
from propfirm.service.live import LiveReader
from propfirm.service.projector import HistoryReader
from propfirm.service.setup import setup_from_policy
from propfirm.settings.service import Settings
from propfirm.settings.store import SettingsStore
from tests.auth_rig import bearer, verifier

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 29)
ADULT = {"full_name": "Ada Trader", "date_of_birth": "1990-05-17", "residence_country": "DE"}
LITE_10K = {"product_id": "lite", "size_cents": 1_000_000}


@dataclass
class Platform:
    api: httpx.AsyncClient
    store: CustomerStore
    writer: InputStreamWriter
    live: LiveReader
    config: ServiceConfig
    settings: Settings
    engine: Callable[[], Awaitable[None]]  # start the engine (if stopped)
    stop_engine: Callable[[], Awaitable[None]]

    async def call(
        self,
        method: str,
        url: str,
        user: str = "u1",
        body: object = None,
        roles: tuple[str, ...] = ("trader",),
    ) -> httpx.Response:
        return await self.api.request(method, url, json=body, headers=bearer(user, roles))


@asynccontextmanager
async def platform(
    database_url: str,
    valkey_url: str,
    schema: str,
    policy: Policy | None = None,
    *,
    sandbox: bool = True,
    wait_s: float = 5.0,
    start_engine: bool = True,
) -> AsyncIterator[Platform]:
    policy = policy or load_policy()
    config = ServiceConfig(
        database_url=database_url,
        valkey_url=valkey_url,
        schema=schema,
        read_schema=f"{schema}_r",
        customer_schema=f"{schema}_c",
        settings_schema=f"{schema}_s",
        stream=f"test:inputs:{uuid.uuid4().hex}",
        block_ms=50,
        publish_ms=1,
    )
    client = avalkey.Valkey.from_url(valkey_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    writer = InputStreamWriter(client, config.stream)
    gateway = Gateway(writer, pool, config.schema)
    store = await CustomerStore.open(pool, config.customer_schema)
    records = await SettingsStore.open(pool, config.settings_schema)
    settings = Settings(policy, records, gateway, wait_s)
    checkout = Checkout(store, settings, SandboxPayments(), SandboxScreener(), gateway, wait_s)
    live = LiveReader(client, config.live_prefix)
    deps = Deps(
        live=live,
        history=HistoryReader(pool, config.read_schema),
        gateway=gateway,
        verifier=verifier(),
        customers=store,
        checkout=checkout,
        policy=settings,
        settings=settings,
        sandbox=sandbox,
        country_header="X-Country",
        today=lambda: TODAY,
        wait_s=wait_s,
    )
    api = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(deps)), base_url="http://t"
    )
    stop = asyncio.Event()
    task: asyncio.Task[Any] | None = None

    async def engine() -> None:
        nonlocal task, stop
        if task is None:
            stop = asyncio.Event()
            in_force = await settings.current()  # what the engine service reads at start
            task = asyncio.create_task(run(config, setup_from_policy(in_force), stop, in_force))

    async def stop_engine() -> None:
        nonlocal task
        if task is not None:
            stop.set()
            await asyncio.wait_for(task, timeout=10)
            task = None

    if start_engine:
        await engine()
    try:
        yield Platform(api, store, writer, live, config, settings, engine, stop_engine)
    finally:
        await stop_engine()
        await api.aclose()
        async with pool.acquire() as conn:
            for s in (config.read_schema, config.customer_schema, config.settings_schema):
                await conn.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
        await pool.close()
        await client.delete(config.stream)
        async for key in client.scan_iter(f"{config.live_prefix}:*"):
            await client.delete(key)
        await client.aclose()


async def eventually(check: Callable[[], Awaitable[Any]], timeout: float = 5.0) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = await check()
        if value:
            return value
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def test_register_buy_pay_and_trade(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert (await p.call("GET", "/me")).status_code == 404  # not registered yet
            created = await p.call("POST", "/me", body=ADULT)
            assert created.status_code == 201, created.text
            assert created.json()["cif"] == "CIF00000001"
            assert (await p.call("POST", "/me", body=ADULT)).status_code == 409

            await p.store.add_code("WELCOME10", Fraction(1, 10), now_ms())
            bought = await p.call("POST", "/orders", body={**LITE_10K, "code": "WELCOME10"})
            assert bought.status_code == 201, bought.text
            order = bought.json()
            assert (order["ord"], order["price_cents"], order["status"]) == (
                "ORD00000001",
                3_960,
                "pending",
            )
            assert (order["discount_kind"], order["discount_percent"]) == ("code", "10")
            assert (await p.call("GET", f"/orders/{order['ord']}", "u2")).status_code == 404

            paid = await p.call("POST", order["pay_url"])
            assert paid.json()["status"] == "provisioned", paid.text
            acc = paid.json()["acc"]
            assert acc == "ACC00000001"
            again = await p.call("POST", order["pay_url"])  # a repeated payment notice
            assert again.json()["status"] == "provisioned" and again.json()["acc"] == acc

            me = (await p.call("GET", "/me")).json()
            assert [a["acc"] for a in me["accounts"]] == [acc]
            assert me["accounts"][0]["rsv_id"] == "lite-s1-v1"

            t = now_ms()
            await p.writer.publish(PriceUpdate("EUR/USD", 1, t, t, 110_000))
            await eventually(lambda: p.live.account(acc))
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 10}
            placed = await p.call("POST", f"/accounts/{acc}/orders", body=trade)
            assert placed.json()["status"] == "done", placed.text
            assert (await p.call("POST", f"/accounts/{acc}/orders", "u2", trade)).status_code == 404

            second = (await p.call("POST", "/orders", body=LITE_10K)).json()  # nothing paid yet
            assert second["discount_kind"] == "repeat" and second["price_cents"] == 3_960
            coded = await p.call("POST", "/orders", body={**LITE_10K, "code": "WELCOME10"})
            assert coded.status_code == 422  # codes are for first accounts only

    asyncio.run(scenario())


def test_registration_refusals(database_url: str, valkey_url: str, pg_schema: str) -> None:
    policy = load_policy()
    gated = replace(policy, access=replace(policy.access, country_gating=True))

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, gated, start_engine=False) as p:
            minor = {**ADULT, "date_of_birth": "2008-10-30"}  # 18 tomorrow
            refused = await p.call("POST", "/me", "teen", minor)
            assert refused.status_code == 403
            assert "age:17<18" in refused.json()["detail"]["message"]
            bd = await p.call("POST", "/me", "bd", {**ADULT, "residence_country": "BD"})
            assert bd.status_code == 403 and "declared:BD" in bd.text
            via_ip = await p.api.post(
                "/me", json=ADULT, headers={**bearer("ip"), "X-Country": "IN"}
            )
            assert via_ip.status_code == 403 and "ip:IN" in via_ip.text
            assert (await p.call("GET", "/me", "teen")).status_code == 404  # no CIF made
            events = await p.store.events("bd")
            assert [e["kind"] for e in events] == ["registration_refused"]
            assert events[0]["detail"]["reasons"] == ["declared:BD"]
            fine = await p.call("POST", "/me", "de", ADULT)
            assert fine.status_code == 201

    asyncio.run(scenario())


def test_gating_off_admits_every_country(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False) as p:
            bd = await p.call("POST", "/me", "bd", {**ADULT, "residence_country": "BD"})
            assert bd.status_code == 201  # the shipped policy has country_gating = false

    asyncio.run(scenario())


def test_screening_hit_blocks_the_customer(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False) as p:
            await p.call("POST", "/me", body={**ADULT, "full_name": "Sanctioned Person"})
            first = await p.call("POST", "/orders", body=LITE_10K)
            assert first.status_code == 403 and first.json()["detail"]["code"] == "screening"
            again = await p.call("POST", "/orders", body=LITE_10K)
            assert again.json()["detail"]["code"] == "blocked"
            assert (await p.call("GET", "/me")).json()["status"] == "blocked"

    asyncio.run(scenario())


def test_payment_while_the_engine_is_down_is_provisioned_later(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, wait_s=0.3, start_engine=False
        ) as p:
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            paid = (await p.call("POST", order["pay_url"])).json()
            assert (paid["status"], paid["acc"]) == ("paid", "ACC00000001")  # no engine answer
            await p.engine()
            checkout = p.api._transport.app.state.deps.checkout  # type: ignore[attr-defined]
            assert await checkout.reconcile(now_ms()) == 1
            done = (await p.call("GET", f"/orders/{order['ord']}")).json()
            assert (done["status"], done["acc"]) == ("provisioned", "ACC00000001")

    asyncio.run(scenario())


def test_sandbox_payments_are_off_unless_enabled(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, sandbox=False, start_engine=False
        ) as p:
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            assert (await p.call("POST", order["pay_url"])).status_code == 404

    asyncio.run(scenario())


def test_migrations_run_once(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        schema = f"{pg_schema}_m"
        conn = await asyncpg.connect(database_url)
        try:
            assert await migrate(conn, schema) == [1]
            assert await migrate(conn, schema) == []
        finally:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await conn.close()

    asyncio.run(scenario())
