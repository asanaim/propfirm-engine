"""Per-account history built from the journal.

    python -m propfirm.service.projector

Reads journal records after its cursor and writes every event that names an
account to `{read}.account_events`, one transaction per page, cursor
included, so a crash never skips or repeats a record. One projector per
read schema (advisory lock).

The tables are derived: `rebuild` drops and recreates them from the journal.
They hold fill prices (licensed data), so a licensed-data purge drops them.
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
"""


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
            rows = await self._conn.fetch(
                f"SELECT seq, events FROM {self._j}.journal WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                limit,
            )
            if not rows:
                return 0
            out: list[tuple[str, int, int, str, int, str]] = []
            for row in rows:
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
            await self._conn.execute(f"UPDATE {self._r}.cursor SET seq = $1", rows[-1]["seq"])
            return len(rows)

    async def rebuild(self) -> None:
        """Drop the projections; the next runs rebuild them from record 1."""
        async with self._conn.transaction():
            await self._conn.execute(f"DROP SCHEMA {self._r} CASCADE")
            await self._conn.execute(_DDL.format(r=self._r))


class HistoryReader:
    def __init__(self, pool: asyncpg.Pool, read_schema: str) -> None:
        self._pool, self._r = pool, _check(read_schema)

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
