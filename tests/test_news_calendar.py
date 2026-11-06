"""The restricted-events calendar files and the risk desk (M13): parsing,
UTC conversion at the edge, publishing to a real engine, manual and
no-fixed-time windows, cancels, roles and the action log."""

import asyncio
import typing
from pathlib import Path
from typing import Any

import httpx
import pytest

from propfirm.api.app import Currency, Deps, create_app
from propfirm.journal.inputs import Input
from propfirm.journal.machine import CODEC, Machine
from propfirm.policy import load_policy
from propfirm.rules.calendar import (
    CURRENCIES,
    DEFAULT_NEWS_DIR,
    CalendarError,
    load_calendars,
    parse_calendar,
    utc_ms,
)
from propfirm.rules.news import TWO_MINUTES_MS, EventScope
from propfirm.service.gateway import Outcome
from propfirm.service.jsonview import view
from propfirm.service.setup import setup_from_policy
from propfirm.settings.newsdesk import Author, NewsAction, NewsDesk, NewsRefused
from tests.auth_rig import FakeCustomers, bearer, verifier

H = 3_600_000
NOV_6_NFP = 1_793_971_800_000  # 2026-11-06 13:30 UTC (08:30 New York, EST)
STAFF = Author("staff-1", "risk@example.com", "monthly list")

# --- calendar files ---------------------------------------------------------------------


def test_the_shipped_calendars_load_with_official_sources_and_utc_times() -> None:
    nov, dec = load_calendars(DEFAULT_NEWS_DIR)
    assert (nov.version, nov.month, len(nov.events)) == ("news-2026-11-v2", "2026-11", 15)
    assert (dec.version, len(dec.events), len(dec.unscheduled)) == ("news-2026-12-v2", 21, 0)
    times = {e.event.event_id: e.event.ts for e in nov.events + dec.events}
    assert times["2026-11-06-us-employment"] == NOV_6_NFP
    assert times["2026-11-12-us-oil-inventory"] == NOV_6_NFP + 6 * 24 * H + 7 * H // 2
    assert times["2026-12-17-ecb-decision"] == utc_ms("2026-12-17T13:15", "UTC")  # 14:15 CET
    assert times["2026-12-17-boe-decision"] == utc_ms("2026-12-17T12:00", "UTC")  # GMT
    assert times["2026-12-09-fomc-decision"] == utc_ms("2026-12-09T19:00", "UTC")
    for item in nov.events + dec.events:
        assert item.source.startswith("https://")
        assert (item.event.before_ms, item.event.after_ms) == (TWO_MINUTES_MS, TWO_MINUTES_MS)
    oil = [e for e in nov.events if e.event.scope is EventScope.OIL_INVENTORY]
    assert len(oil) == 4 and all(e.event.currency is None for e in oil)
    currencies = {e.event.currency for e in nov.events + dec.events}
    assert currencies == {"USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", None}  # FTMO: no JPY
    assert times["2026-11-04-nz-employment"] == utc_ms("2026-11-03T21:45", "UTC")  # NZDT
    assert times["2026-11-03-rba-decision"] == utc_ms("2026-11-03T03:30", "UTC")  # AEDT
    assert [e.event.ts for e in nov.events] == sorted(e.event.ts for e in nov.events)


def test_the_api_currency_list_matches_the_calendar() -> None:
    assert set(typing.get_args(Currency)) == CURRENCIES


def test_daylight_saving_gaps_and_repeats_are_refused() -> None:
    assert utc_ms("2026-03-08T01:59", "America/New_York") + 60_000 == utc_ms(
        "2026-03-08T03:00", "America/New_York"
    )
    with pytest.raises(CalendarError, match="does not exist"):
        utc_ms("2026-03-08T02:30", "America/New_York")
    with pytest.raises(CalendarError, match="occurs twice"):
        utc_ms("2026-11-01T01:30", "America/New_York")
    with pytest.raises(CalendarError, match="unknown time zone"):
        utc_ms("2026-11-06T08:30", "Mars/Olympus")
    with pytest.raises(CalendarError, match="YYYY-MM-DDTHH:MM"):
        utc_ms("2026-11-06 08:30", "UTC")


