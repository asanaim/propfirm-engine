"""Admin setting changes in PostgreSQL (schema `settings` by default).

Append-only tables (updates and deletes are refused by a trigger):
- `changes`: every change, numbered, with its author and reason;
- `applied`: when a change took effect. A country-gating change takes
  effect when it is recorded; a stage-limits change when the engine has
  journaled its new rule set (`journal_seq`). Only applied changes are in
  force, so checkout never sells a rule set the engine does not know yet.
- `proposals` and `proposal_decisions`: maker-checker [staff spec 5a-2]:
  an admin proposes a change, a second admin approves (the change is then
  recorded with both names), rejects it, or the maker withdraws it; one
  decision per proposal;
- `news_actions`: every restricted-event action of the risk desk (M13):
  who, why, the input it sent and what the engine answered.
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
    (
        2,
        """
        CREATE TABLE {s}.news_actions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            action text NOT NULL
                CHECK (action IN ('calendar', 'manual', 'published', 'cancel')),
            event_id text NOT NULL,
            title text,
            calendar_version text,
            author text NOT NULL,
            author_email text,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            entry_id text NOT NULL,
            result text NOT NULL CHECK (result IN ('done', 'rejected', 'pending')),
            detail text,
            created_at bigint NOT NULL
        );
        CREATE INDEX news_actions_event ON {s}.news_actions (event_id);
        CREATE TRIGGER news_actions_append_only BEFORE UPDATE OR DELETE ON {s}.news_actions
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        3,
        """
        -- events the collector read from the publishers' schedules
        ALTER TABLE {s}.news_actions DROP CONSTRAINT news_actions_action_check;
        ALTER TABLE {s}.news_actions ADD CONSTRAINT news_actions_action_check
            CHECK (action IN ('calendar', 'manual', 'published', 'cancel', 'collector'));
        """,
    ),
    (
        4,
        """
        -- the collector's sources: when one cannot be read, staff and admins
        -- are alerted to add its events by hand
        CREATE TABLE {s}.news_source_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('failed', 'reminded', 'recovered')),
            error text,
            notified int NOT NULL,  -- emails sent for this event
            ts bigint NOT NULL
        );
        CREATE INDEX news_source_events_source ON {s}.news_source_events (source, id);
        CREATE TRIGGER news_source_events_append_only
            BEFORE UPDATE OR DELETE ON {s}.news_source_events
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        5,
        """
        -- maker-checker: a change takes effect only once a second admin approves
        CREATE TABLE {s}.proposals (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('country_gating', 'stage_limits')),
            value jsonb NOT NULL,
            author text NOT NULL,
            author_email text,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.proposal_decisions (
            proposal bigint PRIMARY KEY REFERENCES {s}.proposals (id),
            decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'withdrawn')),
            decided_by text NOT NULL,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            decided_at bigint NOT NULL
        );
        ALTER TABLE {s}.changes
            ADD COLUMN proposal_id bigint UNIQUE REFERENCES {s}.proposals (id),
            ADD COLUMN approved_by text;
        CREATE TRIGGER proposals_append_only BEFORE UPDATE OR DELETE ON {s}.proposals
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        CREATE TRIGGER proposal_decisions_append_only
            BEFORE UPDATE OR DELETE ON {s}.proposal_decisions
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        6,
        """
        -- the owners' stand-in approver (rules AC00, AC01, 8 Nov 2026)
        ALTER TABLE {s}.changes DROP CONSTRAINT changes_kind_check;
        ALTER TABLE {s}.changes ADD CONSTRAINT changes_kind_check
            CHECK (kind IN ('country_gating', 'stage_limits', 'stand_in'));
        ALTER TABLE {s}.proposals DROP CONSTRAINT proposals_kind_check;
        ALTER TABLE {s}.proposals ADD CONSTRAINT proposals_kind_check
            CHECK (kind IN ('country_gating', 'stage_limits', 'stand_in'));
        """,
    ),
    (
        7,
        """
        -- rule AC10 (8 Nov 2026): versioned policy pages, and who accepted which
        ALTER TABLE {s}.changes DROP CONSTRAINT changes_kind_check;
        ALTER TABLE {s}.changes ADD CONSTRAINT changes_kind_check
            CHECK (kind IN ('country_gating', 'stage_limits', 'stand_in', 'policy_page'));
        ALTER TABLE {s}.proposals DROP CONSTRAINT proposals_kind_check;
        ALTER TABLE {s}.proposals ADD CONSTRAINT proposals_kind_check
            CHECK (kind IN ('country_gating', 'stage_limits', 'stand_in', 'policy_page'));
        CREATE TABLE {s}.policy_acceptances (
            subject text NOT NULL,
            slug text NOT NULL,
            version int NOT NULL CHECK (version >= 1),
            accepted_at bigint NOT NULL,
            PRIMARY KEY (subject, slug, version)
        );
        CREATE TRIGGER policy_acceptances_append_only
            BEFORE UPDATE OR DELETE ON {s}.policy_acceptances
            FOR EACH ROW EXECUTE FUNCTION {s}.append_only();
        """,
    ),
    (
        8,
        """
        -- a release's impact, marked by the risk desk (8 Nov 2026): the latest
        -- mark of an event wins; restriction is separate and unchanged
        CREATE TABLE {s}.news_impacts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id text NOT NULL,
            impact text NOT NULL CHECK (impact IN ('high', 'medium', 'low')),
            author text NOT NULL,
            author_email text,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            ts bigint NOT NULL
        );
        CREATE INDEX news_impacts_event ON {s}.news_impacts (event_id, id);
        CREATE TRIGGER news_impacts_append_only BEFORE UPDATE OR DELETE ON {s}.news_impacts
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
        approved_by=row["approved_by"],
        proposal_id=row["proposal_id"],
    )


@dataclass(frozen=True, slots=True)
class Proposal:
    id: int
    setting: Setting
    author: str
    author_email: str | None
    reason: str
    ts: int
    decision: str | None  # approved, rejected, withdrawn; None: open
    decided_by: str | None
    decision_reason: str | None
    decided_at: int | None
    change_id: int | None  # the change an approval recorded


def _proposal(row: asyncpg.Record) -> Proposal:
    return Proposal(
        row["id"],
        from_json(row["kind"], json.loads(row["value"])),
        row["author"],
        row["author_email"],
        row["reason"],
        row["created_at"],
        row["decision"],
        row["decided_by"],
        row["decision_reason"],
        row["decided_at"],
        row["change_id"],
    )


class ProposalDecided(Exception):
    """The proposal already has its decision."""


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

    async def accept(self, subject: str, slug: str, version: int, ts: int) -> None:
        """Record a policy acceptance; a repeat keeps the first."""
        await self._pool.execute(
            f"INSERT INTO {self._s}.policy_acceptances (subject, slug, version, accepted_at) "
            f"VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            subject,
            slug,
            version,
            ts,
        )

    async def accepted(self, subject: str) -> set[tuple[str, int]]:
        rows = await self._pool.fetch(
            f"SELECT slug, version FROM {self._s}.policy_acceptances WHERE subject = $1", subject
        )
        return {(r["slug"], r["version"]) for r in rows}

    async def unapplied_changes(self) -> Sequence[SettingChange]:
        rows = await self._pool.fetch(
            f"SELECT c.* FROM {self._s}.changes c LEFT JOIN {self._s}.applied a "
            f"ON a.change_id = c.id WHERE a.change_id IS NULL ORDER BY c.id"
        )
        return [_change(r) for r in rows]

    # --- maker-checker ----------------------------------------------------------------

    def _proposals_sql(self, where: str) -> str:
        s = self._s
        return (
            f"SELECT p.*, d.decision, d.decided_by, d.reason AS decision_reason, d.decided_at, "
            f"c.id AS change_id FROM {s}.proposals p "
            f"LEFT JOIN {s}.proposal_decisions d ON d.proposal = p.id "
            f"LEFT JOIN {s}.changes c ON c.proposal_id = p.id {where}"
        )

    async def propose(
        self, setting: Setting, author: str, author_email: str | None, reason: str, ts: int
    ) -> Proposal:
        kind, value = to_json(setting)
        pid = await self._pool.fetchval(
            f"INSERT INTO {self._s}.proposals (kind, value, author, author_email, reason, "
            f"created_at) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            kind,
            json.dumps(value, sort_keys=True),
            author,
            author_email,
            reason,
            ts,
        )
        found = await self.proposal(pid)
        assert found is not None
        return found

    async def proposal(self, pid: int) -> Proposal | None:
        row = await self._pool.fetchrow(self._proposals_sql("WHERE p.id = $1"), pid)
        return None if row is None else _proposal(row)

    async def proposals(self, open_only: bool = True, limit: int = 100) -> Sequence[Proposal]:
        where = "WHERE ($1 = false OR d.proposal IS NULL) ORDER BY p.id DESC LIMIT $2"
        rows = await self._pool.fetch(self._proposals_sql(where), open_only, limit)
        return [_proposal(r) for r in rows]

    async def decide(self, pid: int, decision: str, by: str, reason: str, ts: int) -> None:
        """Reject or withdraw; ProposalDecided if it already has a decision."""
        done = await self._pool.execute(
            f"INSERT INTO {self._s}.proposal_decisions (proposal, decision, decided_by, reason, "
            f"decided_at) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (proposal) DO NOTHING",
            pid,
            decision,
            by,
            reason,
            ts,
        )
        if done.endswith(" 0"):
            raise ProposalDecided(f"proposal {pid} is already decided")

    async def approve(
        self, proposal: Proposal, approver: str, reason: str, ts: int, *, applied: bool
    ) -> SettingChange:
        """One transaction: the decision, then the change in the maker's
        name with the approver's (and, for gating, in force at once)."""
        s = self._s
        kind, value = to_json(proposal.setting)
        async with self._pool.acquire() as conn, conn.transaction():
            done = await conn.execute(
                f"INSERT INTO {s}.proposal_decisions (proposal, decision, decided_by, reason, "
                f"decided_at) VALUES ($1, 'approved', $2, $3, $4) "
                f"ON CONFLICT (proposal) DO NOTHING",
                proposal.id,
                approver,
                reason,
                ts,
            )
            if done.endswith(" 0"):
                raise ProposalDecided(f"proposal {proposal.id} is already decided")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.changes (kind, value, author, author_email, reason, "
                f"created_at, proposal_id, approved_by) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *",
                kind,
                json.dumps(value, sort_keys=True),
                proposal.author,
                proposal.author_email,
                proposal.reason,
                ts,
                proposal.id,
                approver,
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
