"""The journal in PostgreSQL.

Tables, in one schema per engine:

    setup            the setup text and its fingerprint (one row)
    journal          one row per record (hash-chained)
    ledger_entries   the ledger: one row per entry, linked to its record
    ledger_postings  one row per posting (BIGINT cents)
    prices           the licensed price store: one row per price input,
                     keyed by the record (update) that carried it
    price_keys       the secrets of the price digests
    snapshots        engine state at checkpoint records

The record tables are append-only: triggers reject UPDATE, DELETE and
TRUNCATE. The licensed tables (`prices`, `price_keys`, `snapshots`, which
holds quotes) reject UPDATE but allow DELETE, so `purge_licensed` can remove
them together (CLAUDE.md); a purged journal still verifies its chain but can
no longer be replayed. The triggers stop mistakes, not a superuser, who can
disable them; the hash chain and the external anchor detect what they let
through.

One writer per schema: the store takes a session advisory lock on connect
and holds it for the life of the connection. A second writer fails at once.

One batch is one transaction: records, ledger rows, prices and snapshots
commit together or not at all.

Limit [DEFAULT]: the schema is created idempotently on connect; versioned
migrations come with the first change to it.
"""

import re
from collections.abc import AsyncIterator, Sequence
from typing import Self

import asyncpg

from propfirm.journal.codec import digest
from propfirm.journal.machine import Record, Snapshot, StoredRecord
from propfirm.ledger import Entry, Posting
from propfirm.market.types import PriceUpdate

_SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_LOCK_CLASS = 0x70726F70  # "prop": first key of the two-key advisory lock
_PAGE = 1_000

_DDL = """
CREATE SCHEMA IF NOT EXISTS {s};

CREATE TABLE IF NOT EXISTS {s}.setup (
    id smallint PRIMARY KEY CHECK (id = 1),
    body text NOT NULL,
    fingerprint text NOT NULL
);

CREATE TABLE IF NOT EXISTS {s}.journal (
    seq bigint PRIMARY KEY CHECK (seq >= 1),
    source_id text NOT NULL UNIQUE,
    kind text NOT NULL,
    ts bigint NOT NULL CHECK (ts >= 0),
    input text NOT NULL,
    events text NOT NULL,
    prev_hash text NOT NULL,
    hash text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS {s}.ledger_entries (
    entry_id text PRIMARY KEY,
    journal_seq bigint NOT NULL REFERENCES {s}.journal (seq),
    position integer NOT NULL CHECK (position >= 0),
    occurred_at bigint NOT NULL CHECK (occurred_at >= 0),
    kind text NOT NULL,
    reference text NOT NULL,
    UNIQUE (journal_seq, position)
);

CREATE TABLE IF NOT EXISTS {s}.ledger_postings (
    entry_id text NOT NULL REFERENCES {s}.ledger_entries (entry_id),
    position integer NOT NULL CHECK (position >= 0),
    account text NOT NULL,
    amount bigint NOT NULL CHECK (amount <> 0),
    PRIMARY KEY (entry_id, position)
);

CREATE TABLE IF NOT EXISTS {s}.prices (
    journal_seq bigint PRIMARY KEY REFERENCES {s}.journal (seq),
    symbol text NOT NULL,
    seq bigint NOT NULL,
    source_ts bigint NOT NULL,
    receive_ts bigint NOT NULL,
    price bigint NOT NULL
);
-- the source's own bid and ask (13 Nov 2026, D-13.10); null for a source
-- that sends one price and for every row written before
ALTER TABLE {s}.prices ADD COLUMN IF NOT EXISTS bid bigint;
ALTER TABLE {s}.prices ADD COLUMN IF NOT EXISTS ask bigint;

CREATE TABLE IF NOT EXISTS {s}.price_keys (
    key_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    secret bytea NOT NULL CHECK (length(secret) = 32)
);

CREATE TABLE IF NOT EXISTS {s}.snapshots (
    seq bigint PRIMARY KEY REFERENCES {s}.journal (seq),
    state text NOT NULL
);

CREATE OR REPLACE FUNCTION {s}.reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on %.% is not allowed: the table is append-only',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END
$$;
"""

_APPEND_ONLY = ("setup", "journal", "ledger_entries", "ledger_postings")
_PURGEABLE = ("prices", "price_keys", "snapshots")