GOOD = """
version = "news-2026-11-v1"
month = "2026-11"
checked = "2026-10-31"

[[event]]
id = "a"
title = "A"
scope = "currency"
currency = "USD"
local = "2026-11-06T08:30"
zone = "America/New_York"
source = "https://example.org/a"
"""


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('version = "news-2026-11-v1"', 'version = "news-2026-12-v1"'), "version"),
        (('local = "2026-11-06T08:30"', 'local = "2026-12-06T08:30"'), "not in 2026-11"),
        (('scope = "currency"', 'scope = "oil_inventory"'), "no currency"),
        (('scope = "currency"', 'scope = "rates"'), "scope"),
        (('currency = "USD"', 'currency = "XAU"'), "currency"),
        (('source = "https://example.org/a"', 'source = "http://example.org/a"'), "https"),
        (('id = "a"', 'id = "A b"'), "id must match"),
        (('title = "A"', 'title = "A"\nwindow_ms = 0'), "unknown keys"),
        (('checked = "2026-10-31"', 'checked = "yesterday"'), "checked"),
    ],
)
def test_bad_calendars_are_refused(change: tuple[str, str], message: str) -> None:
    assert parse_calendar(GOOD).events[0].event.ts == NOV_6_NFP
    with pytest.raises(CalendarError, match=message):
        parse_calendar(GOOD.replace(*change))


def test_ids_are_unique_within_and_across_files(tmp_path: Path) -> None:
    second = GOOD.split("[[event]]")[1]
    with pytest.raises(CalendarError, match="unique"):
        parse_calendar(GOOD + "[[event]]" + second)
    (tmp_path / "2026-11.toml").write_text(GOOD)
    (tmp_path / "2026-12.toml").write_text(GOOD.replace("2026-11", "2026-12"))
    with pytest.raises(CalendarError, match="two files"):
        load_calendars(tmp_path)
    (tmp_path / "2026-12.toml").unlink()
    (tmp_path / "november.toml").write_text(GOOD)
    with pytest.raises(CalendarError, match=r"YYYY-MM\.toml"):
        load_calendars(tmp_path)
    (tmp_path / "november.toml").unlink()
    (tmp_path / "2026-10.toml").write_text(GOOD)
    with pytest.raises(CalendarError, match="differs from the name"):
        load_calendars(tmp_path)


# --- the risk desk against a real engine ---------------------------------------------


class EngineGateway:
    """Applies inputs to a real journal machine, as the engine service would."""

    def __init__(self) -> None:
        self.machine = Machine(CODEC.encode(setup_from_policy(load_policy())), {1: bytes(32)})
        self.n = 0

    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome:
        self.n += 1
        record = self.machine.apply(f"{self.n}-0", item).record
        events = CODEC.decode(record.events)
        assert isinstance(events, tuple)
        return Outcome(f"{self.n}-0", record.seq, [view(e) for e in events])

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome:
        raise NotImplementedError


