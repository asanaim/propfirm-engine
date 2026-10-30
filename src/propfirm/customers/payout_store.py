"""Payout wallets and payouts in PostgreSQL (the customers schema, migration 6).

`wallets` and `payout_events` are append-only (trigger-enforced); the status
columns of `payouts` are a cache of `payout_state`, written in the same
transaction as each event. A partial unique index keeps one payout in
progress per account.
"""

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from propfirm.customers.notifications import Notice, insert_notice
from propfirm.customers.payout_flow import (
    IN_PROGRESS,
    PAYOUT_KINDS,
    TAKEN,
    PayoutError,
    PayoutEvent,
    payout_state,
)
from propfirm.pgschema import Conn, check_schema


@dataclass(frozen=True, slots=True)
class Wallet:
    wal: str
    cif: str
    network: str
    address: str
    added_at: int
    added_by: str


@dataclass(frozen=True, slots=True)
class PayoutRecord:
    po: str
    acc: str
    cif: str
    ord: str
    wal: str
    requested_at: int
    trader_amount_cents: int
    debit_cents: int
    status: str
    approvals: int
    debited_at: int | None
    paid_at: int | None
    provider_ref: str | None
    failure: str | None


class PayoutRefused(Exception):
    """A request the payout's state or the rules refuse (the code says why)."""

    def __init__(self, code: str, message: str, *, max_age_s: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.max_age_s = max_age_s  # step-up: how recent the second factor must be


def _record(row: asyncpg.Record) -> PayoutRecord:
    return PayoutRecord(**dict(row))


class PayoutStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        """The customers schema must be migrated (CustomerStore.open)."""
        self._pool = pool
        self._s = check_schema(schema)

    # --- wallets ----------------------------------------------------------------------

    async def add_wallet(
        self,
        cif: str,
        network: str,
        address: str,
        subject: str,
        ts: int,
        detail: dict[str, Any],
        notice: Notice,
    ) -> Wallet:
        """The wallet and its security email, in one transaction."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            number = await conn.fetchval(f"SELECT nextval('{s}.wal_seq')")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.wallets (wal, cif, network, address, added_at, added_by, "
                f"detail) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) "
                f"RETURNING wal, cif, network, address, added_at, added_by",
                f"WAL{number:08d}",
                cif,
                network,
                address,
                ts,
                subject,
                json.dumps(detail, sort_keys=True),
            )
            assert row is not None
            wallet = Wallet(**dict(row))
            await insert_notice(conn, s, cif, notice, ts, {"wal": wallet.wal})
            return wallet

    async def wallet(self, cif: str) -> Wallet | None:
        """The customer's wallet in use: the latest added."""
        row = await self._pool.fetchrow(
            f"SELECT wal, cif, network, address, added_at, added_by FROM {self._s}.wallets "
            f"WHERE cif = $1 ORDER BY added_at DESC, wal DESC LIMIT 1",
            cif,
        )
        return None if row is None else Wallet(**dict(row))

    async def wallet_by_id(self, wal: str) -> Wallet | None:
        row = await self._pool.fetchrow(
            f"SELECT wal, cif, network, address, added_at, added_by FROM {self._s}.wallets "
            f"WHERE wal = $1",
            wal,
        )
        return None if row is None else Wallet(**dict(row))

    # --- payouts ----------------------------------------------------------------------

    async def request(
        self,
        acc: str,
        cif: str,
        ord_id: str,
        wal: str,
        trader_amount: int,
        debit: int,
        ts: int,
        detail: dict[str, Any],
    ) -> PayoutRecord:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(
                f"SELECT 1 FROM {s}.payouts WHERE acc = $1 AND status = ANY($2::text[])",
                acc,
                list(IN_PROGRESS),
            ):
                raise PayoutRefused("pending", "a payout of this account is in progress")
            number = await conn.fetchval(f"SELECT nextval('{s}.po_seq')")
            po = f"PO{number:08d}"
            try:
                await conn.execute(
                    f"INSERT INTO {s}.payouts (po, acc, cif, ord, wal, requested_at, "
                    f"trader_amount_cents, debit_cents, status) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'requested')",
                    po,
                    acc,
                    cif,
                    ord_id,
                    wal,
                    ts,
                    trader_amount,
                    debit,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PayoutRefused("pending", "a payout of this account is in progress") from exc
            return await self._append(conn, po, "requested", ts, detail)

    async def payout(self, po: str) -> PayoutRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.payouts WHERE po = $1", po)
        return None if row is None else _record(row)

    async def payouts(
        self, status: str | None = None, acc: str | None = None, limit: int = 200
    ) -> list[PayoutRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.payouts WHERE ($1::text IS NULL OR status = $1) "
            f"AND ($2::text IS NULL OR acc = $2) ORDER BY po LIMIT $3",
            status,
            acc,
            limit,
        )
        return [_record(r) for r in rows]

    async def in_progress(self, acc: str) -> PayoutRecord | None:
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.payouts WHERE acc = $1 AND status = ANY($2::text[])",
            acc,
            list(IN_PROGRESS),
        )
        return None if row is None else _record(row)

    async def made(self, ord_id: str) -> int:
        """Payouts taken out of the accounts of an order (fee refunds count these)."""
        count: int = await self._pool.fetchval(
            f"SELECT count(*) FROM {self._s}.payouts WHERE ord = $1 AND status = ANY($2::text[])",
            ord_id,
            list(TAKEN),
        )
        return count

    async def violations_taken(self, acc: str) -> int:
        """Profit removed for violations that payouts of the account already
        took out (each payout takes the V of its request's eligibility)."""
        s = self._s
        taken: int = await self._pool.fetchval(
            f"SELECT coalesce(sum((e.detail -> 'eligibility' ->> 'violations_cents')::bigint), 0)"
            f"::bigint "
            f"FROM {s}.payout_events e JOIN {s}.payouts p USING (po) "
            f"WHERE p.acc = $1 AND p.status = ANY($2::text[]) AND e.kind = 'requested'",
            acc,
            list(TAKEN),
        )
        return taken

    async def last_debited(self, acc: str) -> int | None:
        found: int | None = await self._pool.fetchval(
            f"SELECT max(debited_at) FROM {self._s}.payouts WHERE acc = $1", acc
        )
        return found

    async def due(self) -> list[PayoutRecord]:
        """Approved payouts: taken from the account next."""
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.payouts WHERE status = 'approved' ORDER BY po"
        )
        return [_record(r) for r in rows]

    async def history(self, po: str) -> list[PayoutEvent]:
        async with self._pool.acquire() as conn:
            return await self._events(conn, po)

    # --- steps ------------------------------------------------------------------------

    async def append(
        self, po: str, kind: str, ts: int, detail: dict[str, Any], *, when: tuple[str, ...]
    ) -> PayoutRecord | None:
        """Record `kind` if the payout's status is in `when` (under its lock)."""
        async with self._pool.acquire() as conn, conn.transaction():
            if (await self._locked(conn, po)).status not in when:
                return None
            return await self._append(conn, po, kind, ts, detail)

    async def decide(
        self, po: str, kind: str, staff: str, ts: int, detail: dict[str, Any]
    ) -> PayoutRecord:
        """An employee approves or denies; never on their own customer record."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, po)
            own = await conn.fetchval(f"SELECT cif FROM {s}.cif WHERE subject = $1", staff)
            if own == record.cif:
                raise PayoutRefused("own_payout", "nobody approves their own payout")
            try:
                async with conn.transaction():
                    return await self._append(conn, po, kind, ts, {"by": staff, **detail})
            except PayoutError as exc:
                raise PayoutRefused("not_allowed", str(exc)) from exc

    # --- cache --------------------------------------------------------------------

    async def rebuild_status(self) -> list[str]:
        fixed: list[str] = []
        ids = [
            r["po"] for r in await self._pool.fetch(f"SELECT po FROM {self._s}.payouts ORDER BY po")
        ]
        for po in ids:
            async with self._pool.acquire() as conn, conn.transaction():
                before = await self._locked(conn, po)
                if await self._write_cache(conn, po) != before:
                    fixed.append(po)
        return fixed

    async def _locked(self, conn: Conn, po: str) -> PayoutRecord:
        row = await conn.fetchrow(f"SELECT * FROM {self._s}.payouts WHERE po = $1 FOR UPDATE", po)
        if row is None:
            raise PayoutRefused("unknown", f"no payout {po}")
        return _record(row)

    async def _events(self, conn: Conn, po: str) -> list[PayoutEvent]:
        rows = await conn.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.payout_events "
            f"WHERE po = $1 ORDER BY id",
            po,
        )
        return [PayoutEvent(po, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def _append(
        self, conn: Conn, po: str, kind: str, ts: int, detail: dict[str, Any]
    ) -> PayoutRecord:
        assert kind in PAYOUT_KINDS
        await conn.execute(
            f"INSERT INTO {self._s}.payout_events (po, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            po,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        return await self._write_cache(conn, po)

    async def _write_cache(self, conn: Conn, po: str) -> PayoutRecord:
        state = payout_state(po, await self._events(conn, po))
        row = await conn.fetchrow(
            f"UPDATE {self._s}.payouts SET status = $2, approvals = $3, debited_at = $4, "
            f"paid_at = $5, provider_ref = $6, failure = $7 WHERE po = $1 RETURNING *",
            po,
            state.status,
            len(state.approvals),
            state.debited_at,
            state.paid_at,
            state.provider_ref,
            state.failure,
        )
        assert row is not None
        return _record(row)
