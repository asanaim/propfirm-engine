"""Acceptance tests 1 and 4 against PostgreSQL and Valkey.

- Replay: a session journaled to PostgreSQL rebuilds, from a new
  connection, into the state of the live engine, and the stored records
  equal the in-memory reference bit for bit.
- The record cannot be changed through SQL: append-only triggers, and the
  hash chain and replay for whatever gets past them.
- Crash and restart: simulated crashes at every point, then real SIGKILLs
  of an engine process, give the reference journal with nothing pending.
"""

import asyncio
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
import valkey.asyncio as avalkey

from propfirm.journal.machine import JournalIntegrityError, PricesUnavailableError
from propfirm.journal.postgres import JournalLockedError, PostgresJournal
from propfirm.journal.runner import rebuild, verify_chain
from propfirm.journal.stream import InputStreamReader
from tests.journal_rig import (
    CONSUMER,
    Crash,
    Fault,
    FaultySource,
    FaultyStore,
    MemoryStream,
    observe,
    publish_valkey,
    run_memory,
    session,
    start,
)
from tests.journal_worker import GROUP

pytestmark = pytest.mark.integration

ITEMS = session(0, seconds=120)
REFERENCE, _stream, _runner, _crashes = asyncio.run(run_memory(ITEMS))
REFERENCE_STATE = observe(_runner.machine)
ROOT = Path(__file__).resolve().parents[2]


async def stored_rows(database_url: str, schema: str) -> list[object]:
    """Every record read back through a second connection."""
    reader = await PostgresJournal.connect(database_url, schema)
    try:
        return [r async for r in reader.records()]
    finally:
        await reader.close()


async def journal_live(database_url: str, schema: str) -> dict[str, object]:
    store = await PostgresJournal.connect(database_url, schema)
    try:
        stream = MemoryStream()
        stream.publish(ITEMS)
        runner = await start(store, stream.reader())
        await runner.drain(200)
        return observe(runner.machine)
    finally:
        await store.close()


