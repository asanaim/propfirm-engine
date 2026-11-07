"""Progressions in PostgreSQL (the customers schema, migration 3).

`progression_events` is the record (append-only, trigger-enforced); the
status columns of `progressions` are a cache of `progression_state`,
written in the same transaction as each event, like orders.

Passes come from the engine journal: `record_passes` reads the records
after its cursor and creates one progression per StagePassed of a known
account, cursor included in the same transaction, so a pass is never
missed or doubled. Concurrent callers skip while another holds the cursor.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from propfirm.customers.four_eyes import SameProposer, next_event, open_proposal
from propfirm.customers.progression import (
    PROGRESSION_KINDS,
    ProgressionEvent,
    progression_state,
)
from propfirm.customers.store import AccountRecord
from propfirm.journal.machine import CODEC
from propfirm.pgschema import Conn, check_schema
from propfirm.rules.events import StagePassed
from propfirm.rules.products import Stage

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Plan:
    """What follows a pass: the next stage and, for an evaluation stage, its
    rule set; or no stage, with the reason."""

    to_stage: Stage | None
    to_rsv_id: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressionRecord:
    prg: str
    from_acc: str
    cif: str
    ord: str
    product_id: str
    size_cents: int
    from_stage: str
    to_stage: str | None
    passed_seq: int
    passed_at: int
    created_at: int
    status: str
    to_rsv_id: str | None
    to_acc: str | None
    failure: str | None
    decided_by: str | None
    decided_at: int | None


class DecisionError(Exception):
    """A review decision refused (the code says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _record(row: asyncpg.Record) -> ProgressionRecord:
    return ProgressionRecord(**dict(row))


class ProgressionStore:
    def __init__(self, pool: asyncpg.Pool, schema: str, journal_schema: str) -> None:
        """The customers schema must be migrated (CustomerStore.open)."""
        self._pool = pool
        self._s = check_schema(schema)
        self._j = check_schema(journal_schema)

    # --- passes from the journal ----------------------------------------------------

    async def record_passes(
        self, plan: Callable[[AccountRecord], Plan], ts: int, limit: int = 500
    ) -> list[str]:
        """Create a progression for each StagePassed after the cursor; return
        the new PRG ids (none while another caller holds the cursor)."""
        s, j = self._s, self._j
        created: list[str] = []
        async with self._pool.acquire() as conn, conn.transaction():
            after = await conn.fetchval(
                f"SELECT seq FROM {s}.lifecycle_cursor WHERE id = 1 FOR UPDATE SKIP LOCKED"
            )
            if after is None:
                return created
            if await conn.fetchval("SELECT to_regclass($1)", f"{j}.journal") is None:
                return created  # the engine has not created its journal yet
            rows = await conn.fetch(
                f"SELECT seq, events FROM {j}.journal WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                limit,
            )
            for row in rows:
                events = CODEC.decode(row["events"])
                assert isinstance(events, tuple)
                for event in events:
                    if isinstance(event, StagePassed):
                        prg = await self._from_pass(conn, event, row["seq"], plan, ts)
                        if prg is not None:
                            created.append(prg)
            if rows:
                await conn.execute(
                    f"UPDATE {s}.lifecycle_cursor SET seq = $1 WHERE id = 1", rows[-1]["seq"]
                )
        return created

    async def _from_pass(
        self,
        conn: Conn,
        event: StagePassed,
        seq: int,
        plan: Callable[[AccountRecord], Plan],
        ts: int,
    ) -> str | None:
        s = self._s
        row = await conn.fetchrow(f"SELECT * FROM {s}.accounts WHERE acc = $1", event.account_id)
        if row is None:
            log.warning("pass of %s, an account with no customer record", event.account_id)
            return None
        account = AccountRecord(**dict(row))
        if account.stage not in (Stage.S1.value, Stage.S2.value):
            log.warning("pass of %s on stage %s ignored", account.acc, account.stage)
            return None
        if await conn.fetchval(f"SELECT 1 FROM {s}.progressions WHERE from_acc = $1", account.acc):
            return None  # already recorded (cannot happen past the cursor; kept as a guard)
        decided = plan(account)
        number = await conn.fetchval(f"SELECT nextval('{s}.prg_seq')")
        prg = f"PRG{number:08d}"
        to_stage = None if decided.to_stage is None else decided.to_stage.value
        initial = "blocked" if to_stage is None else "review"  # rewritten from the record
        await conn.execute(
            f"INSERT INTO {s}.progressions (prg, from_acc, cif, ord, product_id, size_cents, "
            f"from_stage, to_stage, passed_seq, passed_at, created_at, status) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
            prg,
            account.acc,
            account.cif,
            account.ord,
            account.product_id,
            account.size_cents,
            account.stage,
            to_stage,
            seq,
            event.ts,
            ts,
            initial,
        )
        detail: dict[str, Any] = {
            "from_acc": account.acc,
            "from_rsv_id": event.rsv_id,
            "balance_cents": event.balance_cents,
            "trading_days": event.trading_days,
            "passed_seq": seq,
            "to_stage": to_stage,
            "to_rsv_id": decided.to_rsv_id,
        }
        if decided.reason is not None:
            detail["reason"] = decided.reason
        await self._append(conn, prg, "created", ts, detail)
        return prg

    async def cursor(self) -> int:
        """The last journal seq read for passes."""
        seq: int = await self._pool.fetchval(
            f"SELECT seq FROM {self._s}.lifecycle_cursor WHERE id = 1"
        )
        return seq

    # --- reading --------------------------------------------------------------------

    async def progression(self, prg: str) -> ProgressionRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.progressions WHERE prg = $1", prg)
        return None if row is None else _record(row)

    async def progressions_for(self, cif: str) -> list[ProgressionRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.progressions WHERE cif = $1 ORDER BY prg", cif
        )
        return [_record(r) for r in rows]

    async def with_status(self, status: str, limit: int = 200) -> list[ProgressionRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.progressions WHERE status = $1 ORDER BY prg LIMIT $2",
            status,
            limit,
        )
        return [_record(r) for r in rows]

    async def history(self, prg: str) -> list[ProgressionEvent]:
        async with self._pool.acquire() as conn:
            return await self._events(conn, prg)

    # --- opening the next evaluation account ---------------------------------------

    async def assign_account(self, prg: str, ts: int) -> str:
        """The next account's ACC id, allocated once and kept."""
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, prg)
            if record.to_acc is not None:
                return record.to_acc
            number = await conn.fetchval(f"SELECT nextval('{self._s}.acc_seq')")
            acc = f"ACC{number:08d}"
            await self._append(conn, prg, "account_assigned", ts, {"acc": acc})
            return acc

    async def provision_attempted(self, prg: str, acc: str, entry_id: str, ts: int) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await self._locked(conn, prg)
            await self._append(
                conn, prg, "provision_attempted", ts, {"acc": acc, "entry_id": entry_id}
            )

    async def mark_provisioned(self, prg: str, ts: int, journal_seq: int | None) -> None:
        """provisioning -> provisioned, with the new account's owner record;
        nothing when the progression is no longer provisioning."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, prg)
            if record.status != "provisioning":
                return
            assert record.to_acc is not None and record.to_rsv_id is not None
            assert record.to_stage is not None
            await conn.execute(
                f"INSERT INTO {s}.accounts (acc, cif, ord, product_id, rsv_id, size_cents, "
                f"created_at, stage, progression) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                record.to_acc,
                record.cif,
                record.ord,
                record.product_id,
                record.to_rsv_id,
                record.size_cents,
                ts,
                record.to_stage,
                prg,
            )
            await self._append(
                conn,
                prg,
                "provisioned",
                ts,
                {"acc": record.to_acc, "rsv_id": record.to_rsv_id, "journal_seq": journal_seq},
            )

    async def mark_failed(self, prg: str, reason: str, ts: int, journal_seq: int | None) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            if (await self._locked(conn, prg)).status != "provisioning":
                return
            await self._append(
                conn, prg, "failed", ts, {"reason": reason, "journal_seq": journal_seq}
            )

    # --- funded review --------------------------------------------------------------

    async def decide(
        self,
        prg: str,
        approve: bool,
        subject: str,
        email: str | None,
        reason: str,
        ts: int,
    ) -> ProgressionRecord:
        """An employee approves or denies a funded review. Refused when it is
        not in review, or when it is the employee's own progression."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.progressions WHERE prg = $1 FOR UPDATE", prg
            )
            if row is None:
                raise DecisionError("unknown", f"no progression {prg}")
            record = _record(row)
            if record.status != "review":
                raise DecisionError("not_in_review", f"{prg} is {record.status}")
            own = await conn.fetchval(f"SELECT cif FROM {s}.cif WHERE subject = $1", subject)
            if own == record.cif:
                raise DecisionError("own_progression", "nobody decides their own review")
            # rule AC00: the first decision is a proposal; a second person decides
            proposal = open_proposal(await self._events(conn, prg))
            try:
                kind, detail = next_event(proposal, approve, subject, email, reason)
            except SameProposer as exc:
                raise DecisionError("four_eyes", str(exc)) from exc
            return await self._append(conn, prg, kind, ts, detail)

    # --- cache --------------------------------------------------------------------

    async def rebuild_status(self) -> list[str]:
        """Rewrite every cached status from the records; return the PRG ids
        whose cache was wrong."""
        fixed: list[str] = []
        prgs = [
            r["prg"]
            for r in await self._pool.fetch(f"SELECT prg FROM {self._s}.progressions ORDER BY prg")
        ]
        for prg in prgs:
            async with self._pool.acquire() as conn, conn.transaction():
                before = await self._locked(conn, prg)
                if await self._write_cache(conn, prg) != before:
                    fixed.append(prg)
        return fixed

    async def _locked(self, conn: Conn, prg: str) -> ProgressionRecord:
        row = await conn.fetchrow(
            f"SELECT * FROM {self._s}.progressions WHERE prg = $1 FOR UPDATE", prg
        )
        if row is None:
            raise KeyError(f"unknown progression {prg!r}")
        return _record(row)

    async def _events(self, conn: Conn, prg: str) -> list[ProgressionEvent]:
        rows = await conn.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.progression_events "
            f"WHERE prg = $1 ORDER BY id",
            prg,
        )
        return [ProgressionEvent(prg, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def _append(
        self, conn: Conn, prg: str, kind: str, ts: int, detail: dict[str, Any]
    ) -> ProgressionRecord:
        """Record an event, then write the cache from the whole history."""
        assert kind in PROGRESSION_KINDS
        await conn.execute(
            f"INSERT INTO {self._s}.progression_events (prg, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            prg,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        return await self._write_cache(conn, prg)

    async def _write_cache(self, conn: Conn, prg: str) -> ProgressionRecord:
        state = progression_state(prg, await self._events(conn, prg))
        row = await conn.fetchrow(
            f"UPDATE {self._s}.progressions SET status = $2, to_rsv_id = $3, to_acc = $4, "
            f"failure = $5, decided_by = $6, decided_at = $7 WHERE prg = $1 RETURNING *",
            prg,
            state.status,
            state.to_rsv_id,
            state.to_acc,
            state.failure,
            state.decided_by,
            state.decided_at,
        )
        assert row is not None
        return _record(row)