class MemoryLog:
    def __init__(self) -> None:
        self.actions: list[NewsAction] = []

    async def record(self, action: NewsAction) -> NewsAction:
        from dataclasses import replace

        saved = replace(action, id=len(self.actions) + 1)
        self.actions.append(saved)
        return saved

    async def history(self, limit: int = 100) -> list[NewsAction]:
        return list(reversed(self.actions))[:limit]

    async def known_ids(self) -> set[str]:
        return {a.event_id for a in self.actions}

    async def titles(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for a in self.actions:
            if a.action in ("manual", "published", "collector") and a.result == "done" and a.title:
                out.setdefault(a.event_id, a.title)
        return out


def desk() -> tuple[NewsDesk, EngineGateway, MemoryLog]:
    gateway, log = EngineGateway(), MemoryLog()
    return NewsDesk(load_calendars(DEFAULT_NEWS_DIR), log, gateway), gateway, log


def test_publishing_a_month_sends_only_windows_not_started_and_is_idempotent() -> None:
    news, gateway, log = desk()
    now = NOV_6_NFP - TWO_MINUTES_MS  # the NFP window starts now: left out with earlier ones
    sent = asyncio.run(news.publish("2026-11", STAFF, now))
    assert [a.event_id for a in sent] == [  # the US and Canadian jobs reports start now: left out
        "2026-11-10-us-cpi",
        "2026-11-12-us-oil-inventory",
        "2026-11-16-ca-cpi",
        "2026-11-18-uk-cpi",
        "2026-11-18-us-oil-inventory",
        "2026-11-18-fomc-minutes",
        "2026-11-19-au-employment",
        "2026-11-25-au-cpi",
        "2026-11-25-us-oil-inventory",
    ]
    assert {(a.action, a.result, a.calendar_version, a.author) for a in sent} == {
        ("calendar", "done", "news-2026-11-v2", "staff-1")
    }
    engine = gateway.machine.supervisor.engine
    cpi = engine.news_event("2026-11-10-us-cpi")
    assert cpi is not None and "EUR/USD" in cpi.symbols and "XAU/USD" in cpi.symbols
    assert "BTC/USD" not in cpi.symbols  # crypto exempt
    oil = engine.news_event("2026-11-12-us-oil-inventory")
    assert oil is not None and oil.symbols == {"WTI", "Brent"}
    again = asyncio.run(news.publish("2026-11", STAFF, now + 1))
    assert {(a.result, a.detail) for a in again} == {("rejected", "duplicate_news_event")}
    assert len(log.actions) == 18
    with pytest.raises(NewsRefused, match="no calendar file"):
        asyncio.run(news.publish("2027-01", STAFF, now))


def test_manual_and_published_windows_and_admin_cancel() -> None:
    news, gateway, _ = desk()
    engine = gateway.machine.supervisor.engine
    now = NOV_6_NFP
    emergency = asyncio.run(
        news.manual(
            "2026-11-07-snb-emergency",
            "Swiss National Bank unscheduled statement",
            EventScope.CURRENCY,
            "CHF",
            now + H,
            STAFF,
            now,
        )
    )
    assert (emergency.action, emergency.result) == ("manual", "done")
    window = engine.news_event("2026-11-07-snb-emergency")
    assert window is not None and (window.start_ts, window.end_ts) == (
        now + H - TWO_MINUTES_MS,
        now + H + TWO_MINUTES_MS + 1,
    )
    with pytest.raises(NewsRefused, match="already over"):
        asyncio.run(news.manual("old", "x", EventScope.CURRENCY, "USD", now - H, STAFF, now))
    with pytest.raises(NewsRefused):
        asyncio.run(news.manual("oil", "x", EventScope.OIL_INVENTORY, "USD", now, STAFF, now))
    with pytest.raises(NewsRefused, match="event id"):
        asyncio.run(news.manual("Bad Id", "x", EventScope.CURRENCY, "USD", now, STAFF, now))
    boj = asyncio.run(news.published("2026-12-18-boj-decision", "BoJ", "JPY", STAFF, now))
    assert boj.result == "done"
    window = engine.news_event("2026-12-18-boj-decision")
    assert window is not None and (window.start_ts, window.end_ts) == (
        now,
        now + TWO_MINUTES_MS + 1,
    )
    assert "USD/JPY" in window.symbols and "EUR/USD" not in window.symbols
    admin = Author("admin-1", None, "SNB statement withdrawn")
    cancelled = asyncio.run(news.cancel("2026-11-07-snb-emergency", admin, now + 1))
    assert (cancelled.action, cancelled.result) == ("cancel", "done")
    assert engine.news_event("2026-11-07-snb-emergency") is None
    late = asyncio.run(news.cancel("2026-12-18-boj-decision", admin, now + 2))
    assert (late.result, late.detail) == ("rejected", "news_started")
    titles = asyncio.run(news.titles())
    assert titles["2026-12-18-boj-decision"] == "BoJ"
    assert titles["2026-11-06-us-employment"] == (
        "US Non-Farm Employment Change, Unemployment Rate & Wages"
    )


# --- API: roles, bodies and titles ------------------------------------------------------


class Live:
    async def head(self) -> dict[str, Any] | None:
        return {"seq": 1}

    async def account(self, account_id: str) -> dict[str, Any] | None:
        return None

    async def quotes(self) -> dict[str, dict[str, Any]]:
        return {}

    async def news(self) -> list[dict[str, Any]]:
        return [
            {"event_id": "2026-11-10-us-cpi", "start_ts": 1, "end_ts": 2, "symbols": ["EUR/USD"]},
            {"event_id": "unnamed", "start_ts": 3, "end_ts": 4, "symbols": ["EUR/USD"]},
        ]


class History:
    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        return []

    async def bars(
        self, symbol: str, interval: str, limit: int = 300, before: int | None = None
    ) -> list[dict[str, int]]:
        return []


def call(
    method: str,
    url: str,
    body: object = None,
    roles: tuple[str, ...] = ("staff",),
    *,
    mfa: bool = True,
    login_age_s: int = 0,
) -> tuple[httpx.Response, EngineGateway]:
    news, gateway, _ = desk()
    deps = Deps(
        Live(),
        History(),
        gateway,
        verifier(),
        FakeCustomers(),
        news=news,
        clock=lambda: NOV_6_NFP - 10 * H,
    )
    app = create_app(deps)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            login = (NOV_6_NFP - 10 * H) // 1000 - login_age_s  # "now" on the API's clock
            headers = bearer("u", roles, mfa=mfa, auth_time=login)
            return await client.request(method, url, json=body, headers=headers)

    return asyncio.run(go()), gateway


def test_traders_read_titled_windows_but_cannot_touch_the_calendar() -> None:
    response, _ = call("GET", "/news", roles=("trader",))
    assert [e["title"] for e in response.json()] == ["US CPI y/y", None]
    assert call("GET", "/staff/news", roles=("trader",))[0].status_code == 403
    reason = {"reason": "monthly list"}
    assert call("POST", "/staff/news/calendars/2026-11/publish", reason, ("trader",))[
        0
    ].status_code == (403)


def test_staff_publish_and_list_but_only_an_admin_cancels() -> None:
    response, gateway = call("POST", "/staff/news/calendars/2026-11/publish", {"reason": "nov"})
    assert response.status_code == 200
    assert [a["event_id"] for a in response.json()][:3] == [
        "2026-11-06-ca-employment",
        "2026-11-06-us-employment",
        "2026-11-10-us-cpi",
    ]
    assert gateway.machine.supervisor.engine.news_event("2026-11-06-us-employment") is not None
    listing = call("GET", "/staff/news")[0].json()
    assert [c["version"] for c in listing["calendars"]] == ["news-2026-11-v2", "news-2026-12-v2"]
    cpi = listing["calendars"][0]["events"][6]
    assert (cpi["event_id"], cpi["in_engine"], cpi["local"]) == (
        "2026-11-10-us-cpi",
        True,  # the fake live view lists it
        "2026-11-10T08:30",
    )
    body = {"reason": "withdrawn"}
    url = "/admin/news/events/2026-11-10-us-cpi/cancel"
    assert call("POST", url, body, ("staff",))[0].status_code == 403
    cancelled = call("POST", url, body, ("admin",))[0]
    assert cancelled.status_code == 200 and cancelled.json()["result"] == "rejected"  # unknown here
    assert call("POST", "/staff/news/calendars/2027-01/publish", body)[0].status_code == 404


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("/staff/news/calendars/2026-11/publish", {}),  # no reason
        ("/staff/news/calendars/2026-11/publish", {"reason": "   "}),
        (
            "/staff/news/published",
            {"event_id": "boj", "title": "BoJ", "currency": "XAU", "reason": "r"},
        ),
        (
            "/staff/news/published",
            {"event_id": "B J", "title": "BoJ", "currency": "JPY", "reason": "r"},
        ),
        (
            "/staff/news/events",
            {
                "event_id": "x",
                "title": "X",
                "scope": "currency",
                "currency": "USD",
                "ts": 1.5,
                "reason": "r",
            },
        ),
        (
            "/staff/news/events",
            {
                "event_id": "x",
                "title": "X",
                "scope": "oil_inventory",
                "currency": "USD",
                "ts": 1,
                "reason": "r",
            },
        ),
    ],
)
def test_news_requests_the_api_refuses(url: str, body: dict[str, Any]) -> None:
    response, gateway = call("POST", url, body)
    assert response.status_code == 422
    assert gateway.n == 0