@asynccontextmanager
async def sql(database_url: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(database_url)
    try:
        yield conn
    finally:
        await conn.close()


# --- acceptance test 1 --------------------------------------------------------------


def test_postgres_journal_rebuilds_the_live_state(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        live = await journal_live(database_url, pg_schema)
        assert live == REFERENCE_STATE
        assert await stored_rows(database_url, pg_schema) == REFERENCE.rows
        store = await PostgresJournal.connect(database_url, pg_schema)
        try:
            assert observe(await rebuild(store)) == live
        finally:
            await store.close()

    asyncio.run(scenario())


def test_ledger_is_queryable_and_balances(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        live = await journal_live(database_url, pg_schema)
        async with sql(database_url) as conn:
            rows = await conn.fetch(
                f"SELECT account, sum(amount)::bigint AS balance "
                f"FROM {pg_schema}.ledger_postings GROUP BY account"
            )
            unbalanced = await conn.fetchval(
                f"SELECT count(*) FROM (SELECT entry_id FROM {pg_schema}.ledger_postings "
                f"GROUP BY entry_id HAVING sum(amount) <> 0) AS x"
            )
        assert {r["account"]: r["balance"] for r in rows} == live["balances"]
        assert unbalanced == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("table", ["setup", "journal", "ledger_entries", "ledger_postings"])
@pytest.mark.parametrize(
    "statement",
    ["UPDATE {t} SET {c} = {c}", "DELETE FROM {t}", "TRUNCATE {t} CASCADE"],
)
def test_record_tables_are_append_only(
    database_url: str, pg_schema: str, table: str, statement: str
) -> None:
    column = {"setup": "body", "journal": "events", "ledger_entries": "kind"}.get(table, "amount")

    async def scenario() -> None:
        await journal_live(database_url, pg_schema)
        async with sql(database_url) as conn:
            text = statement.format(t=f"{pg_schema}.{table}", c=column)
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute(text)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("table", "update"),
    [
        (
            "ledger_postings",
            "UPDATE {s}.ledger_postings SET amount = amount * 2 WHERE entry_id = "
            "(SELECT entry_id FROM {s}.ledger_entries WHERE kind = 'trade_pnl' "
            "ORDER BY journal_seq LIMIT 1)",
        ),
        (
            "journal",
            "UPDATE {s}.journal SET events = '[]' WHERE seq = "
            "(SELECT min(seq) FROM {s}.journal WHERE events LIKE '%PositionOpened%')",
        ),
    ],
)
def test_tampering_behind_disabled_triggers_is_detected(
    database_url: str, pg_schema: str, table: str, update: str
) -> None:
    async def scenario() -> None:
        await journal_live(database_url, pg_schema)
        async with sql(database_url) as conn:
            await conn.execute(f"ALTER TABLE {pg_schema}.{table} DISABLE TRIGGER USER")
            assert await conn.execute(update.format(s=pg_schema)) != "UPDATE 0"
        store = await PostgresJournal.connect(database_url, pg_schema)
        try:
            with pytest.raises(JournalIntegrityError, match="hash does not match"):
                await rebuild(store, full=True)  # the audit replays every record
            with pytest.raises(JournalIntegrityError, match="hash does not match"):
                await verify_chain(store)
        finally:
            await store.close()

    asyncio.run(scenario())


def test_one_writer_per_journal(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        first = await PostgresJournal.connect(database_url, pg_schema)
        with pytest.raises(JournalLockedError):
            await PostgresJournal.connect(database_url, pg_schema)
        await first.close()
        second = await PostgresJournal.connect(database_url, pg_schema)
        await second.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE {s}.prices SET price = price + 1",
        "UPDATE {s}.price_keys SET secret = secret",
        "UPDATE {s}.snapshots SET state = state",
        "TRUNCATE {s}.snapshots",
    ],
)
def test_licensed_tables_take_no_updates(database_url: str, pg_schema: str, statement: str) -> None:
    async def scenario() -> None:
        await journal_live(database_url, pg_schema)
        async with sql(database_url) as conn:
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute(statement.format(s=pg_schema))

    asyncio.run(scenario())


def test_start_resumes_from_the_postgres_snapshot(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        live = await journal_live(database_url, pg_schema)
        store = await PostgresJournal.connect(database_url, pg_schema)
        try:
            snapshot = await store.latest_snapshot()
            assert snapshot == REFERENCE.snapshots[-1]
            assert observe(await rebuild(store)) == live
            assert observe(await rebuild(store, full=True)) == live
        finally:
            await store.close()

    asyncio.run(scenario())


def test_purging_licensed_data_keeps_the_journal_but_stops_replay(
    database_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        await journal_live(database_url, pg_schema)
        store = await PostgresJournal.connect(database_url, pg_schema)
        try:
            purged = await store.purge_licensed()
            assert purged == {
                "prices": sum(1 for r in REFERENCE.rows if r.price is not None),
                "price_keys": 1,
                "snapshots": len(REFERENCE.snapshots),
            }
            with pytest.raises(PricesUnavailableError):
                await rebuild(store)
            rows = [r async for r in store.records()]
            assert [r.record for r in rows] == [r.record for r in REFERENCE.rows]
            assert await verify_chain(store) == (len(rows), rows[-1].record.hash)
        finally:
            await store.close()

    asyncio.run(scenario())


# --- acceptance test 4 --------------------------------------------------------------


async def valkey_session(valkey_url: str) -> tuple[avalkey.Valkey, str]:
    client = avalkey.Valkey.from_url(valkey_url)
    stream = f"test:inputs:{uuid.uuid4().hex}"
    await publish_valkey(client, stream, ITEMS)
    return client, stream


async def finish(
    database_url: str, schema: str, client: avalkey.Valkey, stream: str
) -> dict[str, object]:
    """Restart once more without faults, drain, and check the outcome."""
    store = await connect_retrying(database_url, schema)
    try:
        reader = InputStreamReader(client, stream, GROUP, CONSUMER)
        runner = await start(store, reader)
        await runner.drain(100)
        state = observe(runner.machine)
    finally:
        await store.close()
    assert await stored_rows(database_url, schema) == REFERENCE.rows
    pending = await client.xpending(stream, GROUP)
    assert pending["pending"] == 0
    return state


async def connect_retrying(database_url: str, schema: str) -> PostgresJournal:
    """After a kill, PostgreSQL releases the dead session's lock shortly."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return await PostgresJournal.connect(database_url, schema)
        except JournalLockedError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.05)


def test_crash_points_against_valkey_and_postgres(
    valkey_url: str, database_url: str, pg_schema: str
) -> None:
    plan = [
        (Fault.AFTER_READ, 2),
        (Fault.BEFORE_COMMIT, 2),
        (Fault.AFTER_COMMIT, 3),
        (Fault.BEFORE_ACK, 2),
        (Fault.AFTER_ACK, 4),
        (Fault.AFTER_COMMIT, 1),
    ]

    async def scenario() -> None:
        client, stream = await valkey_session(valkey_url)
        try:
            await InputStreamReader(client, stream, GROUP, CONSUMER).ensure_group()
            for fault, at in plan:
                store = await PostgresJournal.connect(database_url, pg_schema)
                try:
                    reader = InputStreamReader(client, stream, GROUP, CONSUMER)
                    runner = await start(
                        FaultyStore(store, fault, at), FaultySource(reader, fault, at)
                    )
                    with pytest.raises(Crash):
                        await runner.drain(25)
                finally:
                    await store.close()  # the process is gone: its session ends
            assert await finish(database_url, pg_schema, client, stream) == REFERENCE_STATE
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(scenario())


def test_killed_engine_process_recovers(valkey_url: str, database_url: str, pg_schema: str) -> None:
    async def count(conn: asyncpg.Connection) -> int:
        try:
            return int(await conn.fetchval(f"SELECT count(*) FROM {pg_schema}.journal"))
        except asyncpg.UndefinedTableError:
            return 0

    async def run_until(conn: asyncpg.Connection, stream: str, records: int) -> None:
        args = [database_url, pg_schema, valkey_url, stream]
        worker = subprocess.Popen([sys.executable, "-m", "tests.journal_worker", *args], cwd=ROOT)
        try:
            await wait_for(lambda: count(conn), records, worker)
        finally:
            worker.kill()  # SIGKILL: no cleanup, no final commit or ack
            worker.wait()

    async def scenario() -> None:
        client, stream = await valkey_session(valkey_url)
        try:
            async with sql(database_url) as conn:
                total = len(ITEMS)
                for target in (total * k // 10 for k in (1, 2, 3, 4, 5, 6)):
                    await run_until(conn, stream, target)
                killed_at = await count(conn)
                assert total * 6 // 10 <= killed_at < total
            assert await finish(database_url, pg_schema, client, stream) == REFERENCE_STATE
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(scenario())


async def wait_for(
    value: Callable[[], Awaitable[int]], target: int, worker: subprocess.Popen[bytes]
) -> None:
    deadline = time.monotonic() + 60
    while await value() < target:
        if worker.poll() is not None:
            raise AssertionError(f"worker exited early with {worker.returncode}")
        if time.monotonic() > deadline:
            raise AssertionError("worker made no progress")
        await asyncio.sleep(0.002)
