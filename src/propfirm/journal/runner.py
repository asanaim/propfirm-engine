"""The engine process loop: read inputs, journal them, then acknowledge.

Start-up:
1. Store the setup if the journal is new; otherwise require the configured
   setup to equal the stored one.
2. Rebuild. From the latest snapshot when there is one: check it against the
   digest its checkpoint record holds, reload the ledger entries up to it,
   then replay the records after it. Otherwise (or with `full_replay`)
   replay the whole journal. Every replayed record must come out identical
   (acceptance test 1).
3. Use the newest price key, creating one for a new journal or after a purge.
4. With an anchor store: require the journal to contain every anchored
   record with the anchored hash, then anchor the latest checkpoint if a
   crash or an anchor outage came between its commit and its anchor.

Each poll:
1. Read a batch (pending messages first after a restart).
2. Skip messages at or before the journal head: they were journaled before
   a crash that came ahead of their acknowledgement.
3. Apply the rest in order and append their records, ledger entries, prices
   and snapshots in one transaction.
4. Acknowledge the whole batch.
5. Anchor the batch's last checkpoint (each anchor pins every record before
   it, so the latest one is enough).

The commit in step 3 is the only point where an input takes effect. A crash
before it loses only in-memory state, which the next start rebuilds from the
journal; the messages are still pending and are processed again with the
same result. A crash after it leaves messages pending that step 2 skips.
So every message takes effect exactly once (acceptance test 4).

Fail-stop: if reading, applying, committing or acknowledging raises, the
runner refuses further work; memory may be ahead of the journal, so the
process must restart and rebuild.

Anchoring is not fail-stop [DEFAULT]: an anchor store outage must not halt
trading. A failed publish is kept in `anchor_error` and retried on every
later poll until it succeeds; the journal itself is unaffected.

A resumed start trusts the journal before the snapshot (it was verified
when the snapshot was taken, and is pinned by its anchor). `full_replay`
and `verify_chain` re-check all of it; run them as a periodic audit.
"""

import secrets
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Protocol

from propfirm.journal.anchor import Anchor, AnchorStore
from propfirm.journal.codec import digest
from propfirm.journal.machine import (
    CODEC,
    PRICE_KEY_BYTES,
    JournalIntegrityError,
    Machine,
    Record,
    Snapshot,
    StoredRecord,
    check_link,
    check_price,
)
from propfirm.journal.setup import EngineSetup, SetupError
from propfirm.journal.stream import InputMessage
from propfirm.ledger import Entry


class InputSource(Protocol):
    async def read(self, count: int, block_ms: int | None = None) -> list[InputMessage]: ...

    async def ack(self, entry_ids: Sequence[str]) -> int: ...


class JournalStore(Protocol):
    async def setup(self) -> str | None:
        """The stored setup text, or None for a new journal."""
        ...

    async def init_setup(self, text: str) -> None: ...

    def records(self, after: int = 0) -> AsyncIterator[StoredRecord]:
        """Every record with seq > `after`, in order, with its price."""
        ...

    async def record(self, seq: int) -> Record | None: ...

    async def entries_until(self, seq: int) -> list[Entry]:
        """Every ledger entry of records 1..seq, in journal order."""
        ...

    async def latest_snapshot(self) -> Snapshot | None: ...

    async def price_keys(self) -> dict[int, bytes]: ...

    async def add_price_key(self, secret: bytes) -> int:
        """Store a new price key; return its id (larger than any before)."""
        ...

    async def append(
        self, batch: Sequence[StoredRecord], snapshots: Sequence[Snapshot] = ()
    ) -> None:
        """Persist records, ledger entries, prices and snapshots atomically."""
        ...


class RunnerStoppedError(RuntimeError):
    """The runner failed earlier; restart the process to rebuild."""


async def rebuild(store: JournalStore, *, full: bool = False) -> Machine:
    """The engine at the journal head; raise on any discrepancy."""
    text = await store.setup()
    if text is None:
        raise SetupError("the journal has no setup")
    keys = await store.price_keys()
    snapshot = None if full else await store.latest_snapshot()
    if snapshot is None:
        machine = Machine(text, keys)
    else:
        record = await store.record(snapshot.seq)
        if record is None:
            raise JournalIntegrityError(f"snapshot {snapshot.seq} has no record")
        entries = await store.entries_until(snapshot.seq)
        machine = Machine.resume(text, keys, snapshot, record, entries)
    async for stored in store.records(after=machine.seq):
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
    keys = await store.price_keys()
    seq, head = 0, digest(text)
    async for stored in store.records():
        check_link(stored.record, seq, head)
        check_price(stored, keys)
        seq, head = stored.record.seq, stored.record.hash
    return seq, head


