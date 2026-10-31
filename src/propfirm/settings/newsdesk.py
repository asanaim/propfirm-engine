"""The risk desk's restricted-events work (M13): publishing the monthly
calendar to the engine, manual windows, the no-fixed-time Bank of Japan
case, and cancellations. Every action is logged (`news_actions`, append-
only) with who, why, the input sent and the engine's answer.

Publication: every calendar file is published automatically when the API
starts (author `system:calendar`), so a new month goes live with the
deploy that adds its file [decided]. Events already in the engine are
skipped.

Who may do what [decided: only an admin cancels]:
- employees (staff) publish a calendar month again, add a manual window for an
  unscheduled event (with a reason), and mark a no-fixed-time event
  published: each only adds restrictions;
- removing a window lifts a restriction, so only an admin may cancel one,
  and only before it starts (the engine refuses later cancels).

Windows are always the [CONFIRMED] 2 minutes before to 2 minutes after; a
"published" event runs 2 minutes from the moment it is marked, with no
window before it [DEFAULT]. The engine decides: duplicates, unknown ids and
started windows come back as recorded rejections, so publishing a month
twice changes nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.journal.inputs import CancelNews, Input, ScheduleNews
from propfirm.pgschema import check_schema
from propfirm.rules.calendar import EVENT_ID, Calendar, CalendarEvent
from propfirm.rules.news import TWO_MINUTES_MS, EventScope, RestrictedEvent
from propfirm.service.gateway import Outcome


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...


class NewsRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NewsAction:
    id: int
    action: str  # calendar, manual, published, cancel
    event_id: str
    title: str | None
    calendar_version: str | None
    author: str
    author_email: str | None
    reason: str
    entry_id: str
    result: str  # done, rejected, pending (not journaled within the wait)
    detail: str | None  # the engine's reason when rejected
    ts: int


def _action(row: asyncpg.Record) -> NewsAction:
    return NewsAction(
        id=row["id"],
        action=row["action"],
        event_id=row["event_id"],
        title=row["title"],
        calendar_version=row["calendar_version"],
        author=row["author"],
        author_email=row["author_email"],
        reason=row["reason"],
        entry_id=row["entry_id"],
        result=row["result"],
        detail=row["detail"],
        ts=row["created_at"],
    )


class NewsStore:
    """`news_actions` in the settings schema (created by its migrations)."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = check_schema(schema)

    async def record(self, action: NewsAction) -> NewsAction:
        row = await self._pool.fetchrow(
            f"INSERT INTO {self._s}.news_actions (action, event_id, title, calendar_version, "
            f"author, author_email, reason, entry_id, result, detail, created_at) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *",
            action.action,
            action.event_id,
            action.title,
            action.calendar_version,
            action.author,
            action.author_email,
            action.reason,
            action.entry_id,
            action.result,
            action.detail,
            action.ts,
        )
        assert row is not None
        return _action(row)

    async def history(self, limit: int = 100) -> list[NewsAction]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.news_actions ORDER BY id DESC LIMIT $1", limit
        )
        return [_action(r) for r in rows]

    async def titles(self) -> dict[str, str]:
        """Titles of manual and published events, by event id (the first
        accepted action that named the id)."""
        rows = await self._pool.fetch(
            f"SELECT DISTINCT ON (event_id) event_id, title FROM {self._s}.news_actions "
            f"WHERE action IN ('manual', 'published') AND result = 'done' "
            f"AND title IS NOT NULL ORDER BY event_id, id"
        )
        return {r["event_id"]: r["title"] for r in rows}


class NewsLog(Protocol):
    async def record(self, action: NewsAction) -> NewsAction: ...

    async def history(self, limit: int = 100) -> list[NewsAction]: ...

    async def titles(self) -> dict[str, str]: ...


def _result(outcome: Outcome) -> tuple[str, str | None]:
    if outcome.seq is None:
        return "pending", None
    for event in outcome.events:
        if isinstance(event, dict) and event.get("type") == "InputRejected":
            return "rejected", str(event.get("reason"))
    return "done", None


@dataclass(frozen=True, slots=True)
class Author:
    subject: str
    email: str | None
    reason: str


