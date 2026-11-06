"""The staff directory in PostgreSQL (schema `staff` by default).

`member_events` and `grant_events` are the record (append-only, enforced
by a trigger); `members` and `grants` are caches of `member_state` and
`grant_state`, written in the same transaction as each event. Every change
runs under one advisory lock, so a check and the write it allows cannot
interleave with another change (directory changes are rare).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

import asyncpg

from propfirm.pgschema import Conn, check_schema
from propfirm.pgschema import migrate as _migrate
from propfirm.staff.records import (
    GRANT_KINDS,
    MEMBER_KINDS,
    Cutoff,
    Grant,
    GrantEvent,
    Member,
    MemberEvent,
    fold_cutoffs,
    grant_state,
    member_state,
)

_LOCK_CLASS = 0x73746166  # "staf"

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE SEQUENCE {s}.staff_numbers;
        CREATE SEQUENCE {s}.apprentice_numbers;
        CREATE SEQUENCE {s}.grant_numbers;
        CREATE TABLE {s}.member_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            sid text NOT NULL CHECK (sid ~ '^(STF|APP)[0-9]{{8}}$'),
            kind text NOT NULL
                CHECK (kind IN ('joined', 'moved', 'recommended', 'graduated', 'left')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX member_events_sid ON {s}.member_events (sid, id);
        CREATE TABLE {s}.grant_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            grt text NOT NULL CHECK (grt ~ '^GRT[0-9]{{8}}$'),
            kind text NOT NULL CHECK (kind IN
                ('requested', 'approved', 'denied', 'cancelled', 'revoked', 'superseded')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX grant_events_grt ON {s}.grant_events (grt, id);
        CREATE FUNCTION {s}.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'staff records are append-only';
        END $$;
        CREATE TRIGGER member_events_append_only BEFORE UPDATE OR DELETE ON {s}.member_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER grant_events_append_only BEFORE UPDATE OR DELETE ON {s}.grant_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        -- caches, rebuilt from the events
        CREATE TABLE {s}.members (
            sid text PRIMARY KEY,
            subject text NOT NULL UNIQUE,
            name text NOT NULL,
            email text,
            kind text NOT NULL CHECK (kind IN ('staff', 'apprentice', 'contractor')),
            team text,
            lead text,
            mentor text,
            starts_at bigint NOT NULL,
            ends_at bigint,
            status text NOT NULL CHECK (status IN ('active', 'left', 'graduated')),
            from_sid text,
            to_sid text,
            recommended_by text
        );
        CREATE TABLE {s}.grants (
            grt text PRIMARY KEY,
            sid text NOT NULL REFERENCES {s}.members (sid),
            grant_kind text NOT NULL CHECK (grant_kind IN ('tier', 'role', 'power', 'acting')),
            value jsonb NOT NULL,
            starts_at bigint NOT NULL,
            ends_at bigint,
            status text NOT NULL,
            requested_by text NOT NULL,
            requester_tier int,
            requested_at bigint NOT NULL,
            reason text NOT NULL,
            approved_by text,
            approved_at bigint,
            ended_by text,
            ended_at bigint
        );
        CREATE INDEX grants_sid ON {s}.grants (sid);
        CREATE INDEX grants_status ON {s}.grants (status);
        """,
    ),
    (
        2,
        """
        -- the staff page (5b): duty acknowledgements, notifications, messages
        CREATE TABLE {s}.duty_acks (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            subject text NOT NULL,
            sheet text NOT NULL,
            version int NOT NULL,
            ts bigint NOT NULL,
            UNIQUE (subject, sheet, version)
        );
        CREATE TABLE {s}.notifications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            audience text NOT NULL,  -- a permission (its holders now) or "member:<STF/APP>"
            kind text NOT NULL,
            ref text NOT NULL,  -- what it is about: a record id, or e.g. news-source:bls
            title text NOT NULL,
            body text NOT NULL,
            urgent boolean NOT NULL,
            ts bigint NOT NULL
        );
        CREATE INDEX notifications_ref ON {s}.notifications (ref);
        CREATE TABLE {s}.notification_acks (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            notification bigint NOT NULL REFERENCES {s}.notifications (id),
            subject text NOT NULL,
            ts bigint NOT NULL,
            UNIQUE (notification, subject)
        );
        CREATE TABLE {s}.notification_closes (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            notification bigint NOT NULL UNIQUE REFERENCES {s}.notifications (id),
            reason text NOT NULL,
            ts bigint NOT NULL
        );
        CREATE TABLE {s}.messages (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ref text NOT NULL,
            subject text NOT NULL,
            sid text,
            body text NOT NULL,
            ts bigint NOT NULL
        );
        CREATE INDEX messages_ref ON {s}.messages (ref, id);
        CREATE TRIGGER duty_acks_append_only BEFORE UPDATE OR DELETE ON {s}.duty_acks
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER notifications_append_only BEFORE UPDATE OR DELETE ON {s}.notifications
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER notification_acks_append_only
            BEFORE UPDATE OR DELETE ON {s}.notification_acks
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER notification_closes_append_only
            BEFORE UPDATE OR DELETE ON {s}.notification_closes
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER messages_append_only BEFORE UPDATE OR DELETE ON {s}.messages
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        3,
        """
        -- leavers' logins disabled at the identity provider (5d)
        CREATE TABLE {s}.access_cutoffs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            sid text NOT NULL,
            subject text NOT NULL,
            kind text NOT NULL CHECK (kind IN
                ('requested', 'done', 'failed', 'alerted', 'overdue', 'confirmed')),
            detail text,
            ts bigint NOT NULL
        );
        CREATE UNIQUE INDEX access_cutoffs_one_request ON {s}.access_cutoffs (sid)
            WHERE kind = 'requested';
        CREATE TRIGGER access_cutoffs_append_only BEFORE UPDATE OR DELETE ON {s}.access_cutoffs
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
)