_TRIGGERS = """
CREATE OR REPLACE TRIGGER {t}_append_only BEFORE {ops} ON {s}.{t}
    FOR EACH ROW EXECUTE FUNCTION {s}.reject_change();
CREATE OR REPLACE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {s}.{t}
    FOR EACH STATEMENT EXECUTE FUNCTION {s}.reject_change();
"""


class JournalLockedError(RuntimeError):
    """Another writer holds this journal."""


def _ddl(schema: str) -> str:
    parts = [_DDL.format(s=schema)]
    parts += [_TRIGGERS.format(s=schema, t=t, ops="UPDATE OR DELETE") for t in _APPEND_ONLY]
    parts += [_TRIGGERS.format(s=schema, t=t, ops="UPDATE") for t in _PURGEABLE]
    return "\n".join(parts)


class PostgresJournal:
    def __init__(self, conn: asyncpg.Connection, schema: str) -> None:
        """Use `PostgresJournal.connect`."""
        self._conn = conn
        self._s = schema

    @classmethod
    async def connect(cls, dsn: str, schema: str) -> Self:
        """Connect, take the writer lock, and create the schema if needed."""
        if not _SCHEMA.fullmatch(schema):
            raise ValueError(f"schema must match {_SCHEMA.pattern}, got {schema!r}")
        conn = await asyncpg.connect(dsn)
        try:
            locked = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, hashtext($2))", _LOCK_CLASS, schema
            )
            if not locked:
                raise JournalLockedError(f"journal {schema} has another writer")
            await conn.execute(_ddl(schema))
        except BaseException:
            await conn.close()
            raise
        return cls(conn, schema)

    async def close(self) -> None:
        await self._conn.close()

    async def setup(self) -> str | None:
        body: str | None = await self._conn.fetchval(f"SELECT body FROM {self._s}.setup")
        return body

    async def init_setup(self, text: str) -> None:
        await self._conn.execute(
            f"INSERT INTO {self._s}.setup (id, body, fingerprint) VALUES (1, $1, $2)",
            text,
            digest(text),
        )

    async def records(self, after: int = 0) -> AsyncIterator[StoredRecord]:
        while True:
            rows = await self._conn.fetch(
                f"SELECT seq, source_id, kind, ts, input, events, prev_hash, hash "
                f"FROM {self._s}.journal WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                _PAGE,
            )
            if not rows:
                return
            first, last = rows[0]["seq"], rows[-1]["seq"]
            entries = await self._entries(first, last)
            prices = await self._prices(first, last)
            for row in rows:
                seq = row["seq"]
                yield StoredRecord(_record(row, entries.get(seq, [])), prices.get(seq))
            after = last

    async def record(self, seq: int) -> Record | None:
        row = await self._conn.fetchrow(
            f"SELECT seq, source_id, kind, ts, input, events, prev_hash, hash "
            f"FROM {self._s}.journal WHERE seq = $1",
            seq,
        )
        if row is None:
            return None
        return _record(row, (await self._entries(seq, seq)).get(seq, []))

    async def entries_until(self, seq: int) -> list[Entry]:
        grouped = await self._entries(1, seq)
        return [e for s in sorted(grouped) for e in grouped[s]]

    async def latest_snapshot(self) -> Snapshot | None:
        row = await self._conn.fetchrow(
            f"SELECT seq, state FROM {self._s}.snapshots ORDER BY seq DESC LIMIT 1"
        )
        return None if row is None else Snapshot(row["seq"], row["state"])

    async def price_keys(self) -> dict[int, bytes]:
        rows = await self._conn.fetch(f"SELECT key_id, secret FROM {self._s}.price_keys")
        return {r["key_id"]: bytes(r["secret"]) for r in rows}

    async def add_price_key(self, secret: bytes) -> int:
        key_id: int = await self._conn.fetchval(
            f"INSERT INTO {self._s}.price_keys (secret) VALUES ($1) RETURNING key_id", secret
        )
        return key_id

    async def _entries(self, first: int, last: int) -> dict[int, list[Entry]]:
        rows = await self._conn.fetch(
            f"SELECT e.journal_seq, e.entry_id, e.occurred_at, e.kind, e.reference, "
            f"p.account, p.amount "
            f"FROM {self._s}.ledger_entries e "
            f"JOIN {self._s}.ledger_postings p ON p.entry_id = e.entry_id "
            f"WHERE e.journal_seq BETWEEN $1 AND $2 "
            f"ORDER BY e.journal_seq, e.position, p.position",
            first,
            last,
        )
        grouped: dict[int, list[tuple[asyncpg.Record, list[Posting]]]] = {}
        for row in rows:
            entries = grouped.setdefault(row["journal_seq"], [])
            if not entries or entries[-1][0]["entry_id"] != row["entry_id"]:
                entries.append((row, []))
            entries[-1][1].append(Posting(row["account"], row["amount"]))
        return {
            seq: [
                Entry(h["entry_id"], h["occurred_at"], h["kind"], tuple(ps), h["reference"])
                for h, ps in items
            ]
            for seq, items in grouped.items()
        }

    async def _prices(self, first: int, last: int) -> dict[int, PriceUpdate]:
        rows = await self._conn.fetch(
            f"SELECT journal_seq, symbol, seq, source_ts, receive_ts, price, bid, ask "
            f"FROM {self._s}.prices WHERE journal_seq BETWEEN $1 AND $2",
            first,
            last,
        )
        return {
            r["journal_seq"]: PriceUpdate(
                r["symbol"],
                r["seq"],
                r["source_ts"],
                r["receive_ts"],
                r["price"],
                r["bid"],
                r["ask"],
            )
            for r in rows
        }

    async def append(
        self, batch: Sequence[StoredRecord], snapshots: Sequence[Snapshot] = ()
    ) -> None:
        if not batch:
            return
        journal: list[tuple[object, ...]] = []
        entries: list[tuple[object, ...]] = []
        postings: list[tuple[object, ...]] = []
        prices: list[tuple[object, ...]] = []
        for stored in batch:
            r = stored.record
            journal.append(
                (r.seq, r.source_id, r.kind, r.ts, r.input, r.events, r.prev_hash, r.hash)
            )
            for position, e in enumerate(r.entries):
                entries.append((e.entry_id, r.seq, position, e.occurred_at, e.kind, e.reference))
                postings.extend(
                    (e.entry_id, i, p.account, p.amount) for i, p in enumerate(e.postings)
                )
            if stored.price is not None:
                u = stored.price
                prices.append(
                    (r.seq, u.symbol, u.seq, u.source_ts, u.receive_ts, u.price, u.bid, u.ask)
                )
        s = self._s
        async with self._conn.transaction():
            await self._conn.executemany(
                f"INSERT INTO {s}.journal (seq, source_id, kind, ts, input, events, prev_hash, "
                f"hash) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                journal,
            )
            if entries:
                await self._conn.executemany(
                    f"INSERT INTO {s}.ledger_entries (entry_id, journal_seq, position, "
                    f"occurred_at, kind, reference) VALUES ($1, $2, $3, $4, $5, $6)",
                    entries,
                )
                await self._conn.executemany(
                    f"INSERT INTO {s}.ledger_postings (entry_id, position, account, amount) "
                    f"VALUES ($1, $2, $3, $4)",
                    postings,
                )
            if prices:
                await self._conn.executemany(
                    f"INSERT INTO {s}.prices (journal_seq, symbol, seq, source_ts, receive_ts, "
                    f"price, bid, ask) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    prices,
                )
            if snapshots:
                await self._conn.executemany(
                    f"INSERT INTO {s}.snapshots (seq, state) VALUES ($1, $2)",
                    [(snap.seq, snap.state) for snap in snapshots],
                )

    async def purge_licensed(self) -> dict[str, int]:
        """Delete every stored price, price key and snapshot in one
        transaction (licence ended); return the rows deleted per table. Run
        with the engine stopped: this store holds the writer lock."""
        counts: dict[str, int] = {}
        async with self._conn.transaction():
            for table in _PURGEABLE:
                result: str = await self._conn.execute(f"DELETE FROM {self._s}.{table}")
                counts[table] = int(result.split()[-1])
        return counts


def _record(row: asyncpg.Record, entries: list[Entry]) -> Record:
    return Record(
        seq=row["seq"],
        source_id=row["source_id"],
        kind=row["kind"],
        ts=row["ts"],
        input=row["input"],
        events=row["events"],
        entries=tuple(entries),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )
