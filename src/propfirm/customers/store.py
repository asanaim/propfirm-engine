"""Customer records in PostgreSQL (schema `customers` by default).

Identifiers are short and fixed-width: CIF00000001 (customer), ORD00000001
(order), ACC00000001 (trading account), from database sequences.

Schema changes are numbered migrations (`propfirm.pgschema`); a new change
is a new number, never an edit.

Money is whole cents (BIGINT); discount shares are stored as exact text
("1/10"); times are UTC milliseconds supplied by the caller.
"""

import json
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Self

import asyncpg

from propfirm.pgschema import Conn, check_schema
from propfirm.pgschema import migrate as _migrate

_LOCK_CLASS = 0x63757374  # "cust"

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE SEQUENCE {s}.cif_seq;
        CREATE SEQUENCE {s}.ord_seq;
        CREATE SEQUENCE {s}.acc_seq;
        CREATE TABLE {s}.cif (
            cif text PRIMARY KEY,
            subject text NOT NULL UNIQUE,
            email text NOT NULL,
            full_name text NOT NULL,
            date_of_birth date NOT NULL,
            residence_country text NOT NULL CHECK (residence_country ~ '^[A-Z]{{2}}$'),
            status text NOT NULL CHECK (status IN ('registered', 'blocked')),
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.cif_events (
            id bigserial PRIMARY KEY,
            cif text REFERENCES {s}.cif (cif),
            subject text NOT NULL,
            ts bigint NOT NULL,
            kind text NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE INDEX ON {s}.cif_events (subject, id);
        CREATE TABLE {s}.screenings (
            id bigserial PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            provider text NOT NULL,
            result text NOT NULL CHECK (result IN ('clear', 'hit')),
            ts bigint NOT NULL,
            detail jsonb NOT NULL
        );
        CREATE TABLE {s}.discount_codes (
            code text PRIMARY KEY CHECK (code ~ '^[A-Z0-9-]{{3,32}}$'),
            discount text NOT NULL,
            active boolean NOT NULL,
            created_at bigint NOT NULL
        );
        CREATE TABLE {s}.orders (
            ord text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            product_id text NOT NULL,
            size_cents bigint NOT NULL CHECK (size_cents > 0),
            list_price_cents bigint NOT NULL CHECK (list_price_cents > 0),
            discount_kind text NOT NULL,
            discount text NOT NULL,
            discount_code text REFERENCES {s}.discount_codes (code),
            price_cents bigint NOT NULL CHECK (price_cents >= 0),
            rsv_id text NOT NULL,
            status text NOT NULL
                CHECK (status IN ('pending', 'paid', 'provisioned', 'failed')),
            provider text NOT NULL,
            provider_ref text NOT NULL UNIQUE,
            acc text UNIQUE,
            failure text,
            created_at bigint NOT NULL,
            paid_at bigint,
            provisioned_at bigint
        );
        CREATE INDEX ON {s}.orders (cif, product_id);
        CREATE TABLE {s}.accounts (
            acc text PRIMARY KEY,
            cif text NOT NULL REFERENCES {s}.cif (cif),
            ord text NOT NULL UNIQUE REFERENCES {s}.orders (ord),
            product_id text NOT NULL,
            rsv_id text NOT NULL,
            size_cents bigint NOT NULL,
            created_at bigint NOT NULL
        );
        CREATE INDEX ON {s}.accounts (cif);
        """,
    ),
)


class AlreadyRegisteredError(Exception):
    """This login identity already has a CIF."""


@dataclass(frozen=True, slots=True)
class CifRecord:
    cif: str
    subject: str
    email: str
    full_name: str
    date_of_birth: date
    residence_country: str
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class OrderRecord:
    ord: str
    cif: str
    product_id: str
    size_cents: int
    list_price_cents: int
    discount_kind: str
    discount: Fraction
    discount_code: str | None
    price_cents: int
    rsv_id: str
    status: str
    provider: str
    provider_ref: str
    acc: str | None
    failure: str | None
    created_at: int
    paid_at: int | None
    provisioned_at: int | None


@dataclass(frozen=True, slots=True)
class AccountRecord:
    acc: str
    cif: str
    ord: str
    product_id: str
    rsv_id: str
    size_cents: int
    created_at: int


def _check(name: str) -> str:
    return check_schema(name)


async def migrate(conn: Conn, schema: str) -> list[int]:
    """Apply the customer migrations not yet applied; return their numbers."""
    return await _migrate(conn, schema, _MIGRATIONS, _LOCK_CLASS)


def _share(text: str) -> Fraction:
    return Fraction(text)


def _order(row: asyncpg.Record) -> OrderRecord:
    return OrderRecord(
        ord=row["ord"],
        cif=row["cif"],
        product_id=row["product_id"],
        size_cents=row["size_cents"],
        list_price_cents=row["list_price_cents"],
        discount_kind=row["discount_kind"],
        discount=_share(row["discount"]),
        discount_code=row["discount_code"],
        price_cents=row["price_cents"],
        rsv_id=row["rsv_id"],
        status=row["status"],
        provider=row["provider"],
        provider_ref=row["provider_ref"],
        acc=row["acc"],
        failure=row["failure"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
        provisioned_at=row["provisioned_at"],
    )


def _cif(row: asyncpg.Record) -> CifRecord:
    return CifRecord(
        cif=row["cif"],
        subject=row["subject"],
        email=row["email"],
        full_name=row["full_name"],
        date_of_birth=row["date_of_birth"],
        residence_country=row["residence_country"],
        status=row["status"],
        created_at=row["created_at"],
    )


class CustomerStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = _check(schema)

    @classmethod
    async def open(cls, pool: asyncpg.Pool, schema: str) -> Self:
        async with pool.acquire() as conn:
            await migrate(conn, schema)
        return cls(pool, schema)

    # --- CIF ------------------------------------------------------------------------

    async def register(
        self,
        subject: str,
        email: str,
        full_name: str,
        date_of_birth: date,
        residence_country: str,
        ts: int,
        detail: dict[str, Any],
    ) -> CifRecord:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(f"SELECT 1 FROM {s}.cif WHERE subject = $1", subject):
                raise AlreadyRegisteredError(subject)
            number = await conn.fetchval(f"SELECT nextval('{s}.cif_seq')")
            cif = f"CIF{number:08d}"
            try:
                row = await conn.fetchrow(
                    f"INSERT INTO {s}.cif (cif, subject, email, full_name, date_of_birth, "
                    f"residence_country, status, created_at) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, 'registered', $7) RETURNING *",
                    cif,
                    subject,
                    email,
                    full_name,
                    date_of_birth,
                    residence_country,
                    ts,
                )
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyRegisteredError(subject) from exc
            await self._event(conn, cif, subject, ts, "registered", detail)
            assert row is not None
            return _cif(row)

    async def refused(self, subject: str, ts: int, detail: dict[str, Any]) -> None:
        """Record a refused registration (no CIF is created)."""
        async with self._pool.acquire() as conn:
            await self._event(conn, None, subject, ts, "registration_refused", detail)

    async def cif_by_subject(self, subject: str) -> CifRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.cif WHERE subject = $1", subject)
        return None if row is None else _cif(row)

    async def block(self, cif: str, subject: str, ts: int, detail: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"UPDATE {self._s}.cif SET status = 'blocked' WHERE cif = $1", cif)
            await self._event(conn, cif, subject, ts, "blocked", detail)

    async def events(self, subject: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT cif, ts, kind, detail::text AS detail FROM {self._s}.cif_events "
            f"WHERE subject = $1 ORDER BY id",
            subject,
        )
        return [
            {"cif": r["cif"], "ts": r["ts"], "kind": r["kind"], "detail": json.loads(r["detail"])}
            for r in rows
        ]

    async def _event(
        self,
        conn: Conn,
        cif: str | None,
        subject: str,
        ts: int,
        kind: str,
        detail: dict[str, Any],
    ) -> None:
        await conn.execute(
            f"INSERT INTO {self._s}.cif_events (cif, subject, ts, kind, detail) "
            f"VALUES ($1, $2, $3, $4, $5::jsonb)",
            cif,
            subject,
            ts,
            kind,
            json.dumps(detail, sort_keys=True),
        )

    # --- screening ------------------------------------------------------------------

    async def record_screening(
        self, cif: str, provider: str, result: str, ts: int, detail: dict[str, Any]
    ) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.screenings (cif, provider, result, ts, detail) "
            f"VALUES ($1, $2, $3, $4, $5::jsonb)",
            cif,
            provider,
            result,
            ts,
            json.dumps(detail, sort_keys=True),
        )

    async def screened_clear(self, cif: str) -> bool:
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.screenings WHERE cif = $1 AND result = 'clear'", cif
        )
        return found is not None

    # --- discount codes -------------------------------------------------------------

    async def add_code(self, code: str, discount: Fraction, ts: int) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.discount_codes (code, discount, active, created_at) "
            f"VALUES ($1, $2, true, $3)",
            code,
            str(discount),
            ts,
        )

    async def code_discount(self, code: str) -> Fraction | None:
        text = await self._pool.fetchval(
            f"SELECT discount FROM {self._s}.discount_codes WHERE code = $1 AND active", code
        )
        return None if text is None else _share(text)

    # --- orders and accounts --------------------------------------------------------

    async def prior_accounts(self, cif: str, product_id: str) -> int:
        count: int = await self._pool.fetchval(
            f"SELECT count(*) FROM {self._s}.orders WHERE cif = $1 AND product_id = $2 "
            f"AND status IN ('paid', 'provisioned')",
            cif,
            product_id,
        )
        return count

    async def create_order(
        self,
        cif: str,
        product_id: str,
        size_cents: int,
        list_price_cents: int,
        discount_kind: str,
        discount: Fraction,
        discount_code: str | None,
        price_cents: int,
        rsv_id: str,
        provider: str,
        ts: int,
        provider_ref: str | None = None,
    ) -> OrderRecord:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            number = await conn.fetchval(f"SELECT nextval('{s}.ord_seq')")
            ord_id = f"ORD{number:08d}"
            row = await conn.fetchrow(
                f"INSERT INTO {s}.orders (ord, cif, product_id, size_cents, list_price_cents, "
                f"discount_kind, discount, discount_code, price_cents, rsv_id, status, provider, "
                f"provider_ref, created_at) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending', $11, $12, $13) "
                f"RETURNING *",
                ord_id,
                cif,
                product_id,
                size_cents,
                list_price_cents,
                discount_kind,
                str(discount),
                discount_code,
                price_cents,
                rsv_id,
                provider,
                provider_ref or f"pending-{ord_id}",
                ts,
            )
            assert row is not None
            return _order(row)

    async def set_provider_ref(self, ord_id: str, provider_ref: str) -> None:
        await self._pool.execute(
            f"UPDATE {self._s}.orders SET provider_ref = $2 WHERE ord = $1", ord_id, provider_ref
        )

    async def order(self, ord_id: str) -> OrderRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.orders WHERE ord = $1", ord_id)
        return None if row is None else _order(row)

    async def order_by_ref(self, provider: str, provider_ref: str) -> OrderRecord | None:
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.orders WHERE provider = $1 AND provider_ref = $2",
            provider,
            provider_ref,
        )
        return None if row is None else _order(row)

    async def orders_for(self, cif: str) -> list[OrderRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.orders WHERE cif = $1 ORDER BY ord", cif
        )
        return [_order(r) for r in rows]

    async def mark_paid(self, ord_id: str, ts: int) -> bool:
        """pending -> paid; False when it was not pending (a repeated notice)."""
        result: str = await self._pool.execute(
            f"UPDATE {self._s}.orders SET status = 'paid', paid_at = $2 "
            f"WHERE ord = $1 AND status = 'pending'",
            ord_id,
            ts,
        )
        return result.endswith(" 1")

    async def assign_account(self, ord_id: str) -> str:
        """The order's ACC id, allocated once and kept, so a retried
        provisioning opens the same account."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            acc: str | None = await conn.fetchval(
                f"SELECT acc FROM {s}.orders WHERE ord = $1 FOR UPDATE", ord_id
            )
            if acc is None:
                number = await conn.fetchval(f"SELECT nextval('{s}.acc_seq')")
                acc = f"ACC{number:08d}"
                await conn.execute(f"UPDATE {s}.orders SET acc = $2 WHERE ord = $1", ord_id, acc)
            return acc

    async def mark_provisioned(self, order: OrderRecord, acc: str, ts: int) -> None:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"INSERT INTO {s}.accounts (acc, cif, ord, product_id, rsv_id, size_cents, "
                f"created_at) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (acc) DO NOTHING",
                acc,
                order.cif,
                order.ord,
                order.product_id,
                order.rsv_id,
                order.size_cents,
                ts,
            )
            await conn.execute(
                f"UPDATE {s}.orders SET status = 'provisioned', provisioned_at = $2 "
                f"WHERE ord = $1 AND status = 'paid'",
                order.ord,
                ts,
            )

    async def mark_failed(self, ord_id: str, failure: str) -> None:
        await self._pool.execute(
            f"UPDATE {self._s}.orders SET status = 'failed', failure = $2 "
            f"WHERE ord = $1 AND status = 'paid'",
            ord_id,
            failure,
        )

    async def unprovisioned(self) -> list[OrderRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.orders WHERE status = 'paid' ORDER BY ord"
        )
        return [_order(r) for r in rows]

    async def accounts_for(self, cif: str) -> list[AccountRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.accounts WHERE cif = $1 ORDER BY acc", cif
        )
        return [AccountRecord(**dict(r)) for r in rows]

    async def account_owner(self, acc: str) -> str | None:
        owner: str | None = await self._pool.fetchval(
            f"SELECT cif FROM {self._s}.accounts WHERE acc = $1", acc
        )
        return owner
