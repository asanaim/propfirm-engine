"""Submit an input to the engine and wait for the record it produced.

The stream entry id Valkey assigns to the input becomes the journal
record's `source_id`, so the outcome is found by that id once the engine
has committed it. The engine decides; the gateway only carries the input
and reads the result.

Limit [DEFAULT]: the wait polls PostgreSQL (5 ms, doubling to 50 ms); a
LISTEN/NOTIFY wake-up can replace it if load test 6 needs it.
"""

import asyncio
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.journal.inputs import Input
from propfirm.journal.machine import CODEC
from propfirm.service.jsonview import view

_SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class InputWriter(Protocol):
    async def publish(self, item: Input, entry_id: str = "*") -> str: ...


@dataclass(frozen=True, slots=True)
class Outcome:
    entry_id: str
    seq: int | None  # None: not journaled within the wait
    events: Sequence[Any]  # JSON views of the record's events


class Gateway:
    def __init__(self, writer: InputWriter, pool: asyncpg.Pool, schema: str) -> None:
        if not _SCHEMA.fullmatch(schema):
            raise ValueError(f"schema must match {_SCHEMA.pattern}")
        self._writer = writer
        self._pool = pool
        self._schema = schema

    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome:
        entry_id = await self._writer.publish(item)
        return await self.outcome(entry_id, wait_s)

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome:
        deadline = time.monotonic() + wait_s
        delay = 0.005
        while True:
            try:
                row = await self._pool.fetchrow(
                    f"SELECT seq, events FROM {self._schema}.journal WHERE source_id = $1",
                    entry_id,
                )
            except asyncpg.UndefinedTableError:
                row = None  # the engine has not created its journal yet
            if row is not None:
                events = CODEC.decode(row["events"])
                assert isinstance(events, tuple)
                return Outcome(entry_id, row["seq"], [view(e) for e in events])
            if time.monotonic() >= deadline:
                return Outcome(entry_id, None, [])
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.05)