async def check_anchors(
    store: JournalStore, journal: str, head_seq: int, anchors: AnchorStore
) -> None:
    """Raise unless the journal holds every anchored record unchanged."""
    for anchor in await anchors.anchors(journal):
        if anchor.seq > head_seq:
            raise JournalIntegrityError(
                f"journal ends at record {head_seq}, anchored at {anchor.seq}: rolled back"
            )
        record = await store.record(anchor.seq)
        if record is None or record.hash != anchor.hash:
            raise JournalIntegrityError(f"record {anchor.seq} differs from its anchor")


def _new_secret() -> bytes:
    return secrets.token_bytes(PRICE_KEY_BYTES)


class Runner:
    def __init__(
        self,
        machine: Machine,
        store: JournalStore,
        source: InputSource,
        anchors: AnchorStore | None,
    ) -> None:
        """Use `Runner.start`."""
        self._machine = machine
        self._store = store
        self._source = source
        self._anchors = anchors
        self._stopped = False
        self._unanchored: Anchor | None = None
        self.anchor_error: Exception | None = None

    @classmethod
    async def start(
        cls,
        store: JournalStore,
        source: InputSource,
        setup: EngineSetup,
        *,
        anchors: AnchorStore | None = None,
        full_replay: bool = False,
        new_price_key: Callable[[], bytes] = _new_secret,
    ) -> Runner:
        """`new_price_key` makes the secret when a key is needed; tests fix it."""
        text = CODEC.encode(setup)
        stored = await store.setup()
        if stored is None:
            await store.init_setup(text)
        elif stored != text:
            raise SetupError("configured setup differs from the journal's setup")
        machine = await rebuild(store, full=full_replay)
        keys = await store.price_keys()
        if keys:
            key_id = max(keys)
        else:
            secret = new_price_key()
            key_id = await store.add_price_key(secret)
            machine.add_price_key(key_id, secret)
        machine.use_price_key(key_id)
        runner = cls(machine, store, source, anchors)
        if anchors is not None:
            await check_anchors(store, machine.fingerprint, machine.seq, anchors)
            await runner._anchor_latest_checkpoint(anchors)
        return runner

    @property
    def machine(self) -> Machine:
        return self._machine

    async def poll(self, count: int = 100, block_ms: int | None = None) -> int:
        """Process one batch; return the number of messages read."""
        if self._stopped:
            raise RunnerStoppedError("runner stopped after a failure")
        try:
            messages = await self._source.read(count, block_ms)
            batch: list[StoredRecord] = []
            snapshots: list[Snapshot] = []
            for m in messages:
                if self._machine.is_journaled(m.entry_id):
                    continue
                batch.append(self._machine.apply(m.entry_id, m.item))
                snapshot = self._machine.take_snapshot()
                if snapshot is not None:
                    snapshots.append(snapshot)
            if batch:
                await self._store.append(batch, snapshots)
            if messages:
                await self._source.ack([m.entry_id for m in messages])
        except BaseException:
            self._stopped = True
            raise
        if snapshots and self._anchors is not None:
            last = snapshots[-1].seq
            record = next(s.record for s in batch if s.record.seq == last)
            self._unanchored = Anchor(self._machine.fingerprint, record.seq, record.hash)
        await self._publish_anchor()
        return len(messages)

    async def drain(self, count: int = 100) -> int:
        """Poll until a read returns nothing; return the messages read."""
        total = 0
        while read := await self.poll(count):
            total += read
        return total

    async def _anchor_latest_checkpoint(self, anchors: AnchorStore) -> None:
        snapshot = await self._store.latest_snapshot()
        if snapshot is None:
            return
        known = await anchors.anchors(self._machine.fingerprint)
        if known and known[-1].seq >= snapshot.seq:
            return
        record = await self._store.record(snapshot.seq)
        assert record is not None  # rebuild checked it
        self._unanchored = Anchor(self._machine.fingerprint, record.seq, record.hash)
        await self._publish_anchor()

    async def _publish_anchor(self) -> None:
        if self._anchors is None or self._unanchored is None:
            return
        try:
            await self._anchors.publish(self._unanchored)
        except Exception as exc:
            self.anchor_error = exc
            return
        self._unanchored = None
        self.anchor_error = None
