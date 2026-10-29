"""An engine process for the kill test: reads the Valkey input stream,
journals to PostgreSQL, one message per transaction, until the stream is
drained. The test kills it with SIGKILL at an arbitrary moment.

Usage: python -m tests.journal_worker DATABASE_URL SCHEMA VALKEY_URL STREAM
"""

import asyncio
import sys

import valkey.asyncio as avalkey

from propfirm.journal.postgres import PostgresJournal
from propfirm.journal.runner import Runner
from propfirm.journal.stream import InputStreamReader
from tests.journal_rig import CONSUMER, engine_setup

GROUP = "engine"


async def main(database_url: str, schema: str, valkey_url: str, stream: str) -> None:
    client = avalkey.Valkey.from_url(valkey_url)
    store = await PostgresJournal.connect(database_url, schema)
    try:
        reader = InputStreamReader(client, stream, GROUP, CONSUMER)
        await reader.ensure_group()
        runner = await Runner.start(store, reader, engine_setup())
        await runner.drain(count=1)
    finally:
        await store.close()
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:5]))