class NewsDesk:
    def __init__(self, calendars: Sequence[Calendar], log: NewsLog, gateway: Submitter) -> None:
        self._calendars = {c.month: c for c in calendars}
        self._log = log
        self._gateway = gateway

    @property
    def calendars(self) -> tuple[Calendar, ...]:
        return tuple(self._calendars.values())

    async def titles(self) -> dict[str, str]:
        """Titles by event id: the calendar files' and the desk's own."""
        out = await self._log.titles()
        for calendar in self._calendars.values():
            out.update({e.event.event_id: e.title for e in calendar.events})
        return out

    async def history(self, limit: int = 100) -> list[NewsAction]:
        return await self._log.history(limit)

    async def publish(
        self, month: str, who: Author, now: int, skip: frozenset[str] = frozenset()
    ) -> list[NewsAction]:
        """Send every event of the month whose window has not started yet;
        windows already started or over, and ids in `skip` (already in the
        engine), are left out (not sent)."""
        calendar = self._calendars.get(month)
        if calendar is None:
            raise NewsRefused("unknown_calendar", f"no calendar file for {month}")
        out = []
        for item in calendar.events:
            if item.event.ts - item.event.before_ms <= now or item.event.event_id in skip:
                continue
            out.append(await self._send(item, calendar.version, who, now))
        return out

    async def publish_all(
        self, who: Author, now: int, skip: frozenset[str] = frozenset()
    ) -> list[NewsAction]:
        """Every calendar file, as at start-up [decided 2026-10-31: months
        publish automatically]. A cancelled event is not restored: the
        engine refuses an id it has ever scheduled."""
        out = []
        for month in self._calendars:
            out += await self.publish(month, who, now, skip)
        return out

    async def manual(
        self,
        event_id: str,
        title: str,
        scope: EventScope,
        currency: str | None,
        ts: int,
        who: Author,
        now: int,
    ) -> NewsAction:
        """A window for an event not in the calendar (emergency action): the
        standard 2 minutes either side of `ts`, which must not be over."""
        _check_id(event_id)
        if ts + TWO_MINUTES_MS < now:
            raise NewsRefused("window_over", "that window is already over")
        try:
            event = RestrictedEvent(event_id, ts, scope, currency)
        except (TypeError, ValueError) as exc:
            raise NewsRefused("invalid_event", str(exc)) from exc
        return await self._submit("manual", event, title, None, who, now)

    async def published(
        self, event_id: str, title: str, currency: str, who: Author, now: int
    ) -> NewsAction:
        """An event with no fixed time was just published (Bank of Japan):
        no trades on its symbols for the next 2 minutes."""
        _check_id(event_id)
        event = RestrictedEvent(event_id, now, EventScope.CURRENCY, currency, before_ms=0)
        return await self._submit("published", event, title, None, who, now)

    async def cancel(self, event_id: str, who: Author, now: int) -> NewsAction:
        outcome = await self._gateway.submit(CancelNews(event_id, now))
        return await self._record("cancel", event_id, None, None, who, outcome, now)

    async def _send(self, item: CalendarEvent, version: str, who: Author, now: int) -> NewsAction:
        return await self._submit("calendar", item.event, item.title, version, who, now)

    async def _submit(
        self,
        action: str,
        event: RestrictedEvent,
        title: str,
        version: str | None,
        who: Author,
        now: int,
    ) -> NewsAction:
        outcome = await self._gateway.submit(ScheduleNews(event, now))
        return await self._record(action, event.event_id, title, version, who, outcome, now)

    async def _record(
        self,
        action: str,
        event_id: str,
        title: str | None,
        version: str | None,
        who: Author,
        outcome: Outcome,
        now: int,
    ) -> NewsAction:
        result, detail = _result(outcome)
        return await self._log.record(
            NewsAction(
                id=0,
                action=action,
                event_id=event_id,
                title=title,
                calendar_version=version,
                author=who.subject,
                author_email=who.email,
                reason=who.reason,
                entry_id=outcome.entry_id,
                result=result,
                detail=detail,
                ts=now,
            )
        )


def _check_id(event_id: str) -> None:
    if not EVENT_ID.fullmatch(event_id):
        raise NewsRefused("invalid_event", f"event id must match {EVENT_ID.pattern}")


def action_view(action: NewsAction) -> dict[str, Any]:
    return {
        "id": action.id,
        "action": action.action,
        "event_id": action.event_id,
        "title": action.title,
        "calendar_version": action.calendar_version,
        "author": action.author,
        "author_email": action.author_email,
        "reason": action.reason,
        "entry_id": action.entry_id,
        "result": action.result,
        "detail": action.detail,
        "ts": action.ts,
    }
