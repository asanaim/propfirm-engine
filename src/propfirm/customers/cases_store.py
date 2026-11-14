"""Cases and alerts in PostgreSQL (the customers schema, migration 5).

`case_events` and `alerts` are the record (append-only, trigger-enforced);
the status columns of `cases` are a cache of `case_state`, written in the
same transaction as each event. A partial unique index keeps one case not
closed per customer, so concurrent alerts cannot open two.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from propfirm.customers.cases import (
    CASE_KINDS,
    CaseError,
    CaseEvent,
    case_state,
)
from propfirm.journal.machine import CODEC
from propfirm.pgschema import Conn, check_schema
from propfirm.rules.events import NewsViolation

OPEN = ("open", "decision_pending", "actioning")


@dataclass(frozen=True, slots=True)
class CaseRecord:
    cas: str
    cif: str
    opened_at: int
    status: str
    severity: str
    due_at: int
    assignee: str | None
    outcome: str | None
    proposed_by: str | None
    closed_at: int | None


@dataclass(frozen=True, slots=True)
class Alert:
    alr: str
    cas: str
    cif: str
    acc: str | None
    source: str
    kind: str
    severity: str
    ts: int
    detail: dict[str, Any]


class CaseRefused(Exception):
    """A request the case's state or the rules refuse (the code says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _record(row: asyncpg.Record) -> CaseRecord:
    return CaseRecord(**dict(row))


log = logging.getLogger(__name__)

NEWS_SOURCE = "news_rule"
NEWS_SEVERITY = "high"  # [DEFAULT] as FTMO, a violation can end a funded account


