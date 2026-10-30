"""The API process, wired to PostgreSQL, Valkey and Keycloak from the
environment.

    uvicorn propfirm.api.main:app --host 127.0.0.1 --port 8000

Needs OIDC_ISSUER and OIDC_JWKS_URL (the Keycloak realm). Checkout runs
only with PROPFIRM_SANDBOX=1 until the real payment and screening
providers are connected (C6, C7); without it, checkout answers 503.
A background task retries paid orders whose account is not open yet, and
admin stage-limit changes the engine has not journaled yet; another, every
second, turns new stage passes into progressions, opens the next
evaluation accounts, and moves funded onboardings on (starting them for
approved funded reviews, reading identity-check outcomes no webhook
brought, issuing contracts and opening signed funded accounts).

Funded onboarding runs with the sandbox identity check under
PROPFIRM_SANDBOX=1, or with Didit when DIDIT_API_KEY, DIDIT_WORKFLOW_ID and
DIDIT_WEBHOOK_SECRET are set (and PROPFIRM_PUBLIC_URL for the callback);
otherwise its endpoints answer 503. At start the cached order, customer and progression
statuses are rebuilt from their histories; a correction (the cache was
edited by hand) is logged as a warning.
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
from propfirm.customers.cases_store import CaseStore
from propfirm.customers.casework import Casework
from propfirm.customers.checkout import Checkout
from propfirm.customers.funding import Onboarding
from propfirm.customers.kyc import DiditKyc, KycProvider, SandboxKyc, WorkflowScreening
from propfirm.customers.lifecycle import Lifecycle
from propfirm.customers.lifecycle_store import ProgressionStore
from propfirm.customers.onboarding_store import OnboardingStore
from propfirm.customers.providers import SandboxPayments, SandboxScreener, Screener
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
LIFECYCLE_S = 1.0
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


async def _lifecycle_forever(
    lifecycle: Lifecycle, onboarding: Onboarding | None, cases: Casework
) -> None:
    while True:
        await asyncio.sleep(LIFECYCLE_S)
        try:
            await lifecycle.poll(now_ms())
            await lifecycle.advance(now_ms())
        except Exception:  # retried next round; passes wait in the journal
            log.exception("stage progression round failed")
        try:
            await cases.advance(now_ms())  # confirmed decisions not yet carried out
        except Exception:  # retried next round
            log.exception("carrying out case decisions failed")
        if onboarding is None:
            continue
        try:
            await onboarding.advance(now_ms())
        except Exception:  # retried next round; every step is recorded before it moves on
            log.exception("funded onboarding round failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = ServiceConfig.from_env(os.environ)
    if not config.oidc_issuer or not config.oidc_jwks_url:
        raise ConfigError("OIDC_ISSUER and OIDC_JWKS_URL are required")
    client = avalkey.Valkey.from_url(config.valkey_url)
    pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=10)
    http = httpx.AsyncClient()
    tasks: list[asyncio.Task[None]] = []
    try:
        policy = load_policy(config.policy_path)
        gateway = Gateway(InputStreamWriter(client, config.stream), pool, config.schema)
        customers = await CustomerStore.open(pool, config.customer_schema)
        progressions = ProgressionStore(pool, config.customer_schema, config.schema)
        onboardings = OnboardingStore(pool, config.customer_schema)
        case_store = CaseStore(pool, config.customer_schema)
        fixed = (
            await customers.rebuild_status()
            + await progressions.rebuild_status()
            + await onboardings.rebuild_status()
            + await case_store.rebuild_status()
        )
        if fixed:
            log.warning("cached status differed from the history and was rebuilt: %s", fixed)
        settings = Settings(policy, await SettingsStore.open(pool, config.settings_schema), gateway)
        cases = Casework(case_store, customers, settings, gateway)
        checkout = None
        if config.sandbox:
            checkout = Checkout(
                customers,
                settings,
                SandboxPayments(),
                SandboxScreener(),
                gateway,
                funded=onboardings,
                cases=cases,
            )
        kyc: KycProvider | None = None
        screener: Screener | None = None
        if config.didit:
            assert config.didit_api_key and config.didit_workflow_id
            kyc = DiditKyc(http, config.didit_api_key, config.didit_workflow_id)
            screener = WorkflowScreening()  # the Didit workflow includes AML screening
        elif config.sandbox:
            kyc, screener = SandboxKyc(), SandboxScreener()
        onboarding = None
        if kyc is not None and screener is not None:
            onboarding = Onboarding(
                onboardings,
                customers,
                settings,
                kyc,
                screener,
                gateway,
                checkout,
                sandbox=config.sandbox,
                callback_url=config.public_url,
                cases=cases,
            )
        lifecycle = Lifecycle(progressions, settings, gateway, cases=cases)
        tasks.append(asyncio.create_task(_reconcile_forever(checkout, settings)))
        tasks.append(asyncio.create_task(_lifecycle_forever(lifecycle, onboarding, cases)))
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
            lifecycle=lifecycle,
            onboarding=onboarding,
            cases=cases,
            didit_webhook_secret=config.didit_webhook_secret,
            sandbox=config.sandbox,
            country_header=config.country_header,
            price_source=config.price_source,
        )
        yield
    finally:
        for task in tasks:
            task.cancel()
        await http.aclose()
        await pool.close()
        await client.aclose()


app = create_app(lifespan=lifespan)