def test_staff_add_a_manual_window_and_mark_a_release_published() -> None:
    body = {
        "event_id": "2026-11-06-snb",
        "title": "SNB statement",
        "scope": "currency",
        "currency": "CHF",
        "ts": NOV_6_NFP,
        "reason": "unscheduled",
    }
    response, gateway = call("POST", "/staff/news/events", body)
    assert response.status_code == 201 and response.json()["result"] == "done"
    published = {"event_id": "2026-12-18-boj", "title": "BoJ", "currency": "JPY", "reason": "out"}
    response, gateway = call("POST", "/staff/news/published", published)
    assert response.status_code == 201
    window = gateway.machine.supervisor.engine.news_event("2026-12-18-boj")
    assert window is not None and window.start_ts == NOV_6_NFP - 10 * H


def test_the_calendar_directory_is_configurable() -> None:
    from propfirm.service.config import ServiceConfig

    env = {"DATABASE_URL": "postgresql://u:p@h/db", "VALKEY_URL": "redis://h:6379/0"}
    assert ServiceConfig.from_env(env).news_dir == DEFAULT_NEWS_DIR
    assert ServiceConfig.from_env({**env, "PROPFIRM_NEWS_DIR": "/srv/news"}).news_dir == Path(
        "/srv/news"
    )


