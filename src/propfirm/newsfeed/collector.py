"""The news collector: reads the publishers' own schedules and sends the
restricted events straight to the engine [decided 31 Oct 2026].

Each run (at API start, then every `PROPFIRM_COLLECT_HOURS`, default 6):
1. fetch every source (`propfirm.newsfeed.sources`); a source that fails
   is logged and skipped, the others still count;
2. keep events whose window has not started and that are at most
   `horizon_days` ahead (default 45);
3. send each event the desk has never handled (any action, any result:
   so a cancelled event is never restored) to the engine, logged as action
   `collector` with its source;
4. alert staff and admins about every source that could not be read
   (`propfirm.newsfeed.alerts`), so they add its events by hand;
5. when the engine already holds an event under the same id at another
   time (the publisher moved it), add a window at the new time as
   `<id>-at<HHMM>`, keep the old one (more restriction, never less) and log
   a warning: an admin decides whether to cancel the old one.

Requests identify the firm (`PROPFIRM_COLLECTOR_AGENT`); the schedule pages
read are allowed by each site's robots.txt (checked 31 Oct 2026). No
third-party calendar is read (licence, CC-5).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from propfirm.newsfeed import sources as src
from propfirm.newsfeed.alerts import SourceAlerts
from propfirm.newsfeed.sources import Collected, SourceError
from propfirm.settings.newsdesk import Author, NewsAction, NewsDesk

log = logging.getLogger(__name__)

SYSTEM_COLLECTOR = "system:collector"
DEFAULT_AGENT = "pipridge-news-collector/1.0"

type Fetch = Callable[[str], Awaitable[str]]


class LiveNews(Protocol):
    async def news(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RunReport:
    found: int
    sent: tuple[NewsAction, ...]
    failures: tuple[tuple[str, str], ...]  # (source, error)
    moved: tuple[tuple[str, int, int], ...] = field(default=())  # (id, engine start, new start)


def _months(today: date, count: int) -> list[tuple[int, int]]:
    out, y, m = [], today.year, today.month
    for _ in range(count):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


async def _fed(fetch: Fetch, today: date) -> list[Collected]:
    """The current month's page must exist; a later month's page is
    published only some weeks ahead, so its 404 means nothing yet."""
    out = []
    for i, (y, m) in enumerate(_months(today, 3)):
        url = src.fed_month_url(y, m)
        try:
            page = await fetch(url)
        except Exception as exc:
            if i > 0 and _not_found(exc):
                break
            raise
        out += src.parse_fed_month(page, y, m, url)
    return out


async def _abs(fetch: Fetch, today: date) -> list[Collected]:
    out = []
    for y, m in _months(today, 3):
        for theme in src.ABS_THEMES:
            url = src.ABS.format(yyyymm=f"{y}{m:02d}", theme=theme)
            out += src.parse_abs(await fetch(url), url)
    return out


async def _ons(fetch: Fetch, today: date) -> list[Collected]:
    url = src.ONS.format(start=today.isoformat(), end=(today + timedelta(days=90)).isoformat())
    return src.parse_ons(await fetch(url), url)


async def _eia(fetch: Fetch, today: date) -> list[Collected]:
    return src.parse_eia(await fetch(src.EIA), today, today + timedelta(days=90))


def _single(
    url: str, parse: Callable[[str, str], list[Collected]]
) -> Callable[..., Awaitable[list[Collected]]]:
    async def run(fetch: Fetch, today: date) -> list[Collected]:
        return parse(await fetch(url), url)

    return run


SOURCES: dict[str, Callable[[Fetch, date], Awaitable[list[Collected]]]] = {
    "federal_reserve": _fed,
    "statistics_canada": _single(src.STATCAN, src.parse_statcan),
    "stats_nz": _single(src.STATS_NZ, src.parse_stats_nz),
    "ons": _ons,
    "abs": _abs,
    "bank_of_canada": _single(src.BOC, src.parse_boc),
    "rba": _single(src.RBA, src.parse_rba),
    "snb": _single(src.SNB, src.parse_snb),
    "bank_of_england": _single(src.BOE, src.parse_boe),
    "ecb": _single(src.ECB, src.parse_ecb),
    "bea": _single(src.BEA, src.parse_bea),
    "eia": _eia,
    "bls": _single(src.BLS, src.parse_bls),
    "rbnz": _single(src.RBNZ, src.parse_rbnz),
}


async def collect(
    fetch: Fetch, today: date, names: Sequence[str] | None = None
) -> tuple[list[Collected], list[tuple[str, str]]]:
    """Every source's events (one per id), and the sources that failed."""
    found: list[Collected] = []
    failures: list[tuple[str, str]] = []
    for name in names or list(SOURCES):
        try:
            found += await SOURCES[name](fetch, today)
        except Exception as exc:  # one source's outage or page change never stops the rest
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            level = logging.ERROR if isinstance(exc, SourceError) else logging.WARNING
            log.log(level, "news source %s failed: %s", name, exc)
    return src.unique(found), failures


class Collector:
    def __init__(
        self,
        desk: NewsDesk,
        live: LiveNews,
        fetch: Fetch,
        horizon_days: int = 45,
        names: Sequence[str] | None = None,
        alerts: SourceAlerts | None = None,
    ) -> None:
        self.desk, self._live, self._fetch = desk, live, fetch
        self._horizon_ms = horizon_days * 86_400_000
        self._names = names
        self._alerts = alerts

    async def run(self, now: int) -> RunReport:
        today = datetime.fromtimestamp(now / 1000, UTC).date()
        found, failures = await collect(self._fetch, today, self._names)
        if self._alerts is not None:
            failed = {name for name, _ in failures}
            succeeded = [n for n in (self._names or list(SOURCES)) if n not in failed]
            await self._alerts.update(failures, succeeded, now)
        engine = {str(e["event_id"]): e.get("start_ts") for e in await self._live.news()}
        handled = await self.desk.known_ids()
        sent: list[NewsAction] = []
        moved: list[tuple[str, int, int]] = []
        for item in found:
            ev = item.event
            start = ev.ts - ev.before_ms
            if start <= now or ev.ts > now + self._horizon_ms:
                continue
            held = engine.get(ev.event_id)
            if isinstance(held, int) and held != start:
                revised = f"{ev.event_id}-at{item.local[11:13]}{item.local[14:16]}"
                moved.append((ev.event_id, held, start))
                log.warning(
                    "news event %s moved: engine window starts %d, publisher now %d; adding %s",
                    ev.event_id,
                    held,
                    start,
                    revised,
                )
                if revised in handled or revised in engine:
                    continue
                item = replace(item, event=replace(ev, event_id=revised))
            elif ev.event_id in handled or ev.event_id in engine:
                continue
            who = Author(SYSTEM_COLLECTOR, None, f"official schedule: {item.source}")
            sent.append(await self.desk.collected(item.event, item.title, item.source, who, now))
        return RunReport(len(found), tuple(sent), tuple(failures), tuple(moved))


async def collect_forever(collector: Collector, every_s: float, clock: Callable[[], int]) -> None:
    """Run now, then every `every_s` seconds; a failed run is logged and
    retried at the next."""
    while True:
        try:
            report = await collector.run(clock())
            log.info(
                "news collector: %d found, %d sent, %d sources failed",
                report.found,
                len(report.sent),
                len(report.failures),
            )
        except Exception:
            log.exception("news collector run failed")
        await asyncio.sleep(every_s)
