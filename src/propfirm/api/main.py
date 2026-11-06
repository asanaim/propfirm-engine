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
brought, issuing contracts and opening signed funded accounts), carries out
confirmed case decisions, and moves payouts on (once approved: checked
again, debited by the engine, sent, confirmed).

Payouts run only with PROPFIRM_SANDBOX=1 (the sandbox payout provider sends
nothing) until the NOWPayments payout client is connected (C6); without it,
the payout endpoints answer 503. Security emails (a payout wallet added or
changed) go out through SMTP when SMTP_HOST and MAIL_FROM are set (with
SMTP_PORT, SMTP_TLS, SMTP_USERNAME, SMTP_PASSWORD), otherwise through the
sandbox mailer, which sends nothing; payouts never run without one of them.

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
from propfirm.customers.notifications import (
    Mailer,
    Notifications,
    NotificationStore,
    SandboxMailer,
    SmtpMailer,
)
from propfirm.customers.onboarding_store import OnboardingStore
from propfirm.customers.payout_service import Payouts
from propfirm.customers.payout_store import PayoutStore
from propfirm.customers.providers import (
    SandboxPayments,
    SandboxPayouts,
    SandboxScreener,
    Screener,
)
from propfirm.customers.store import CustomerStore
from propfirm.journal.stream import InputStreamWriter
from propfirm.newsfeed.alerts import SourceAlerts, SourceStore
from propfirm.newsfeed.collector import Collector, collect_forever
from propfirm.policy import load_policy
from propfirm.rules.calendar import load_calendars
from propfirm.service.config import ConfigError, ServiceConfig
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Gateway
from propfirm.service.live import LiveReader
from propfirm.service.projector import HistoryReader
from propfirm.settings.newsdesk import Author, NewsDesk, NewsStore
from propfirm.settings.service import Settings
from propfirm.settings.store import SettingsStore
from propfirm.staff.cutoff import Cutoffs, IdentityProvider, KeycloakAdmin, SandboxIdentity
from propfirm.staff.desk import ApprenticeDesk, StaffDesk, load_duties
from propfirm.staff.directory import Directory, Owner
from propfirm.staff.store import StaffStore

RECONCILE_S = 10.0
CUTOFF_S = 60.0
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


async def _cutoffs_forever(cutoffs: Cutoffs) -> None:
    """Leavers' logins disabled at the identity provider, every minute."""
    while True:
        try:
            await cutoffs.advance(now_ms())
        except Exception:  # retried next round; late ones are alerted
            log.exception("leaver cut-off round failed")
        await asyncio.sleep(CUTOFF_S)


SYSTEM_CALENDAR = "system:calendar"


async def _publish_calendars(news: NewsDesk, live: LiveReader) -> None:
    """Publish the calendar files at start-up; events the engine already
    shows are skipped, and any other repeat is refused by the engine."""
    try:
        known = frozenset(e["event_id"] for e in await live.news())
        versions = ", ".join(c.version for c in news.calendars) or "none"
        who = Author(SYSTEM_CALENDAR, None, f"automatic at start-up ({versions})")
        done = await news.publish_all(who, now_ms(), known)
        log.info("news calendar: %d events sent at start-up", len(done))
    except Exception:
        log.exception("news calendar publication at start-up failed")


