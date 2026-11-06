"""Per-account history built from the journal.

    python -m propfirm.service.projector

Reads journal records after its cursor and writes every event that names an
account to `{read}.account_events`, and every price update into one-minute
bars per symbol (`{read}.bars`: open, high, low and close of the prices in
points, in journal order, by the minute of their source time), one
transaction per page, cursor included, so a crash never skips or repeats a
record. One projector per read schema (advisory lock).

The tables are derived: `rebuild` drops and recreates them from the journal
(bars appear for prices journaled after their table was created; a rebuild
fills in the earlier ones). They hold fill prices and prices (licensed
data), so a licensed-data purge drops them.
"""

import asyncio
import json
import os
import re
import signal
from typing import Any, Self

import asyncpg

from propfirm.journal.machine import CODEC
from propfirm.service._wait import wait_or_stop
from propfirm.service.config import ServiceConfig
from propfirm.service.jsonview import view

_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_LOCK_CLASS = 0x70726F6A  # "proj"

_DDL = """
CREATE SCHEMA IF NOT EXISTS {r};
CREATE TABLE IF NOT EXISTS {r}.cursor (
    id smallint PRIMARY KEY CHECK (id = 1),
    seq bigint NOT NULL CHECK (seq >= 0)
);
INSERT INTO {r}.cursor (id, seq) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS {r}.account_events (
    account_id text NOT NULL,
    seq bigint NOT NULL,
    idx integer NOT NULL,
    kind text NOT NULL,
    ts bigint NOT NULL,
    event jsonb NOT NULL,
    PRIMARY KEY (account_id, seq, idx)
);
CREATE TABLE IF NOT EXISTS {r}.bars (
    symbol text NOT NULL,
    minute bigint NOT NULL,          -- ms UTC, a multiple of 60,000
    open bigint NOT NULL,            -- points (the symbol's digits)
    high bigint NOT NULL,
    low bigint NOT NULL,
    close bigint NOT NULL,
    updates integer NOT NULL,
    first_seq bigint NOT NULL,       -- journal records of the open and close
    last_seq bigint NOT NULL,
    PRIMARY KEY (symbol, minute)
);
"""

MINUTE_MS = 60_000


class Bar:
    """One minute of one symbol's prices, in journal order."""

    __slots__ = ("close", "first_seq", "high", "last_seq", "low", "open", "updates")

    def __init__(self, seq: int, price: int) -> None:
        self.open = self.high = self.low = self.close = price
        self.first_seq = self.last_seq = seq
        self.updates = 1

    def add(self, seq: int, price: int) -> None:
        self.high, self.low = max(self.high, price), min(self.low, price)
        self.close, self.last_seq = price, seq
        self.updates += 1


class ProjectorLockedError(RuntimeError):
    """Another projector holds this read schema."""