class CaseStore:
    def __init__(self, pool: asyncpg.Pool, schema: str, journal_schema: str | None = None) -> None:
        """The customers schema must be migrated (CustomerStore.open);
        `journal_schema` is the engine's, read for news violations."""
        self._pool = pool
        self._s = check_schema(schema)
        self._j = None if journal_schema is None else check_schema(journal_schema)

    # --- alerts -----------------------------------------------------------------------

    async def raise_alert(
        self,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        detail: dict[str, Any],
        due_at: int,
        ts: int,
    ) -> tuple[str, str]:
        """Record an alert on the customer's case not closed, or on a new
        case; return (CAS, ALR)."""
        async with self._pool.acquire() as conn, conn.transaction():
            return await self._raise(conn, cif, acc, source, kind, severity, detail, due_at, ts)

    async def has_alert(self, acc: str, source: str, key: str) -> bool:
        """Whether an alert from `source` with this `key` in its detail was
        raised on the account (one alert per payout period, say)."""
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.alerts WHERE acc = $1 AND source = $2 "
            f"AND detail->>'key' = $3 LIMIT 1",
            acc,
            source,
            key,
        )
        return found is not None

    async def alert_news_violations(
        self, due: Callable[[str, int], int], ts: int, limit: int = 500
    ) -> list[str]:
        """Raise a high-severity integrity alert for every NewsViolation the
        engine journaled after the cursor, cursor moved in the same
        transaction (never missed or doubled); return the ALR ids. None
        while another caller holds the cursor."""
        if self._j is None:
            raise RuntimeError("no journal schema given")
        s, j = self._s, self._j
        raised: list[str] = []
        async with self._pool.acquire() as conn, conn.transaction():
            after = await conn.fetchval(
                f"SELECT seq FROM {s}.news_cursor WHERE id = 1 FOR UPDATE SKIP LOCKED"
            )
            if after is None:
                return raised
            if await conn.fetchval("SELECT to_regclass($1)", f"{j}.journal") is None:
                return raised
            rows = await conn.fetch(
                f"SELECT seq, events FROM {j}.journal WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                limit,
            )
            for row in rows:
                events = CODEC.decode(row["events"])
                assert isinstance(events, tuple)
                for e in events:
                    if not isinstance(e, NewsViolation):
                        continue
                    cif = await conn.fetchval(
                        f"SELECT cif FROM {s}.accounts WHERE acc = $1", e.account_id
                    )
                    if cif is None:
                        log.warning("news violation on %s, no customer record", e.account_id)
                        continue
                    detail = {
                        "rule": "restricted news window",
                        "journal_seq": row["seq"],
                        "position_id": e.position_id,
                        "close_id": e.close_id,
                        "symbol": e.symbol,
                        "fill": e.reason,
                        "pnl_cents": e.pnl_cents,
                        "events": list(e.event_ids),
                        "rsv_id": e.rsv_id,
                        "at": e.ts,
                    }
                    _, alr = await self._raise(
                        conn,
                        cif,
                        e.account_id,
                        NEWS_SOURCE,
                        "integrity",
                        NEWS_SEVERITY,
                        detail,
                        due(NEWS_SEVERITY, ts),
                        ts,
                    )
                    raised.append(alr)
            if rows:
                await conn.execute(
                    f"UPDATE {s}.news_cursor SET seq = $1 WHERE id = 1", rows[-1]["seq"]
                )
        return raised

    async def _raise(
        self,
        conn: Conn,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        detail: dict[str, Any],
        due_at: int,
        ts: int,
    ) -> tuple[str, str]:
        s = self._s
        await conn.execute(f"SELECT 1 FROM {s}.cif WHERE cif = $1 FOR UPDATE", cif)
        row = await conn.fetchrow(
            f"SELECT * FROM {s}.cases WHERE cif = $1 AND status <> 'closed' FOR UPDATE", cif
        )
        alr = f"ALR{await conn.fetchval(f"SELECT nextval('{s}.alr_seq')"):08d}"
        joined: dict[str, Any] = {
            "alr": alr,
            "source": source,
            "kind": kind,
            "severity": severity,
            "acc": acc,
            "due_at": due_at,
        }
        if row is not None and row["status"] == "actioning":
            cas = row["cas"]  # a decision is being carried out: the alert waits on record
            await self._insert_alert(conn, alr, cas, cif, acc, source, kind, severity, ts, detail)
            await self._append(conn, cas, "note", ts, {"by": source, "alert": joined})
            return cas, alr
        if row is None:
            cas = f"CAS{await conn.fetchval(f"SELECT nextval('{s}.cas_seq')"):08d}"
            await conn.execute(
                f"INSERT INTO {s}.cases (cas, cif, opened_at, status, severity, due_at) "
                f"VALUES ($1, $2, $3, 'open', $4, $5)",
                cas,
                cif,
                ts,
                severity,
                due_at,
            )
            await self._insert_alert(conn, alr, cas, cif, acc, source, kind, severity, ts, detail)
            opened = {"cif": cif, "kind": kind, "severity": severity, "due_at": due_at}
            await self._append(conn, cas, "opened", ts, opened)
        else:
            cas = row["cas"]
            await self._insert_alert(conn, alr, cas, cif, acc, source, kind, severity, ts, detail)
        await self._append(conn, cas, "alert", ts, joined)
        return cas, alr

    async def _insert_alert(
        self,
        conn: Conn,
        alr: str,
        cas: str,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        ts: int,
        detail: dict[str, Any],
    ) -> None:
        await conn.execute(
            f"INSERT INTO {self._s}.alerts (alr, cas, cif, acc, source, kind, severity, ts, "
            f"detail) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)",
            alr,
            cas,
            cif,
            acc,
            source,
            kind,
            severity,
            ts,
            json.dumps(detail, sort_keys=True),
        )

    # --- reading ----------------------------------------------------------------------

    async def case(self, cas: str) -> CaseRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.cases WHERE cas = $1", cas)
        return None if row is None else _record(row)

    async def cases(
        self,
        status: str | None = None,
        cif: str | None = None,
        due_before: int | None = None,
        limit: int = 200,
    ) -> list[CaseRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.cases WHERE ($1::text IS NULL OR status = $1) "
            f"AND ($2::text IS NULL OR cif = $2) "
            f"AND ($3::bigint IS NULL OR (status <> 'closed' AND due_at < $3)) "
            f"ORDER BY due_at, cas LIMIT $4",
            status,
            cif,
            due_before,
            limit,
        )
        return [_record(r) for r in rows]

    async def open_case(self, cif: str) -> CaseRecord | None:
        """The customer's case not closed, if any."""
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.cases WHERE cif = $1 AND status <> 'closed'", cif
        )
        return None if row is None else _record(row)

    async def alerts(self, cas: str) -> list[Alert]:
        rows = await self._pool.fetch(
            f"SELECT alr, cas, cif, acc, source, kind, severity, ts, detail::text AS detail "
            f"FROM {self._s}.alerts WHERE cas = $1 ORDER BY alr",
            cas,
        )
        return [Alert(**{**dict(r), "detail": json.loads(r["detail"])}) for r in rows]

    async def history(self, cas: str) -> list[CaseEvent]:
        async with self._pool.acquire() as conn:
            return await self._events(conn, cas)

    async def removed_profit(self, acc: str) -> int:
        """Profit removed from `acc` by confirmed, carried-out decisions (V)."""
        rows = await self._pool.fetch(
            f"SELECT detail::text AS detail FROM {self._s}.case_events "
            f"WHERE kind = 'applied' AND detail->'action'->>'action' = 'remove_profit' "
            f"AND detail->'action'->>'acc' = $1",
            acc,
        )
        return sum(int(json.loads(r["detail"])["action"]["amount_cents"]) for r in rows)

    # --- steps ------------------------------------------------------------------------

    async def act(
        self, cas: str, kind: str, staff: str, ts: int, detail: dict[str, Any]
    ) -> CaseRecord:
        """Record an employee's step. Refused when the case is about the
        employee's own customer record, or the history does not allow it."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {s}.cases WHERE cas = $1 FOR UPDATE", cas)
            if row is None:
                raise CaseRefused("unknown", f"no case {cas}")
            own = await conn.fetchval(f"SELECT cif FROM {s}.cif WHERE subject = $1", staff)
            if own == row["cif"]:
                raise CaseRefused("own_case", "nobody handles a case about themselves")
            try:
                async with conn.transaction():  # a refused step leaves nothing behind
                    return await self._append(conn, cas, kind, ts, {"by": staff, **detail})
            except CaseError as exc:
                raise CaseRefused("not_allowed", str(exc)) from exc
            except asyncpg.UniqueViolationError as exc:  # reopening beside another case
                raise CaseRefused("another_case_open", "the customer has another case") from exc

    async def applied(
        self, cas: str, index: int, action: dict[str, Any], result: dict[str, Any], ts: int
    ) -> CaseRecord:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SELECT 1 FROM {self._s}.cases WHERE cas = $1 FOR UPDATE", cas)
            detail = {"index": index, "action": action, "result": result}
            return await self._append(conn, cas, "applied", ts, detail)

    async def close_carried_out(self, cas: str, ts: int) -> CaseRecord | None:
        """actioning -> closed once every action is carried out."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {self._s}.cases WHERE cas = $1 FOR UPDATE", cas
            )
            assert row is not None
            state = case_state(cas, await self._events(conn, cas))
            if state.status != "actioning" or state.pending_actions:
                return None
            return await self._append(
                conn, cas, "closed", ts, {"by": "platform", "outcome": "actioned"}
            )

    async def pending_actions(self, cas: str) -> tuple[int, ...]:
        async with self._pool.acquire() as conn:
            return case_state(cas, await self._events(conn, cas)).pending_actions

    # --- cache --------------------------------------------------------------------

    async def rebuild_status(self) -> list[str]:
        fixed: list[str] = []
        ids = [
            r["cas"]
            for r in await self._pool.fetch(f"SELECT cas FROM {self._s}.cases ORDER BY cas")
        ]
        for cas in ids:
            async with self._pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(
                    f"SELECT * FROM {self._s}.cases WHERE cas = $1 FOR UPDATE", cas
                )
                assert row is not None
                if await self._write_cache(conn, cas) != _record(row):
                    fixed.append(cas)
        return fixed

    async def _events(self, conn: Conn, cas: str) -> list[CaseEvent]:
        rows = await conn.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.case_events "
            f"WHERE cas = $1 ORDER BY id",
            cas,
        )
        return [CaseEvent(cas, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def _append(
        self, conn: Conn, cas: str, kind: str, ts: int, detail: dict[str, Any]
    ) -> CaseRecord:
        assert kind in CASE_KINDS
        await conn.execute(
            f"INSERT INTO {self._s}.case_events (cas, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            cas,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        return await self._write_cache(conn, cas)

    async def _write_cache(self, conn: Conn, cas: str) -> CaseRecord:
        state = case_state(cas, await self._events(conn, cas))
        row = await conn.fetchrow(
            f"UPDATE {self._s}.cases SET status = $2, severity = $3, due_at = $4, "
            f"assignee = $5, outcome = $6, proposed_by = $7, closed_at = $8 "
            f"WHERE cas = $1 RETURNING *",
            cas,
            state.status,
            state.severity,
            state.due_at,
            state.assignee,
            state.outcome,
            state.proposed_by,
            state.closed_at,
        )
        assert row is not None
        return _record(row)
