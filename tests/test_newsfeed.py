"""The news collector: parsers against saved excerpts of the publishers' own
schedule pages (read 31 Oct 2026), and a collector run against a real
journal machine."""

import asyncio
from datetime import date
from pathlib import Path

import pytest

from propfirm.newsfeed import sources as src
from propfirm.newsfeed.collector import SOURCES, SYSTEM_COLLECTOR, Collector, Fetch, collect
from propfirm.newsfeed.sources import SourceError
from propfirm.rules.calendar import DEFAULT_NEWS_DIR, load_calendars, utc_ms
from propfirm.rules.news import CURRENT_MAP
from propfirm.settings.newsdesk import Author, NewsDesk
from tests.test_news_calendar import EngineGateway, MemoryLog

FIX = Path(__file__).parent / "fixtures" / "news"
NOW = utc_ms("2026-10-31T04:00", "UTC")


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def ids(items: list[src.Collected]) -> list[tuple[str, str]]:
    return [(c.event.event_id, c.local) for c in items]


# --- parsers ------------------------------------------------------------------------------


def test_federal_reserve_decisions_and_minutes() -> None:
    nov = src.parse_fed_month(page("fed-2026-november.html"), 2026, 11, "fed")
    dec = src.parse_fed_month(page("fed-2026-december.html"), 2026, 12, "fed")
    assert ids(nov) == [("2026-11-18-fomc-minutes", "2026-11-18T14:00")]
    assert sorted(ids(dec)) == [
        ("2026-12-09-fomc-decision", "2026-12-09T14:00"),
        ("2026-12-30-fomc-minutes", "2026-12-30T14:00"),
    ]  # the press conference is not restricted
    assert dec[0].event.instrument_map == CURRENT_MAP
    assert src.fed_month_url(2026, 12).endswith("/newsevents/2026-december.htm")
    with pytest.raises(SourceError):
        src.parse_fed_month("<html>maintenance</html>", 2026, 12, "fed")


def test_statistics_canada_cpi_and_labour_force_at_0830_eastern() -> None:
    found = [c for c in src.parse_statcan(page("statcan.json")) if "2026-11" <= c.local < "2027"]
    assert ids(found) == [
        ("2026-11-06-ca-employment", "2026-11-06T08:30"),
        ("2026-11-16-ca-cpi", "2026-11-16T08:30"),
        ("2026-12-04-ca-employment", "2026-12-04T08:30"),
        ("2026-12-14-ca-cpi", "2026-12-14T08:30"),
    ]
    assert found[0].event.ts == utc_ms("2026-11-06T13:30", "UTC")  # EST
    assert {c.event.currency for c in found} == {"CAD"}


def test_stats_nz_quarterly_releases_only() -> None:
    found = src.parse_stats_nz(page("stats-nz.ics"))
    assert ("2026-11-04-nz-employment", "2026-11-04T10:45") in ids(found)
    assert ("2026-12-17-nz-gdp", "2026-12-17T10:45") in ids(found)
    assert all(not c.title.endswith("April 2027") for c in found)  # the monthly CPI is not FTMO's
    assert {c.event.event_id.split("-", 3)[3] for c in found} <= {
        "nz-employment",
        "nz-gdp",
        "nz-cpi",
    }
    labour = next(c for c in found if c.event.event_id == "2026-11-04-nz-employment")
    assert labour.event.ts == utc_ms("2026-11-03T21:45", "UTC")  # NZDT, the day before in UTC
    with pytest.raises(SourceError):
        src.parse_stats_nz("<html>not a calendar</html>")


def test_ons_uk_cpi_in_utc_and_not_the_time_series() -> None:
    found = src.parse_ons(page("ons.json"), "ons")
    assert ids(found) == [
        ("2026-11-18-uk-cpi", "2026-11-18T07:00"),
        ("2026-12-16-uk-cpi", "2026-12-16T07:00"),
    ]
    summer = '{"releases":[{"description":{"title":"Consumer price inflation, UK: June 2026",' + (
        '"release_date":"2026-07-22T06:00:00.000Z"}}]}'
    )
    (july,) = src.parse_ons(summer, "ons")
    assert july.local == "2026-07-22T07:00"  # 06:00 UTC is 07:00 BST


def test_abs_cpi_labour_force_and_gdp_in_canberra_time() -> None:
    found = (
        src.parse_abs(page("abs-202611-rcc_economy.html"), "abs")
        + src.parse_abs(page("abs-202612-rcc_economy.html"), "abs")
        + src.parse_abs(page("abs-202612-rcc_labour.html"), "abs")
    )
    assert ids(found) == [
        ("2026-11-25-au-cpi", "2026-11-25T11:30"),
        ("2026-12-02-au-gdp", "2026-12-02T11:30"),
        ("2026-12-17-au-employment", "2026-12-17T11:30"),
    ]
    assert found[0].event.ts == utc_ms("2026-11-25T00:30", "UTC")  # AEDT
    with pytest.raises(SourceError):
        src.parse_abs("<p>other page</p>", "abs")


