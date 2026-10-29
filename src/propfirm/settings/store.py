"""Admin setting changes in PostgreSQL (schema `settings` by default).

Two append-only tables (updates and deletes are refused by a trigger):
- `changes`: every change, numbered, with its author and reason;
- `applied`: when a change took effect. A country-gating change takes
  effect when it is recorded; a stage-limits change when the engine has
  journaled its new rule set (`journal_seq`). Only applied changes are in
  force, so checkout never sells a rule set the engine does not know yet.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

import asyncpg

from propfirm.pgschema import Conn, check_schema
from propfirm.pgschema import migrate as _migrate
from propfirm.policy import Policy
from propfirm.settings.overlay import Setting, SettingChange, apply_changes, from_json, to_json

_LOCK_CLASS = 0x73657474  # "sett"

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE {s}.changes (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('country_gating', 'stage_limits')),
            value jsonb NOT NULL,
            author text NOT NULL,
            author_email text,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.applied (
            change_id bigint PRIMARY KEY REFERENCES {s}.changes (id),
            journal_seq bigint,
            applied_at bigint NOT NULL
        );
        CREATE FUNCTION {s}.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'setting records are append-only';
        END $$;
        CREATE TRIGGER changes_append_only BEFORE UPDATE OR DELETE ON {s}.changes
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER applied_append_only BEFORE UPDATE OR DELETE ON {s}.applied
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
)


async def migrate(conn: Conn, schema: str) -> list[int]:
    """Apply the settings migrations not yet applied; return their numbers."""
    return await _migrate(conn, schema, _MIGRATIONS, _LOCK_CLASS)


@dataclass(frozen=True, slots=True)
class ChangeStatus:
    change: SettingChange
    applied_at: int | None  # None: waiting for the engine
    journal_seq: int | None


def _change(row: asyncpg.Record) -> SettingChange:
    return SettingChange(
        id=row["id"],
        setting=from_json(row["kind"], json.loads(row["value"])),
        author=row["author"],
        author_email=row["author_email"],
        reason=row["reason"],
        ts=row["created_at"],
    )


class SettingsStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = check_schema(schema)

    @classmethod
    async def open(cls, pool: asyncpg.Pool, schema: str) -> Self:
        async with pool.acquire() as conn:
            await migrate(conn, schema)
        return cls(pool, schema)

    async def record(
        self,
        setting: Setting,
        author: str,
        author_email: str | None,
        reason: str,
        ts: int,
        *,
        applied: bool,
    ) -> SettingChange:
        """Store a change; `applied` also marks it in effect at `ts`."""
        s = self._s
        kind, value = to_json(setting)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"INSERT INTO {s}.changes (kind, value, author, author_email, reason, "
                f"created_at) VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                kind,
                json.dumps(value, sort_keys=True),
                author,
                author_email,
                reason,
                ts,
            )
            assert row is not None
            if applied:
                await conn.execute(
                    f"INSERT INTO {s}.applied (change_id, journal_seq, applied_at) "
                    f"VALUES ($1, NULL, $2)",
                    row["id"],
                    ts,
                )
        return _change(row)

    async def mark_applied(self, change_id: int, journal_seq: int | None, ts: int) -> None:
        """Mark a change in effect; a repeat changes nothing."""
        await self._pool.execute(
            f"INSERT INTO {self._s}.applied (change_id, journal_seq, applied_at) "
            f"VALUES ($1, $2, $3) ON CONFLICT (change_id) DO NOTHING",
            change_id,
            journal_seq,
            ts,
        )

    async def version(self) -> int:
        """How many changes are in effect; grows with every applied change."""
        count = await self._pool.fetchval(f"SELECT count(*) FROM {self._s}.applied")
        assert isinstance(count, int)
        return count

    async def applied_changes(self) -> Sequence[SettingChange]:
        rows = await self._pool.fetch(
            f"SELECT c.* FROM {self._s}.changes c JOIN {self._s}.applied a "
            f"ON a.change_id = c.id ORDER BY c.id"
        )
        return [_change(r) for r in rows]

    async def unapplied_changes(self) -> Sequence[SettingChange]:
        rows = await self._pool.fetch(
            f"SELECT c.* FROM {self._s}.changes c LEFT JOIN {self._s}.applied a "
            f"ON a.change_id = c.id WHERE a.change_id IS NULL ORDER BY c.id"
        )
        return [_change(r) for r in rows]

    async def history(self, limit: int = 100) -> Sequence[ChangeStatus]:
        """The latest changes first."""
        rows = await self._pool.fetch(
            f"SELECT c.*, a.applied_at, a.journal_seq FROM {self._s}.changes c "
            f"LEFT JOIN {self._s}.applied a ON a.change_id = c.id ORDER BY c.id DESC LIMIT $1",
            limit,
        )
        return [ChangeStatus(_change(r), r["applied_at"], r["journal_seq"]) for r in rows]


async def policy_in_force(database_url: str, schema: str, base: Policy) -> Policy:
    """The policy file with the applied admin changes (read by the engine
    service at start, so it publishes what a file version bump needs)."""
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    try:
        store = await SettingsStore.open(pool, schema)
        return apply_changes(base, await store.applied_changes())
    finally:
        await pool.close()