async def _lifecycle_forever(
    lifecycle: Lifecycle,
    onboarding: Onboarding | None,
    cases: Casework,
    payouts: Payouts | None,
    notifications: Notifications | None,
) -> None:
    while True:
        await asyncio.sleep(LIFECYCLE_S)
        try:
            await lifecycle.poll(now_ms())
            await lifecycle.advance(now_ms())
        except Exception:  # retried next round; passes wait in the journal
            log.exception("stage progression round failed")
        try:
            await cases.news_violations(now_ms())  # restricted-window fills: integrity alerts
        except Exception:  # retried next round; the cursor moves only with the alerts
            log.exception("raising news-violation alerts failed")
        try:
            await cases.advance(now_ms())  # confirmed decisions not yet carried out
        except Exception:  # retried next round
            log.exception("carrying out case decisions failed")
        if payouts is not None:
            try:
                await payouts.advance(now_ms())  # each payout's next step, recorded first
            except Exception:  # retried next round
                log.exception("payout round failed")
        if notifications is not None:
            try:
                await notifications.advance(now_ms())  # security emails not sent yet
            except Exception:  # retried next round
                log.exception("sending notices failed")
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
        case_store = CaseStore(pool, config.customer_schema, config.schema)
        payout_store = PayoutStore(pool, config.customer_schema)
        fixed = (
            await customers.rebuild_status()
            + await progressions.rebuild_status()
            + await onboardings.rebuild_status()
            + await case_store.rebuild_status()
            + await payout_store.rebuild_status()
        )
        if fixed:
            log.warning("cached status differed from the history and was rebuilt: %s", fixed)
        settings_store = await SettingsStore.open(pool, config.settings_schema)
        settings = Settings(policy, settings_store, gateway, proposals=settings_store)
        news = NewsDesk(
            load_calendars(config.news_dir), NewsStore(pool, config.settings_schema), gateway
        )
        cases = Casework(case_store, customers, settings, gateway)
        staff = Directory(
            await StaffStore.open(pool, config.staff_schema),
            settings,
            [Owner(subject, name) for subject, name in config.staff_owners],
            legacy_roles=config.staff_legacy_roles,
        )
        if added := await staff.bootstrap(now_ms()):
            log.warning("owners recorded in the staff directory: %s", added)
        if not config.staff_owners:
            log.warning("STAFF_OWNERS is empty: no owner in the staff directory")
        desk = StaffDesk(pool, config.staff_schema, load_duties())
        provider: IdentityProvider | None = None
        if config.staff_idp_url:
            provider = KeycloakAdmin(
                http,
                config.staff_idp_url,
                config.staff_idp_realm,
                config.staff_idp_client_id,
                config.staff_idp_client_secret,
            )
        elif config.sandbox:
            provider = SandboxIdentity()
        else:
            log.warning("STAFF_IDP_URL not set: leavers' logins are disabled by hand (alerts)")
        staff_policy = policy.staff
        cutoffs = Cutoffs(
            staff.store,
            provider,
            desk,
            staff_policy.leaver_cutoff_alert_minutes,
            staff_policy.leaver_cutoff_max_hours,
        )
        tasks.append(asyncio.create_task(_cutoffs_forever(cutoffs)))
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
        live = LiveReader(client, config.live_prefix)
        mailer: Mailer | None = None
        if config.smtp_host:
            assert config.mail_from is not None
            mailer = SmtpMailer(
                config.smtp_host,
                config.smtp_port,
                config.mail_from,
                config.smtp_username,
                config.smtp_password,
                config.smtp_tls,
            )
        elif config.sandbox:
            mailer = SandboxMailer()
        notifications = (
            Notifications(NotificationStore(pool, config.customer_schema), mailer)
            if mailer
            else None
        )
        payouts = None
        if config.sandbox and notifications is not None:
            payouts = Payouts(
                payout_store,
                customers,
                live,
                settings,
                gateway,
                SandboxPayouts(),
                identity=onboardings,
                cases=cases,
            )
        tasks.append(asyncio.create_task(_reconcile_forever(checkout, settings)))
        tasks.append(asyncio.create_task(_publish_calendars(news, live)))
        if config.news_collector:
            agent = config.collector_agent

            async def fetch(url: str) -> str:
                response = await http.get(
                    url, headers={"User-Agent": agent}, timeout=30.0, follow_redirects=True
                )
                response.raise_for_status()
                return response.text

            # source failures are emailed to the news.desk holders in the staff
            # directory, plus NEWS_ALERT_EMAILS, and shown on their staff page
            alerts = SourceAlerts(
                SourceStore(pool, config.settings_schema),
                mailer,
                config.news_alert_emails,
                config.news_alert_remind_hours,
                holders=staff.emails_for,
                notices=desk,
            )
            collector = Collector(news, live, fetch, alerts=alerts)
            tasks.append(
                asyncio.create_task(collect_forever(collector, config.collect_hours * 3600, now_ms))
            )
        tasks.append(
            asyncio.create_task(
                _lifecycle_forever(lifecycle, onboarding, cases, payouts, notifications)
            )
        )
        app.state.deps = Deps(
            live=live,
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
            payouts=payouts,
            news=news,
            staff=staff,
            desk=desk,
            cutoffs=cutoffs,
            apprentices=ApprenticeDesk(pool, config.staff_schema),
            source_alerts=SourceAlerts(SourceStore(pool, config.settings_schema), None, ()),
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