async def migrate(conn: Conn, schema: str) -> list[int]:
    return await _migrate(conn, schema, _MIGRATIONS, _LOCK_CLASS)


def _member(row: asyncpg.Record) -> Member:
    return Member(
        row["sid"],
        row["subject"],
        row["name"],
        row["email"],
        row["kind"],
        row["team"],
        row["lead"],
        row["mentor"],
        row["starts_at"],
        row["ends_at"],
        row["status"],
        row["from_sid"],
        row["to_sid"],
        row["recommended_by"],
    )


def _grant(row: asyncpg.Record) -> Grant:
    return Grant(
        row["grt"],
        row["sid"],
        row["grant_kind"],
        json.loads(row["value"]),
        row["starts_at"],
        row["ends_at"],
        row["status"],
        row["requested_by"],
        row["requester_tier"],
        row["requested_at"],
        row["reason"],
        row["approved_by"],
        row["approved_at"],
        row["ended_by"],
        row["ended_at"],
    )


class StaffOps:
    """Reads and writes on one connection (inside `StaffStore.change` for
    writes)."""

    def __init__(self, conn: Conn, schema: str) -> None:
        self._c = conn
        self._s = schema

    # --- reads ------------------------------------------------------------------------

    async def member(self, sid: str) -> Member | None:
        row = await self._c.fetchrow(f"SELECT * FROM {self._s}.members WHERE sid = $1", sid)
        return None if row is None else _member(row)

    async def member_by_subject(self, subject: str) -> Member | None:
        row = await self._c.fetchrow(f"SELECT * FROM {self._s}.members WHERE subject = $1", subject)
        return None if row is None else _member(row)

    async def members(self, status: str | None = None) -> list[Member]:
        rows = await self._c.fetch(
            f"SELECT * FROM {self._s}.members WHERE $1::text IS NULL OR status = $1 ORDER BY sid",
            status,
        )
        return [_member(r) for r in rows]

    async def grant(self, grt: str) -> Grant | None:
        row = await self._c.fetchrow(f"SELECT * FROM {self._s}.grants WHERE grt = $1", grt)
        return None if row is None else _grant(row)

    async def grants_of(self, sid: str) -> list[Grant]:
        rows = await self._c.fetch(
            f"SELECT * FROM {self._s}.grants WHERE sid = $1 ORDER BY grt", sid
        )
        return [_grant(r) for r in rows]

    async def grants(self, status: str | None = None) -> list[Grant]:
        rows = await self._c.fetch(
            f"SELECT * FROM {self._s}.grants WHERE $1::text IS NULL OR status = $1 ORDER BY grt",
            status,
        )
        return [_grant(r) for r in rows]

    async def member_history(self, sid: str) -> list[MemberEvent]:
        rows = await self._c.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.member_events "
            f"WHERE sid = $1 ORDER BY id",
            sid,
        )
        return [MemberEvent(sid, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def grant_history(self, grt: str) -> list[GrantEvent]:
        rows = await self._c.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.grant_events "
            f"WHERE grt = $1 ORDER BY id",
            grt,
        )
        return [GrantEvent(grt, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def cutoffs(self) -> list[Cutoff]:
        rows = await self._c.fetch(
            f"SELECT sid, subject, kind, detail, ts FROM {self._s}.access_cutoffs ORDER BY id"
        )
        return fold_cutoffs(
            [(r["sid"], r["subject"], r["kind"], r["detail"], r["ts"]) for r in rows]
        )

    # --- writes -----------------------------------------------------------------------

    async def cutoff_event(
        self, sid: str, subject: str, kind: str, detail: str | None, ts: int
    ) -> None:
        await self._c.execute(
            f"INSERT INTO {self._s}.access_cutoffs (sid, subject, kind, detail, ts) "
            f"VALUES ($1, $2, $3, $4, $5)",
            sid,
            subject,
            kind,
            detail,
            ts,
        )

    async def new_sid(self, kind: str) -> str:
        prefix, seq = (
            ("APP", "apprentice_numbers") if kind == "apprentice" else ("STF", "staff_numbers")
        )
        number = await self._c.fetchval(f"SELECT nextval('{self._s}.{seq}')")
        return f"{prefix}{number:08d}"

    async def new_grt(self) -> str:
        number = await self._c.fetchval(f"SELECT nextval('{self._s}.grant_numbers')")
        return f"GRT{number:08d}"

    async def member_event(self, sid: str, kind: str, ts: int, detail: dict[str, Any]) -> Member:
        assert kind in MEMBER_KINDS
        await self._c.execute(
            f"INSERT INTO {self._s}.member_events (sid, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            sid,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        m = member_state(sid, await self.member_history(sid))
        await self._c.execute(
            f"INSERT INTO {self._s}.members (sid, subject, name, email, kind, team, lead, mentor, "
            f"starts_at, ends_at, status, from_sid, to_sid, recommended_by) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) "
            f"ON CONFLICT (sid) DO UPDATE SET team = $6, lead = $7, mentor = $8, "
            f"ends_at = $10, status = $11, to_sid = $13, recommended_by = $14",
            m.sid,
            m.subject,
            m.name,
            m.email,
            m.kind,
            m.team,
            m.lead,
            m.mentor,
            m.starts_at,
            m.ends_at,
            m.status,
            m.from_sid,
            m.to_sid,
            m.recommended_by,
        )
        return m

    async def grant_event(self, grt: str, kind: str, ts: int, detail: dict[str, Any]) -> Grant:
        assert kind in GRANT_KINDS
        await self._c.execute(
            f"INSERT INTO {self._s}.grant_events (grt, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            grt,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        g = grant_state(grt, await self.grant_history(grt))
        await self._c.execute(
            f"INSERT INTO {self._s}.grants (grt, sid, grant_kind, value, starts_at, ends_at, "
            f"status, requested_by, requester_tier, requested_at, reason, approved_by, "
            f"approved_at, ended_by, ended_at) "
            f"VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15) "
            f"ON CONFLICT (grt) DO UPDATE SET status = $7, approved_by = $12, "
            f"approved_at = $13, ended_by = $14, ended_at = $15",
            g.grt,
            g.sid,
            g.grant,
            json.dumps(g.value),
            g.starts_at,
            g.ends_at,
            g.status,
            g.requested_by,
            g.requester_tier,
            g.requested_at,
            g.reason,
            g.approved_by,
            g.approved_at,
            g.ended_by,
            g.ended_at,
        )
        return g


class StaffStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = check_schema(schema)

    @classmethod
    async def open(cls, pool: asyncpg.Pool, schema: str) -> Self:
        async with pool.acquire() as conn:
            await migrate(conn, schema)
        return cls(pool, schema)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[StaffOps]:
        async with self._pool.acquire() as conn:
            yield StaffOps(conn, self._s)

    @asynccontextmanager
    async def change(self) -> AsyncIterator[StaffOps]:
        """One transaction under the directory's lock: checks and writes."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, hashtext($2))", _LOCK_CLASS, f"{self._s}:change"
            )
            yield StaffOps(conn, self._s)
