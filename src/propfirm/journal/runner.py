"""The engine process loop: read inputs, journal them, then acknowledge.

Start-up:
1. Store the setup if the journal is new; otherwise require the configured
   setup to equal the stored one.
2. Rebuild: replay the whole journal into a fresh engine, verifying the hash
   chain, every recomputed record and the ledger (acceptance test 1).

Each poll:
1. Read a batch (pending messages first after a restart).
2. Skip messages at or before the journal head: they were journaled before
   a crash that came ahead of their acknowledgement.
3. Apply the rest in order and append their records in one transaction.
4. Acknowledge the whole batch.

The commit in step 3 is the only point where an input takes effect. A crash
before it loses only in-memory state, which the next start rebuilds from the
journal; the messages are still pending and are processed again with the
same result. A crash after it leaves messages pending that step 2 skips.
So every message takes effect exactly once (acceptance test 4).

Fail-stop: if applying, committing or acknowledging raises, the runner
refuses further work; memory may be ahead of the journal, so the process
must restart and rebuild.

Limit [DEFAULT]: start-up replays the whole journal. Snapshots (state at a
record, verified by its hash) will bound start-up time later.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from propfirm.journal.codec import digest
from propfirm.journal.machine import CODEC, Machine, StoredRecord, check_link, check_price
from propfirm.journal.setup import EngineSetup, SetupError
from propfirm.journal.stream import InputMessage


class InputSource(Protocol):
    async def read(self, count: int, block_ms: int | None = None) -> list[InputMessage]: ...

    async def ack(self, entry_ids: Sequence[str]) -> int: ...


class JournalStore(Protocol):
    async def setup(self) -> str | None:
        """The stored setup text, or None for a new journal."""
        ...

    async def init_setup(self, text: str) -> None: ...

    def records(self) -> AsyncIterator[StoredRecord]:
        """Every record in sequence order, with its price."""
        ...

    async def append(self, batch: Sequence[StoredRecord]) -> None:
        """Persist the records (and their prices and ledger entries) atomically."""
        ...


class RunnerStoppedError(RuntimeError):
    """The runner failed earlier; restart the process to rebuild."""


async def rebuild(store: JournalStore) -> Machine:
    """Replay the journal into a fresh engine; raise on any discrepancy."""
    text = await store.setup()
    if text is None:
        raise SetupError("the journal has no setup")
    machine = Machine(text)
    async for stored in store.records():
        machine.replay(stored)
    machine.ledger.verify()
    return machine


async def verify_chain(store: JournalStore) -> tuple[int, str]:
    """Check numbering, links, hashes and stored prices without replaying,
    so a journal whose licensed prices were purged can still be verified.
    Return (last seq, head hash)."""
    text = await store.setup()
    if text is None:
        raise SetupError("the journal has no setup")
    seq, head = 0, digest(text)
    async for stored in store.records():
        check_link(stored.record, seq, head)
        check_price(stored)
        seq, head = stored.record.seq, stored.record.hash
    return seq, head


class Runner:
    def __init__(self, machine: Machine, store: JournalStore, source: InputSource) -> None:
        """Use `Runner.start`."""
        self._machine = machine
        self._store = store
        self._source = source
        self._stopped = False

    @classmethod
    async def start(cls, store: JournalStore, source: InputSource, setup: EngineSetup) -> Runner:
        text = CODEC.encode(setup)
        stored = await store.setup()
        if stored is None:
            await store.init_setup(text)
        elif stored != text:
            raise SetupError("configured setup differs from the journal's setup")
        return cls(await rebuild(store), store, source)

    @property
    def machine(self) -> Machine:
        return self._machine

    async def poll(self, count: int = 100, block_ms: int | None = None) -> int:
        """Process one batch; return the number of messages read."""
        if self._stopped:
            raise RunnerStoppedError("runner stopped after a failure")
        try:
            messages = await self._source.read(count, block_ms)
            batch = [
                self._machine.apply(m.entry_id, m.item)
                for m in messages
                if not self._machine.is_journaled(m.entry_id)
            ]
            if batch:
                await self._store.append(batch)
            if messages:
                await self._source.ack([m.entry_id for m in messages])
        except BaseException:
            self._stopped = True
            raise
        return len(messages)

    async def drain(self, count: int = 100) -> int:
        """Poll until a read returns nothing; return the messages read."""
        total = 0
        while read := await self.poll(count):
            total += read
        return total
