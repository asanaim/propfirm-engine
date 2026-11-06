"""Alerts when a news source cannot be read [decided 31 Oct 2026].

When the collector cannot read a publisher's schedule (refused, down, or a
page whose layout changed), staff and admins are told which restricted
events are no longer collected, so they add them by hand
(`POST /staff/news/events`). Each source has one state, kept as an
append-only record (`settings.news_source_events`):

- failed: the first failed run after success (or ever): email at once;
- reminded: still failing `remind_hours` (default 24) after the last email:
  email again;
- recovered: the first successful run after a failure: email that the
  source is read again.

Recipients [staff spec 5b]: whoever holds the risk desk's news permission
now (`news.desk`: the risk desk and admins, from the staff directory), plus
`NEWS_ALERT_EMAILS` (kept for logins not yet in the directory). Each failure
is also an in-app notification to the same holders, closed when the source
is read again. An email that cannot be sent is logged; the alert still
stands and shows in `GET /staff/news`.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from propfirm.customers.notifications import Mailer
from propfirm.newsfeed import sources as src
from propfirm.pgschema import check_schema

log = logging.getLogger(__name__)

# what each source supplies, and the page staff read instead
SOURCE_INFO: dict[str, tuple[tuple[str, ...], str]] = {
    "federal_reserve": (
        ("fomc-decision", "fomc-minutes"),
        "https://www.federalreserve.gov/newsevents.htm",
    ),
    "statistics_canada": (
        ("ca-cpi", "ca-employment"),
        "https://www150.statcan.gc.ca/n1/dai-quo/cal1-eng.htm",
    ),
    "stats_nz": (
        ("nz-employment", "nz-gdp", "nz-cpi"),
        "https://www.stats.govt.nz/release-calendar/",
    ),
    "ons": (("uk-cpi",), "https://www.ons.gov.uk/releasecalendar"),
    "abs": (
        ("au-cpi", "au-employment", "au-gdp"),
        "https://www.abs.gov.au/release-calendar/future-releases",
    ),
    "bank_of_canada": (("boc-decision",), src.BOC),
    "rba": (("rba-decision",), src.RBA),
    "snb": (("snb-decision",), src.SNB),
    "bank_of_england": (("boe-decision",), src.BOE),
    "ecb": (("ecb-decision",), src.ECB),
    "bea": (("us-gdp-advance",), src.BEA),
    "eia": (("us-oil-inventory",), src.EIA),
    "bls": (("us-employment", "us-cpi"), "https://www.bls.gov/schedule/"),
    "rbnz": (("rbnz-decision",), src.RBNZ),
}


@dataclass(frozen=True, slots=True)
class SourceEvent:
    source: str
    kind: str  # failed, reminded, recovered
    error: str | None
    notified: int
    ts: int


@dataclass(frozen=True, slots=True)
class OpenAlert:
    source: str
    since: int  # when it first failed
    last_error: str | None
    last_notice: int  # when staff were last emailed (or alerted)
    events: tuple[str, ...]  # titles of what it no longer collects
    page: str


class SourceLog(Protocol):
    async def record(self, event: SourceEvent) -> None: ...

    async def latest(self) -> dict[str, list[SourceEvent]]: ...


class Notices(Protocol):
    """In-app notifications (`propfirm.staff.desk.StaffDesk`)."""

    async def notify(
        self,
        audience: str,
        kind: str,
        ref: str,
        title: str,
        body: str,
        ts: int,
        urgent: bool = False,
    ) -> int: ...

    async def close(self, ref: str, reason: str, ts: int, kind: str | None = None) -> int: ...


NOTICE_KIND = "news_source"
AUDIENCE = "news.desk"  # the risk desk and admins


class SourceStore:
    """`news_source_events` in the settings schema (its migrations)."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = check_schema(schema)

    async def record(self, event: SourceEvent) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.news_source_events (source, kind, error, notified, ts) "
            f"VALUES ($1, $2, $3, $4, $5)",
            event.source,
            event.kind,
            event.error,
            event.notified,
            event.ts,
        )

    async def latest(self) -> dict[str, list[SourceEvent]]:
        """Per source, its events since (and including) its latest failure
        run: enough to know its state and when it was last notified."""
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.news_source_events e WHERE id >= coalesce(("
            f"SELECT max(id) FROM {self._s}.news_source_events r "
            f"WHERE r.source = e.source AND r.kind IN ('failed', 'recovered')), 0) ORDER BY id"
        )
        out: dict[str, list[SourceEvent]] = {}
        for r in rows:
            out.setdefault(r["source"], []).append(
                SourceEvent(r["source"], r["kind"], r["error"], r["notified"], r["ts"])
            )
        return out


def _titles(source: str) -> tuple[str, ...]:
    keys, _ = SOURCE_INFO.get(source, ((), ""))
    return tuple(src.KINDS[k][0] for k in keys)


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _body(source: str, error: str | None, since: int, reminder: bool) -> str:
    _, page = SOURCE_INFO.get(source, ((), ""))
    lines = [
        f"The news collector cannot read {source} (since {_utc(since)}).",
        f"Error: {error or 'unknown'}",
        "",
        "These restricted events are not being collected from it:",
        *[f"- {t}" for t in _titles(source)],
        "",
        f"Check the publisher's own schedule ({page}) and add each upcoming",
        "release by hand: POST /staff/news/events (event id, title, currency,",
        "release time, reason). Events in the monthly files (config/news) are",
        "already in the engine.",
    ]
    if reminder:
        lines.insert(0, "Reminder: still failing.")
    return "\n".join(lines)


class SourceAlerts:
    def __init__(
        self,
        log_store: SourceLog,
        mailer: Mailer | None,
        recipients: Sequence[str],
        remind_hours: int = 24,
        *,
        holders: Callable[[str, int], Awaitable[Sequence[str]]] | None = None,
        notices: Notices | None = None,
    ) -> None:
        self._log = log_store
        self._mailer = mailer
        self._to = tuple(recipients)
        self._remind_ms = remind_hours * 3_600_000
        self._holders = holders
        self._notices = notices

    async def _recipients(self, now: int) -> list[str]:
        found = list(self._to)
        if self._holders is not None:
            try:
                found += await self._holders(AUDIENCE, now)
            except Exception:
                log.exception("news source alert: staff directory not read")
        return sorted(set(found))

    async def _email(self, subject: str, body: str, now: int = 0) -> int:
        if self._mailer is None:
            return 0
        sent = 0
        for to in await self._recipients(now):
            try:
                await self._mailer.send(to, subject, body)
                sent += 1
            except Exception:
                log.exception("news source alert to %s not sent", to)
        return sent

    async def update(
        self, failures: Iterable[tuple[str, str]], succeeded: Iterable[str], now: int
    ) -> list[SourceEvent]:
        """Record and email what changed after a collector run."""
        history = await self._log.latest()
        out: list[SourceEvent] = []
        for source, error in failures:
            events = history.get(source, [])
            last = events[-1] if events else None
            if last is None or last.kind == "recovered":
                subject = f"News source not read: {source}"
                body = _body(source, error, now, False)
                sent = await self._email(subject, body, now)
                out.append(SourceEvent(source, "failed", error, sent, now))
                await self._notice(source, subject, body, now)
            elif now - last.ts >= self._remind_ms:
                since = events[0].ts
                subject = f"Reminder: news source not read: {source}"
                sent = await self._email(subject, _body(source, error, since, True), now)
                out.append(SourceEvent(source, "reminded", error, sent, now))
        for source in succeeded:
            events = history.get(source, [])
            if events and events[-1].kind in ("failed", "reminded"):
                sent = await self._email(
                    f"News source read again: {source}",
                    f"The news collector reads {source} again ({_utc(now)}); "
                    "its events are collected automatically from now on.",
                    now,
                )
                out.append(SourceEvent(source, "recovered", None, sent, now))
                if self._notices is not None:
                    try:
                        await self._notices.close(
                            f"news-source:{source}", "source read again", now, NOTICE_KIND
                        )
                    except Exception:
                        log.exception("news source %s: notification not closed", source)
        for event in out:
            await self._log.record(event)
            log.warning("news source %s: %s (%s)", event.source, event.kind, event.error)
        return out

    async def _notice(self, source: str, title: str, body: str, now: int) -> None:
        if self._notices is None:
            return
        try:
            await self._notices.notify(
                AUDIENCE, NOTICE_KIND, f"news-source:{source}", title, body, now, urgent=True
            )
        except Exception:
            log.exception("news source %s: notification not recorded", source)

    async def open_alerts(self) -> list[OpenAlert]:
        out = []
        for source, events in sorted((await self._log.latest()).items()):
            if not events or events[-1].kind == "recovered":
                continue
            _, page = SOURCE_INFO.get(source, ((), ""))
            out.append(
                OpenAlert(
                    source,
                    events[0].ts,
                    events[-1].error,
                    events[-1].ts,
                    _titles(source),
                    page,
                )
            )
        return out


def alert_view(alert: OpenAlert) -> dict[str, Any]:
    return {
        "source": alert.source,
        "since": alert.since,
        "last_error": alert.last_error,
        "last_notice": alert.last_notice,
        "events": list(alert.events),
        "page": alert.page,
    }