def test_central_banks() -> None:
    assert ids(src.parse_boc(page("boc.html"))) == [("2026-12-09-boc-decision", "2026-12-09T09:45")]
    rba = ids(src.parse_rba(page("rba.html")))
    assert ("2026-11-03-rba-decision", "2026-11-03T14:30") in rba  # second meeting day
    assert ("2026-12-08-rba-decision", "2026-12-08T14:30") in rba
    assert ("2026-02-03-rba-decision", "2026-02-03T14:30") in rba
    snb = ids(src.parse_snb(page("snb.html")))
    assert ("2026-12-10-snb-decision", "2026-12-10T09:30") in snb
    assert all(e.endswith("snb-decision") for e, _ in snb)  # news conferences are not separate
    boe = ids(src.parse_boe(page("boe.html")))
    assert ("2026-11-05-boe-decision", "2026-11-05T12:00") in boe
    assert ("2026-12-17-boe-decision", "2026-12-17T12:00") in boe  # "Thursday&nbsp;17 December"
    assert len(boe) == 16  # 2026 and 2027
    ecb = ids(src.parse_ecb(page("ecb.html")))
    assert ("2026-12-17-ecb-decision", "2026-12-17T14:15") in ecb
    for parse in (src.parse_rba, src.parse_boe):
        with pytest.raises(SourceError):
            parse("<html></html>")


def test_bea_advance_gdp_only() -> None:
    assert src.parse_bea(page("bea.html")) == []  # second and third estimates are not FTMO's
    advance = (
        "<p>Year 2027</p><p>January 28</p><p>8:30 AM</p><p>N</p><p>ews</p>"
        "<p>GDP (Advance Estimate), 4th Quarter and Year 2026</p>"
        "<p>February 25</p><p>8:30 AM</p><p>GDP (Second Estimate), 4th Quarter 2026</p>"
    )
    assert ids(src.parse_bea(advance)) == [("2027-01-28-us-gdp-advance", "2027-01-28T08:30")]


def test_eia_weekly_report_with_its_holiday_exceptions() -> None:
    found = src.parse_eia(page("eia.html"), date(2026, 11, 1), date(2026, 11, 30))
    assert ids(found) == [
        ("2026-11-04-us-oil-inventory", "2026-11-04T10:30"),
        ("2026-11-12-us-oil-inventory", "2026-11-12T12:00"),  # Veterans Day: Thursday 12:00
        ("2026-11-18-us-oil-inventory", "2026-11-18T10:30"),
        ("2026-11-25-us-oil-inventory", "2026-11-25T10:30"),
    ]
    assert found[0].event.scope.value == "oil_inventory" and found[0].event.currency is None


def test_collected_dates_agree_with_the_calendar_files() -> None:
    """The files (from the same pages, checked by hand) and the parsers give
    the same id and the same time for every release both carry."""
    files = {
        e.event.event_id: e.event.ts for c in load_calendars(DEFAULT_NEWS_DIR) for e in c.events
    }
    parsed = (
        src.parse_fed_month(page("fed-2026-november.html"), 2026, 11, "")
        + src.parse_fed_month(page("fed-2026-december.html"), 2026, 12, "")
        + src.parse_statcan(page("statcan.json"))
        + src.parse_stats_nz(page("stats-nz.ics"))
        + src.parse_ons(page("ons.json"), "")
        + src.parse_abs(page("abs-202611-rcc_economy.html"), "")
        + src.parse_abs(page("abs-202612-rcc_economy.html"), "")
        + src.parse_abs(page("abs-202612-rcc_labour.html"), "")
        + src.parse_boc(page("boc.html"))
        + src.parse_rba(page("rba.html"))
        + src.parse_snb(page("snb.html"))
        + src.parse_boe(page("boe.html"))
        + src.parse_ecb(page("ecb.html"))
        + src.parse_eia(page("eia.html"), date(2026, 11, 1), date(2026, 12, 31))
    )
    both = [c for c in parsed if c.event.event_id in files]
    assert len(both) >= 30
    assert [c.event.event_id for c in both if files[c.event.event_id] != c.event.ts] == []


# --- the collector ------------------------------------------------------------------------