def test_start_up_publishes_every_month_skipping_what_the_engine_has() -> None:
    from propfirm.api.main import SYSTEM_CALENDAR, _publish_calendars

    news, gateway, log = desk()

    class Shown:
        async def news(self) -> list[dict[str, Any]]:
            return [{"event_id": "2026-12-04-us-employment"}]

    # at a fixed time: past events are not sent, so the wall clock would make
    # this test depend on the day it runs
    asyncio.run(_publish_calendars(news, Shown(), lambda: NOV_6_NFP - H))  # type: ignore[arg-type]
    sent = {a.event_id for a in log.actions}
    assert "2026-12-04-us-employment" not in sent  # the engine already shows it
    assert {"2026-11-06-us-employment", "2026-12-09-fomc-decision"} <= sent
    assert {(a.author, a.result) for a in log.actions} == {(SYSTEM_CALENDAR, "done")}
    assert log.actions[0].reason.startswith("automatic at start-up (news-2026-11-v2")
    engine = gateway.machine.supervisor.engine
    admin = Author("admin-1", None, "withdrawn")
    asyncio.run(news.cancel("2026-12-09-fomc-decision", admin, NOV_6_NFP))
    again = asyncio.run(news.publish_all(STAFF, NOV_6_NFP + 1))
    fomc = [a for a in again if a.event_id == "2026-12-09-fomc-decision"]
    assert [(a.result, a.detail) for a in fomc] == [("rejected", "duplicate_news_event")]
    assert engine.news_event("2026-12-09-fomc-decision") is None  # a cancel is not undone


def test_the_collector_settings() -> None:
    from propfirm.service.config import ConfigError, ServiceConfig

    env = {"DATABASE_URL": "postgresql://u:p@h/db", "VALKEY_URL": "redis://h:6379/0"}
    config = ServiceConfig.from_env(env)
    assert (config.news_collector, config.collect_hours) == (True, 6)
    assert config.collector_agent == "pipridge-news-collector/1.0"
    off = ServiceConfig.from_env(
        {**env, "PROPFIRM_NEWS_COLLECTOR": "0", "PROPFIRM_COLLECT_HOURS": "12"}
    )
    assert (off.news_collector, off.collect_hours) == (False, 12)
    for bad in ({"PROPFIRM_NEWS_COLLECTOR": "yes"}, {"PROPFIRM_COLLECT_HOURS": "0"}):
        with pytest.raises(ConfigError):
            ServiceConfig.from_env({**env, **bad})


def test_cancelling_is_an_approval_it_needs_a_recent_second_factor() -> None:
    """Step-up (staff spec 5a): an admin's approval needs a second factor
    entered at most 5 minutes before (policy `staff.step_up_minutes`)."""
    url, body = "/admin/news/events/2026-11-10-us-cpi/cancel", {"reason": "withdrawn"}
    no_mfa = call("POST", url, body, ("admin",), mfa=False)[0]
    assert (no_mfa.status_code, no_mfa.json()["detail"]["code"]) == (401, "mfa_required")
    assert "insufficient_user_authentication" in no_mfa.headers["WWW-Authenticate"]
    old = call("POST", url, body, ("admin",), login_age_s=301)[0]
    assert (old.status_code, old.json()["detail"]["code"]) == (401, "reauth_required")
    assert old.headers["WWW-Authenticate"].endswith("max_age=300")
    assert call("POST", url, body, ("admin",), login_age_s=300)[0].status_code == 200
    staff = call("POST", url, body, ("staff",))[0]
    assert staff.status_code == 403 and staff.json()["detail"]["permission"] == "news.cancel"


def test_desk_work_is_not_an_approval_but_every_staff_login_needs_a_second_factor() -> None:
    """Desk work needs no recent re-entry (any MFA login); a password-only
    staff login is refused everywhere [decided 6 Nov 2026]."""
    month = "/staff/news/calendars/2026-11/publish"
    assert call("POST", month, {"reason": "nov"}, login_age_s=3_600)[0].status_code == 200
    password_only = call("POST", month, {"reason": "nov"}, mfa=False)[0]
    assert (password_only.status_code, password_only.json()["detail"]["code"]) == (
        401,
        "mfa_required",
    )
    assert call("GET", "/staff/news", mfa=False)[0].status_code == 401
    assert call("POST", month, {"reason": "nov"}, ("trader",))[0].status_code == 403
