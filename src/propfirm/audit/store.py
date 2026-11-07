"""The audit trail in PostgreSQL (schema `audit` by default).

`audit_log` is the record: append-only, and the database itself refuses a
row that does not continue the chain (seq one past the head, prev_hash the
head's hash), an UPDATE, a DELETE or a TRUNCATE. `audit_head` holds the
latest seq and hash; appends serialise on its row lock. `audit_status` and
`audit_export` are the auditor's working state (last check, SIEM cursors).

A database superuser can still disable the triggers; that is what the
external anchors and the auditor's checks catch.
"""

from dataclasses import dataclass

import asyncpg

from propfirm.audit.chain import GENESIS, AuditEvent, AuditRecord, link
from propfirm.pgschema import Conn, check_schema
from propfirm.pgschema import migrate as _migrate

_LOCK_CLASS = 0x61756474  # "audt"

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE {s}.audit_log (
            seq bigint PRIMARY KEY CHECK (seq >= 1),
            ts bigint NOT NULL CHECK (ts >= 0),
            actor text NOT NULL,
            action text NOT NULL,
            target text NOT NULL,
            status integer NOT NULL CHECK (status BETWEEN 0 AND 599),
            ip text NOT NULL,
            request_id text NOT NULL,
            prev_hash text NOT NULL CHECK (prev_hash ~ '^[0-9a-f]{{64}}$'),
            hash text NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{{64}}$')
        );
        CREATE INDEX audit_log_actor ON {s}.audit_log (actor, seq);
        CREATE INDEX audit_log_ts ON {s}.audit_log (ts);
        CREATE TABLE {s}.audit_head (
            one boolean PRIMARY KEY DEFAULT true CHECK (one),
            seq bigint NOT NULL,
            hash text NOT NULL
        );
        INSERT INTO {s}.audit_head (seq, hash) VALUES (0, '{genesis}');

        CREATE FUNCTION {s}.audit_refuse() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the audit log is append-only';
        END $$;
        CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON {s}.audit_log
            FOR EACH ROW EXECUTE FUNCTION {s}.audit_refuse();
        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON {s}.audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION {s}.audit_refuse();
        CREATE TRIGGER audit_head_no_delete BEFORE DELETE ON {s}.audit_head
            FOR EACH ROW EXECUTE FUNCTION {s}.audit_refuse();
        CREATE TRIGGER audit_head_no_truncate BEFORE TRUNCATE ON {s}.audit_head
            FOR EACH STATEMENT EXECUTE FUNCTION {s}.audit_refuse();

        -- a new record continues the chain from the head
        CREATE FUNCTION {s}.audit_continues() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE h record;
        BEGIN
            SELECT seq, hash INTO h FROM {s}.audit_head;
            IF NEW.seq <> h.seq + 1 OR NEW.prev_hash <> h.hash THEN
                RAISE EXCEPTION 'audit record % does not continue the chain', NEW.seq;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER audit_log_continues BEFORE INSERT ON {s}.audit_log
            FOR EACH ROW EXECUTE FUNCTION {s}.audit_continues();

        -- the head only moves forward, one record at a time, onto a record
        CREATE FUNCTION {s}.audit_head_advances() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.seq <> OLD.seq + 1 OR NOT EXISTS (
                SELECT 1 FROM {s}.audit_log WHERE seq = NEW.seq AND hash = NEW.hash
            ) THEN
                RAISE EXCEPTION 'the audit head only advances onto the next record';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER audit_head_advances BEFORE UPDATE ON {s}.audit_head
            FOR EACH ROW EXECUTE FUNCTION {s}.audit_head_advances();

        -- the auditor's working state (not the record)
        CREATE TABLE {s}.audit_status (
            one boolean PRIMARY KEY DEFAULT true CHECK (one),
            checked_at bigint NOT NULL,
            verified_seq bigint NOT NULL,
            anchored_seq bigint NOT NULL,
            broken_seq bigint,
            broken_reason text
        );
        CREATE TABLE {s}.audit_export (
            sink text PRIMARY KEY,
            seq bigint NOT NULL CHECK (seq >= 0)
        );
        """.replace("{genesis}", GENESIS),
    ),
)


async def migrate(conn: Conn, schema: str) -> list[int]:
    return await _migrate(conn, schema, _MIGRATIONS, _LOCK_CLASS)


@dataclass(frozen=True, slots=True)
class Status:
    checked_at: int
    verified_seq: int
    anchored_seq: int
    broken_seq: int | None
    broken_reason: str | None


def _record(r: asyncpg.Record) -> AuditRecord:
    return AuditRecord(
        r["seq"],
        AuditEvent(
            r["ts"], r["actor"], r["action"], r["target"], r["status"], r["ip"], r["request_id"]
        ),
        r["prev_hash"],
        r["hash"],
    )


class AuditStore:
    def __init__(self, pool: asyncpg.Pool, schema: str = "audit") -> None:
        self._pool = pool
        self._s = check_schema(schema)

    async def append(self, event: AuditEvent) -> AuditRecord:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            head = await conn.fetchrow(f"SELECT seq, hash FROM {s}.audit_head FOR UPDATE")
            assert head is not None
            seq = head["seq"] + 1
            record = AuditRecord(seq, event, head["hash"], link(head["hash"], seq, event))
            await conn.execute(
                f"INSERT INTO {s}.audit_log (seq, ts, actor, action, target, status, ip,"
                f" request_id, prev_hash, hash) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                seq,
                event.ts,
                event.actor,
                event.action,
                event.target,
                event.status,
                event.ip,
                event.request_id,
                record.prev_hash,
                record.hash,
            )
            await conn.execute(f"UPDATE {s}.audit_head SET seq = $1, hash = $2", seq, record.hash)
        return record

    async def head(self) -> tuple[int, str]:
        row = await self._pool.fetchrow(f"SELECT seq, hash FROM {self._s}.audit_head")
        assert row is not None
        return row["seq"], row["hash"]

    async def records(
        self,
        after: int = 0,
        limit: int = 1000,
        actor: str | None = None,
        newest_first: bool = False,
    ) -> list[AuditRecord]:
        s = self._s
        if newest_first:
            rows = await self._pool.fetch(
                f"SELECT * FROM {s}.audit_log WHERE ($1::text IS NULL OR actor = $1)"
                f" AND ($2::bigint = 0 OR seq < $2) ORDER BY seq DESC LIMIT $3",
                actor,
                after,
                limit,
            )
        else:
            rows = await self._pool.fetch(
                f"SELECT * FROM {s}.audit_log WHERE seq > $1"
                f" AND ($2::text IS NULL OR actor = $2) ORDER BY seq LIMIT $3",
                after,
                actor,
                limit,
            )
        return [_record(r) for r in rows]

    async def record(self, seq: int) -> AuditRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.audit_log WHERE seq = $1", seq)
        return None if row is None else _record(row)

    async def status(self) -> Status | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.audit_status")
        if row is None:
            return None
        return Status(
            row["checked_at"],
            row["verified_seq"],
            row["anchored_seq"],
            row["broken_seq"],
            row["broken_reason"],
        )

    async def set_status(self, status: Status) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.audit_status AS t (checked_at, verified_seq, anchored_seq,"
            f" broken_seq, broken_reason) VALUES ($1,$2,$3,$4,$5) ON CONFLICT (one) DO UPDATE SET"
            f" checked_at = EXCLUDED.checked_at, verified_seq = EXCLUDED.verified_seq,"
            f" anchored_seq = EXCLUDED.anchored_seq, broken_seq = EXCLUDED.broken_seq,"
            f" broken_reason = EXCLUDED.broken_reason",
            status.checked_at,
            status.verified_seq,
            status.anchored_seq,
            status.broken_seq,
            status.broken_reason,
        )

    async def cursor(self, sink: str) -> int:
        seq = await self._pool.fetchval(
            f"SELECT seq FROM {self._s}.audit_export WHERE sink = $1", sink
        )
        return 0 if seq is None else int(seq)

    async def set_cursor(self, sink: str, seq: int) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.audit_export (sink, seq) VALUES ($1, $2)"
            f" ON CONFLICT (sink) DO UPDATE SET seq = EXCLUDED.seq",
            sink,
            seq,
        )