def fixture_fetch(fail: frozenset[str] = frozenset()) -> Fetch:
    by_url = {
        src.fed_month_url(2026, 10): "fed-2026-november.html",
        src.fed_month_url(2026, 11): "fed-2026-november.html",
        src.fed_month_url(2026, 12): "fed-2026-december.html",
        src.STATCAN: "statcan.json",
        src.STATS_NZ: "stats-nz.ics",
        src.BOC: "boc.html",
        src.RBA: "rba.html",
        src.SNB: "snb.html",
        src.BOE: "boe.html",
        src.ECB: "ecb.html",
        src.BEA: "bea.html",
        src.EIA: "eia.html",
    }

    async def fetch(url: str) -> str:
        if any(f in url for f in fail):
            raise OSError("connection refused")
        if "api.beta.ons.gov.uk" in url:
            return page("ons.json")
        if "abs.gov.au" in url:
            if "202612/rcc_labour" in url:
                return page("abs-202612-rcc_labour.html")
            if "202612" in url:
                return page("abs-202612-rcc_economy.html")
            return page("abs-202611-rcc_economy.html")
        return page(by_url[url])

    return fetch


class Live:
    def __init__(self, held: dict[str, int] | None = None) -> None:
        self.held = held or {}

    async def news(self) -> list[dict[str, object]]:
        return [{"event_id": k, "start_ts": v} for k, v in self.held.items()]


def collector(
    live: Live, fail: frozenset[str] = frozenset()
) -> tuple[Collector, EngineGateway, MemoryLog]:
    gateway, log = EngineGateway(), MemoryLog()
    desk = NewsDesk(load_calendars(DEFAULT_NEWS_DIR), log, gateway)
    return Collector(desk, live, fixture_fetch(fail)), gateway, log


def test_a_run_sends_upcoming_events_once_and_survives_a_failing_source() -> None:
    run, gateway, log = collector(Live(), fail=frozenset({"snb.ch"}))
    report = asyncio.run(run.run(NOW))
    assert report.failures == (("snb", "OSError: connection refused"),)
    sent = {a.event_id for a in report.sent}
    assert {"2026-11-03-rba-decision", "2026-11-16-ca-cpi", "2026-12-09-fomc-decision"} <= sent
    assert "2026-12-10-snb-decision" not in sent  # its source failed
    assert not [e for e in sent if e > "2026-12-15"]  # beyond the 45-day horizon
    assert {(a.action, a.author, a.result) for a in report.sent} == {
        ("collector", SYSTEM_COLLECTOR, "done")
    }
    first = next(a for a in report.sent if a.event_id == "2026-11-16-ca-cpi")
    assert first.calendar_version == src.STATCAN and first.reason.startswith("official schedule:")
    window = gateway.machine.supervisor.engine.news_event("2026-11-16-ca-cpi")
    assert window is not None and "USD/CAD" in window.symbols and "EUR/USD" not in window.symbols
    again = asyncio.run(run.run(NOW + 60_000))
    assert again.sent == ()  # everything handled: nothing twice
    assert len(log.actions) == len(report.sent)


def test_a_cancelled_event_is_not_restored_and_a_moved_one_gets_a_new_window() -> None:
    moved_from = utc_ms("2026-11-16T13:00", "UTC")  # engine holds ca-cpi at another time
    run, _, _ = collector(Live({"2026-11-16-ca-cpi": moved_from}))
    desk = run.desk
    asyncio.run(desk.cancel("2026-11-03-rba-decision", Author("admin-1", None, "test"), NOW))
    report = asyncio.run(run.run(NOW))
    sent = {a.event_id for a in report.sent}
    assert "2026-11-03-rba-decision" not in sent  # the desk handled it (cancelled): left alone
    assert "2026-11-16-ca-cpi-at0830" in sent  # moved: a window at the publisher's time
    assert "2026-11-16-ca-cpi" not in sent
    assert report.moved == (
        ("2026-11-16-ca-cpi", moved_from, utc_ms("2026-11-16T13:30", "UTC") - 120_000),
    )


def test_every_source_has_a_parser_run() -> None:
    found, failures = asyncio.run(collect(fixture_fetch(), date(2026, 10, 31)))
    assert failures == []
    keys = {c.event.event_id.split("-", 3)[3] for c in found}
    assert keys >= {
        "fomc-minutes",
        "fomc-decision",
        "ca-cpi",
        "ca-employment",
        "nz-employment",
        "nz-gdp",
        "uk-cpi",
        "au-cpi",
        "au-gdp",
        "au-employment",
        "boc-decision",
        "rba-decision",
        "snb-decision",
        "boe-decision",
        "ecb-decision",
        "us-oil-inventory",
    }
    assert set(SOURCES) == {
        "federal_reserve",
        "statistics_canada",
        "stats_nz",
        "ons",
        "abs",
        "bank_of_canada",
        "rba",
        "snb",
        "bank_of_england",
        "ecb",
        "bea",
        "eia",
    }