def _check(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise ValueError(f"schema must match {_NAME.pattern}")
    return name


class Projector:
    def __init__(self, conn: asyncpg.Connection, journal: str, read: str) -> None:
        """Use `Projector.connect`."""
        self._conn, self._j, self._r = conn, journal, read

    @classmethod
    async def connect(cls, dsn: str, journal_schema: str, read_schema: str) -> Self:
        journal, read = _check(journal_schema), _check(read_schema)
        conn = await asyncpg.connect(dsn)
        try:
            if not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, hashtext($2))", _LOCK_CLASS, read
            ):
                raise ProjectorLockedError(f"read schema {read} has another projector")
            await conn.execute(_DDL.format(r=read))
        except BaseException:
            await conn.close()
            raise
        return cls(conn, journal, read)

    async def close(self) -> None:
        await self._conn.close()

    async def cursor(self) -> int:
        seq: int = await self._conn.fetchval(f"SELECT seq FROM {self._r}.cursor")
        return seq

    async def run_once(self, limit: int = 1_000) -> int:
        """Project the next page of records; return how many were read
        (0 also while the engine has not created its journal yet)."""
        try:
            return await self._page(limit)
        except asyncpg.UndefinedTableError:
            return 0

    async def _page(self, limit: int) -> int:
        async with self._conn.transaction():
            after = await self._conn.fetchval(f"SELECT seq FROM {self._r}.cursor FOR UPDATE")
            # prices live in their own store (licensing: purgeable), beside the record
            rows = await self._conn.fetch(
                f"SELECT j.seq, j.events, p.symbol, p.source_ts, p.price "
                f"FROM {self._j}.journal j LEFT JOIN {self._j}.prices p ON p.journal_seq = j.seq "
                f"WHERE j.seq > $1 ORDER BY j.seq LIMIT $2",
                after,
                limit,
            )
            if not rows:
                return 0
            out: list[tuple[str, int, int, str, int, str]] = []
            bars: dict[tuple[str, int], Bar] = {}
            for row in rows:
                if row["symbol"] is not None:
                    source_ts: int = row["source_ts"]
                    key = (row["symbol"], source_ts - source_ts % MINUTE_MS)
                    found = bars.get(key)
                    if found is None:
                        bars[key] = Bar(row["seq"], row["price"])
                    else:
                        found.add(row["seq"], row["price"])
                events = CODEC.decode(row["events"])
                assert isinstance(events, tuple)
                for idx, event in enumerate(events):
                    account_id = getattr(event, "account_id", None)
                    if isinstance(account_id, str):
                        data: Any = view(event)
                        ts = int(getattr(event, "ts", 0))
                        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
                        out.append((account_id, row["seq"], idx, type(event).__name__, ts, text))
            if out:
                await self._conn.executemany(
                    f"INSERT INTO {self._r}.account_events (account_id, seq, idx, kind, ts, event) "
                    f"VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                    out,
                )
            if bars:
                # a minute may span pages: keep the earlier open, widen the
                # range, take the later close
                await self._conn.executemany(
                    f"INSERT INTO {self._r}.bars AS b (symbol, minute, open, high, low, close, "
                    f"updates, first_seq, last_seq) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                    f"ON CONFLICT (symbol, minute) DO UPDATE SET "
                    f"high = greatest(b.high, excluded.high), low = least(b.low, excluded.low), "
                    f"close = excluded.close, last_seq = excluded.last_seq, "
                    f"updates = b.updates + excluded.updates",
                    [
                        (s, m, b.open, b.high, b.low, b.close, b.updates, b.first_seq, b.last_seq)
                        for (s, m), b in bars.items()
                    ],
                )
            await self._conn.execute(f"UPDATE {self._r}.cursor SET seq = $1", rows[-1]["seq"])
            return len(rows)

    async def purge_licensed(self) -> dict[str, int]:
        """Licence ended: delete the bars and account histories (they hold
        prices and fill prices) and move the cursor to the journal's head,
        so nothing purged is projected again; return the rows deleted."""
        counts: dict[str, int] = {}
        async with self._conn.transaction():
            for table in ("bars", "account_events"):
                result: str = await self._conn.execute(f"DELETE FROM {self._r}.{table}")
                counts[table] = int(result.split()[-1])
            try:
                head = await self._conn.fetchval(f"SELECT max(seq) FROM {self._j}.journal")
            except asyncpg.UndefinedTableError:
                head = None
            await self._conn.execute(f"UPDATE {self._r}.cursor SET seq = $1", head or 0)
        return counts

    async def rebuild(self) -> None:
        """Drop the projections; the next runs rebuild them from record 1."""
        async with self._conn.transaction():
            await self._conn.execute(f"DROP SCHEMA {self._r} CASCADE")
            await self._conn.execute(_DDL.format(r=self._r))


# bar sizes: fixed lengths aligned to UTC, and the trading day (17:00 New York)
INTERVALS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
# the trading day of a minute: its New York date after adding 7 hours, so
# 17:00 New York starts the next day (DST included)
_TRADING_DAY = (
    "(extract(epoch FROM (date_trunc('day', (to_timestamp(minute / 1000) "
    "AT TIME ZONE 'America/New_York') + interval '7 hours') - interval '7 hours') "
    "AT TIME ZONE 'America/New_York') * 1000)::bigint"
)


class HistoryReader:
    def __init__(self, pool: asyncpg.Pool, read_schema: str) -> None:
        self._pool, self._r = pool, _check(read_schema)

    async def bars(
        self, symbol: str, interval: str, limit: int = 300, before: int | None = None
    ) -> list[dict[str, int]]:
        """The last `limit` bars of `interval` (oldest first) that start
        before `before` (ms UTC; default: up to the latest price). Prices in
        points; `time` is the bar's start, ms UTC."""
        length = INTERVALS[interval]
        bucket = _TRADING_DAY if interval == "1d" else f"minute - minute % {length}"
        r = self._r
        rows = await self._pool.fetch(
            f"WITH bounds AS (SELECT coalesce($3::bigint, max(minute) + 1) AS stop "
            f"FROM {r}.bars WHERE symbol = $1) "
            f"SELECT {bucket} AS time, (array_agg(open ORDER BY minute))[1] AS open, "
            f"max(high) AS high, min(low) AS low, "
            f"(array_agg(close ORDER BY minute DESC))[1] AS close, sum(updates)::int AS updates "
            f"FROM {r}.bars, bounds WHERE symbol = $1 AND minute < bounds.stop "
            f"AND minute >= bounds.stop - $4::bigint "
            f"GROUP BY 1 ORDER BY 1 DESC LIMIT $2",
            symbol,
            limit,
            before,
            (limit + 2) * length,  # enough minutes for `limit` bars, DST days included
        )
        return [dict(r) for r in reversed(rows)]

    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT seq, idx, event::text AS event FROM {self._r}.account_events "
            f"WHERE account_id = $1 AND seq > $2 ORDER BY seq, idx LIMIT $3",
            account_id,
            after_seq,
            limit,
        )
        return [{"seq": r["seq"], "idx": r["idx"], **json.loads(r["event"])} for r in rows]


async def _main() -> None:
    config = ServiceConfig.from_env(os.environ)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    projector = await Projector.connect(config.database_url, config.schema, config.read_schema)
    try:
        while not stop.is_set():
            if not await projector.run_once():
                await wait_or_stop(stop, 0.25)
    finally:
        await projector.close()


if __name__ == "__main__":
    asyncio.run(_main())
