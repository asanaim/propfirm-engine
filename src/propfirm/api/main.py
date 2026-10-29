"""The API process, wired to PostgreSQL, Valkey and Keycloak from the
environment.

    uvicorn propfirm.api.main:app --host 127.0.0.1 --port 8000

Needs OIDC_ISSUER and OIDC_JWKS_URL (the Keycloak realm). Checkout runs
only with PROPFIRM_SANDBOX=1 until the real payment and screening
providers are connected (C6, C7); without it, checkout answers 503.
A background task retries paid orders whose account is not open yet, and
admin stage-limit changes the engine has not journaled yet.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
import valkey.asyncio as avalkey
from fastapi import FastAPI

from propfirm.api.app import Deps, create_app
from propfirm.api.auth import JwksKeys, TokenVerifier
from propfirm.customers.checkout import Checkout
from propfirm.customers.providers import SandboxPayments, SandboxScreener
from propfirm.customers.store import CustomerStore
from propfirm.journal.stream import InputStreamWriter
from propfirm.policy import load_policy
from propfirm.service.config import ConfigError, ServiceConfig
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Gateway
from propfirm.service.live import LiveReader
from propfirm.service.projector import HistoryReader
from propfirm.settings.service import Settings
from propfirm.settings.store import SettingsStore

RECONCILE_S = 10.0
log = logging.getLogger(__name__)


async def _reconcile_forever(checkout: Checkout | None, settings: Settings) -> None:
    while True:
        await asyncio.sleep(RECONCILE_S)
        try:
            await settings.publish_pending(now_ms())
        except Exception:  # retried next round; the previous limits stay in force
            log.exception("setting publication retry failed")
        if checkout is None:
            continue
        try:
            await checkout.reconcile(now_ms())
        except Exception:  # retried next round; the order stays paid
            log.exception("provisioning retry failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = ServiceConfig.from_env(os.environ)
    if not config.oidc_issuer or not config.oidc_jwks_url:
        raise ConfigError("OIDC_ISSUER and OIDC_JWKS_URL are required")
    client = avalkey.Valkey.from_url(config.valkey_url)
    pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=10)
    http = httpx.AsyncClient()
    task: asyncio.Task[None] | None = None
    try:
        policy = load_policy(config.policy_path)
        gateway = Gateway(InputStreamWriter(client, config.stream), pool, config.schema)
        customers = await CustomerStore.open(pool, config.customer_schema)
        settings = Settings(policy, await SettingsStore.open(pool, config.settings_schema), gateway)
        checkout = None
        if config.sandbox:
            checkout = Checkout(customers, settings, SandboxPayments(), SandboxScreener(), gateway)
        task = asyncio.create_task(_reconcile_forever(checkout, settings))
        app.state.deps = Deps(
            live=LiveReader(client, config.live_prefix),
            history=HistoryReader(pool, config.read_schema),
            gateway=gateway,
            verifier=TokenVerifier(
                config.oidc_issuer, config.oidc_audience, JwksKeys(config.oidc_jwks_url, http)
            ),
            customers=customers,
            checkout=checkout,
            policy=settings,
            settings=settings,
            sandbox=config.sandbox,
            country_header=config.country_header,
        )
        yield
    finally:
        if task is not None:
            task.cancel()
        await http.aclose()
        await pool.close()
        await client.aclose()


app = create_app(lifespan=lifespan)
