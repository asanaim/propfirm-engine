"""Shared rig for the journal tests.

- `engine_setup()`: four symbols, every product rule set, a US news block.
- `session(seed)`: a seeded list of inputs that crosses the 17:00 New York
  reset and the news blocks, with stale prices, a silence, an outage, feed
  duplicates, refused inputs, malformed messages, a rule-set version and
  news events added as inputs, and checkpoints. Commands are chosen
  by driving a scratch engine, so they are plausible for the state they
  meet; the list itself is fixed per seed.
- `MemoryStream` / `MemoryJournal`: in-memory stand-ins with the same
  delivery and commit rules as Valkey and PostgreSQL.
- `FaultyStore` / `FaultySource`: simulated process death at a chosen call.
- `start(...)`: Runner.start with a fixed price key, so journals compare.
- `observe(machine)`: every query the engine answers, for state comparison.
"""

import random
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction

import valkey.asyncio as avalkey

from propfirm.journal.anchor import AnchorStore
from propfirm.journal.inputs import (
    AddRuleSet,
    Advance,
    CancelNews,
    CancelOrder,
    Checkpoint,
    ClosePosition,
    Input,
    ModifyStops,
    OpenAccount,
    Outage,
    PlaceOrder,
    ScheduleNews,
    apply,
)
from propfirm.journal.machine import (
    Machine,
    Record,
    Snapshot,
    StoredRecord,
    record_hash,
    source_key,
)
from propfirm.journal.runner import InputSource, JournalStore, Runner
from propfirm.journal.setup import EngineSetup, build, installed_tzdata
from propfirm.journal.stream import InputMessage, decode_input, encode_input
from propfirm.ledger import Entry
from propfirm.market.guards import GuardConfig
from propfirm.market.symbols import SYMBOLS
from propfirm.market.synthetic import Delay, Silence, SyntheticConfig, generate
from propfirm.market.types import PriceUpdate
from propfirm.rules.news import EventScope, RestrictedEvent, trade_blocks
from propfirm.rules.products import RULESETS
from propfirm.rules.supervisor import RuleSupervisor
from propfirm.trading.instruments import default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side
from tests.rules_rig import ny

T0 = ny(2026, 10, 28, 16, 58)  # two minutes before the reset
SYMS = ("EUR/USD", "USD/JPY", "XAU/USD", "BTC/USD")
NEWS = (RestrictedEvent("usd", T0 + 200_000, EventScope.CURRENCY, "USD"),)  # blocks T0+80s..
LITE_V2 = replace(RULESETS["lite-s1-v1"], rsv_id="lite-s1-v2", daily_loss=Fraction(4, 100))
ACCOUNTS = {
    "A": ("lite-s1-v1", 500_000),
    "B": ("instant-funded-v1", 500_000),
    "C": ("classic-s1-v1", 1_000_000),
    "F": ("lite-s1-v2", 500_000),  # opened on a version published by input
}
CONSUMER = "engine-1"
TEST_KEY = bytes(range(32))
CHECKPOINT_EVERY = 400  # inputs


def engine_setup() -> EngineSetup:
    specs = default_instruments()
    blocks = trade_blocks(NEWS, {s: SYMBOLS[s] for s in SYMS})
    return EngineSetup(
        config_version="engine-journal-test",
        instruments=tuple(specs[s] for s in SYMS),
        rule_sets=tuple(RULESETS.values()),
        guard=GuardConfig(default_gap_threshold_ms=5_000),
        news_windows=blocks,
        trade_blocks=blocks,
        tzdata_version=installed_tzdata(),
    )


@dataclass(frozen=True, slots=True)
class Raw:
    """Stream fields sent as they are (to produce malformed messages)."""

    fields: dict[str, str]


type Item = Input | Raw


def session(seed: int, seconds: int = 240) -> list[Item]:
    rng = random.Random(seed)
    setup = engine_setup()
    sup, _ = build(setup)
    rule_sets = setup.rule_set_map()
    items: list[Item] = []

    def feed(item: Item) -> None:
        if not isinstance(item, Raw | Checkpoint):
            apply(sup, rule_sets, item)
        items.append(item)

    feed(AddRuleSet(LITE_V2, T0))
    feed(AddRuleSet(LITE_V2, T0))  # versions are immutable: refused
    feed(ScheduleNews(RestrictedEvent("ecb", T0 + 170_000, EventScope.CURRENCY, "EUR"), T0))
    feed(ScheduleNews(RestrictedEvent("boj", T0 + 300_000, EventScope.CURRENCY, "JPY"), T0))
    feed(ScheduleNews(RestrictedEvent("eia", T0 + 90_000, EventScope.OIL_INVENTORY), T0))  # no oil
    feed(ScheduleNews(RestrictedEvent("ecb", T0 + 1, EventScope.CURRENCY, "EUR"), T0))  # dup id
    for account, (rsv, size) in ACCOUNTS.items():
        feed(OpenAccount(account, rsv, size, T0))
    feed(OpenAccount("A", "lite-s1-v1", 500_000, T0))  # duplicate account
    feed(OpenAccount("D", "no-such-rule-set", 500_000, T0))
    feed(OpenAccount("E", "classic-s1-v1", 500_000, T0))  # Classic has no $5K size
    feed(Raw({"input": "{not json"}))
    feed(Raw({"symbol": "EUR/USD", "price": "110000"}))  # price-stream format: wrong field set
    config = SyntheticConfig(
        seed=f"journal-{seed}",
        symbols=SYMS,
        jump_per_mille=30,
        delays=(Delay("EUR/USD", T0 + 20_000, T0 + 25_000, 3_000),),
        silences=(Silence("XAU/USD", T0 + 150_000, T0 + 160_000),),
    )
    updates = list(generate(config, T0, T0 + seconds * 1_000))
    n = 0
    cancelled = False
    for index, update in enumerate(updates):
        feed(update)
        roll = rng.randrange(60)
        if roll == 0:
            feed(update)  # the feed repeats an update
        elif roll == 1:
            feed(Advance(update.receive_ts + rng.randrange(1, 3_000)))
        elif roll == 2:
            feed(PriceUpdate("GBP/USD", index + 1, update.source_ts, update.receive_ts, 127_000))
        if index == len(updates) // 2:
            feed(Outage(update.receive_ts))
        if update.receive_ts >= T0 + 60_000 and not cancelled:
            cancelled = True
            feed(CancelNews("boj", update.receive_ts))  # not started: removed
            feed(CancelNews("ecb", update.receive_ts))  # started at T0+50s: refused
            feed(CancelNews("nope", update.receive_ts))
        if len(items) % CHECKPOINT_EVERY == 0:
            feed(Checkpoint(update.receive_ts))
        if rng.randrange(3) == 0:
            n += 1
            feed(command(sup, rng, n, update.receive_ts))
    return items


def command(sup: RuleSupervisor, rng: random.Random, n: int, now: int) -> Input:
    engine = sup.engine
    account = rng.choice(list(ACCOUNTS))
    ts = now + rng.randrange(50)
    positions = engine.positions(account)
    pending = engine.pending_orders(account)
    roll = rng.randrange(12)
    if positions and roll < 4:
        position = rng.choice(positions)
        if roll == 0:
            exit_price = position.open_price
            offset = rng.randrange(20, 400)
            sl = exit_price - offset if position.side is Side.BUY else exit_price + offset
            return ModifyStops(account, position.position_id, max(1, sl), None, ts)
        volume = None if roll == 1 else max(1, position.volume // 2)
        return ClosePosition(account, position.position_id, ts, volume)
    if pending and roll == 4:
        return CancelOrder(account, rng.choice(pending).request.order_id, ts)
    if roll == 5:
        return CancelOrder(account, "no-such-order", ts)
    symbol = rng.choice(SYMS)
    quote = engine.quote(symbol)
    mid = quote.mid if quote else 1_000
    order_type = rng.choice([OrderType.MARKET, OrderType.MARKET, OrderType.LIMIT, OrderType.STOP])
    side = rng.choice([Side.BUY, Side.SELL])
    level = None
    if order_type is not OrderType.MARKET:
        offset = rng.randrange(1, 200)
        below = (side is Side.BUY) == (order_type is OrderType.LIMIT)
        level = max(1, mid - offset if below else mid + offset)
    volume = rng.choice([10, 50, 100, 200])
    request = OrderRequest(f"o{n}", account, symbol, side, order_type, volume, level)
    return PlaceOrder(request, ts)


# --- in-memory stream and journal -----------------------------------------------------


def fields_of(item: Item) -> dict[str, str]:
    return dict(item.fields) if isinstance(item, Raw) else encode_input(item)


class MemoryStream:
    """One stream with one consumer group; ids are "1-0", "2-0", ..."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str] | None]] = []
        self.delivered = 0
        self.pending: dict[str, dict[str, None]] = {}  # consumer -> ids in order

    def publish(self, items: Sequence[Item]) -> list[str]:
        ids = []
        for item in items:
            entry_id = f"{len(self.entries) + 1}-0"
            self.entries.append((entry_id, fields_of(item)))
            ids.append(entry_id)
        return ids

    def delete(self, entry_id: str) -> None:
        """Like XDEL: the entry's data is gone, its pending reference stays."""
        index = next(i for i, (e, _) in enumerate(self.entries) if e == entry_id)
        self.entries[index] = (entry_id, None)

    def reader(self, consumer: str = CONSUMER) -> MemoryReader:
        return MemoryReader(self, consumer)


class MemoryReader:
    def __init__(self, stream: MemoryStream, consumer: str) -> None:
        self._stream = stream
        self._consumer = consumer
        self._pending_drained = False
        self._cursor = (0, 0)

    async def read(self, count: int, block_ms: int | None = None) -> list[InputMessage]:
        s = self._stream
        pending = s.pending.setdefault(self._consumer, {})
        data = dict(s.entries)
        if not self._pending_drained:
            ids = [i for i in pending if source_key(i) > self._cursor][:count]
            if ids:
                self._cursor = source_key(ids[-1])
                return [InputMessage(i, decode_input(data[i])) for i in ids]
            self._pending_drained = True
        new = s.entries[s.delivered : s.delivered + count]
        s.delivered += len(new)
        for entry_id, _ in new:
            pending[entry_id] = None
        return [InputMessage(i, decode_input(fields)) for i, fields in new]

    async def ack(self, entry_ids: Sequence[str]) -> int:
        pending = self._stream.pending.setdefault(self._consumer, {})
        acked = [i for i in entry_ids if i in pending]
        for i in acked:
            del pending[i]
        return len(acked)


class MemoryJournal:
    """Keeps PostgreSQL's constraints: contiguous seq, unique source ids,
    snapshots only at journaled records, key ids increasing."""

    def __init__(self) -> None:
        self.setup_text: str | None = None
        self.rows: list[StoredRecord] = []
        self.snapshots: list[Snapshot] = []
        self.keys: dict[int, bytes] = {}
        self._next_key = 1

    def copy(self) -> MemoryJournal:
        other = MemoryJournal()
        other.setup_text = self.setup_text
        other.rows = list(self.rows)
        other.snapshots = list(self.snapshots)
        other.keys = dict(self.keys)
        other._next_key = self._next_key
        return other

    def purge_licensed(self) -> None:
        self.rows = [replace(r, price=None) for r in self.rows]
        self.snapshots = []
        self.keys = {}

    async def setup(self) -> str | None:
        return self.setup_text

    async def init_setup(self, text: str) -> None:
        if self.setup_text is not None:
            raise ValueError("setup already stored")
        self.setup_text = text

    async def records(self, after: int = 0) -> AsyncIterator[StoredRecord]:
        for row in list(self.rows):
            if row.record.seq > after:
                yield row

    async def record(self, seq: int) -> Record | None:
        return next((r.record for r in self.rows if r.record.seq == seq), None)

    async def entries_until(self, seq: int) -> list[Entry]:
        return [e for r in self.rows if r.record.seq <= seq for e in r.record.entries]

    async def latest_snapshot(self) -> Snapshot | None:
        return max(self.snapshots, key=lambda s: s.seq, default=None)

    async def price_keys(self) -> dict[int, bytes]:
        return dict(self.keys)

    async def add_price_key(self, secret: bytes) -> int:
        key_id, self._next_key = self._next_key, self._next_key + 1
        self.keys[key_id] = secret
        return key_id

    async def append(
        self, batch: Sequence[StoredRecord], snapshots: Sequence[Snapshot] = ()
    ) -> None:
        seen = {r.record.source_id for r in self.rows}
        for offset, stored in enumerate(batch):
            if stored.record.seq != len(self.rows) + offset + 1:
                raise ValueError("seq not contiguous")
            if stored.record.source_id in seen:
                raise ValueError("duplicate source id")
            seen.add(stored.record.source_id)
        top = len(self.rows) + len(batch)
        if any(not 1 <= snap.seq <= top for snap in snapshots):
            raise ValueError("snapshot of an unknown record")
        self.rows.extend(batch)
        self.snapshots.extend(snapshots)


def rechain(rows: list[StoredRecord], start: int) -> None:
    """Recompute hashes from index `start` on, as a forger rewriting the
    chain would."""
    for i in range(start, len(rows)):
        prev = rows[i - 1].record.hash if i else rows[i].record.prev_hash
        record = replace(rows[i].record, prev_hash=prev, hash="")
        rows[i] = replace(rows[i], record=replace(record, hash=record_hash(record)))


# --- faults --------------------------------------------------------------------------


class Crash(Exception):
    """Simulated process death."""


class Fault(StrEnum):
    AFTER_READ = "after_read"  # messages delivered (pending), not applied
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"
    BEFORE_ACK = "before_ack"
    AFTER_ACK = "after_ack"


class FaultyStore:
    def __init__(self, inner: JournalStore, fault: Fault, at: int) -> None:
        self._inner, self._fault, self._at, self._calls = inner, fault, at, 0

    async def setup(self) -> str | None:
        return await self._inner.setup()

    async def init_setup(self, text: str) -> None:
        await self._inner.init_setup(text)

    def records(self, after: int = 0) -> AsyncIterator[StoredRecord]:
        return self._inner.records(after)

    async def record(self, seq: int) -> Record | None:
        return await self._inner.record(seq)

    async def entries_until(self, seq: int) -> list[Entry]:
        return await self._inner.entries_until(seq)

    async def latest_snapshot(self) -> Snapshot | None:
        return await self._inner.latest_snapshot()

    async def price_keys(self) -> dict[int, bytes]:
        return await self._inner.price_keys()

    async def add_price_key(self, secret: bytes) -> int:
        return await self._inner.add_price_key(secret)

    async def append(
        self, batch: Sequence[StoredRecord], snapshots: Sequence[Snapshot] = ()
    ) -> None:
        self._calls += 1
        hit = self._calls == self._at
        if hit and self._fault is Fault.BEFORE_COMMIT:
            raise Crash
        await self._inner.append(batch, snapshots)
        if hit and self._fault is Fault.AFTER_COMMIT:
            raise Crash


class FaultySource:
    def __init__(self, inner: InputSource, fault: Fault, at: int) -> None:
        self._inner, self._fault, self._at = inner, fault, at
        self._reads = self._acks = 0

    async def read(self, count: int, block_ms: int | None = None) -> list[InputMessage]:
        messages = await self._inner.read(count, block_ms)
        if messages:
            self._reads += 1
            if self._reads == self._at and self._fault is Fault.AFTER_READ:
                raise Crash
        return messages

    async def ack(self, entry_ids: Sequence[str]) -> int:
        self._acks += 1
        hit = self._acks == self._at
        if hit and self._fault is Fault.BEFORE_ACK:
            raise Crash
        acked = await self._inner.ack(entry_ids)
        if hit and self._fault is Fault.AFTER_ACK:
            raise Crash
        return acked


async def start(
    store: JournalStore,
    source: InputSource,
    *,
    anchors: AnchorStore | None = None,
    full_replay: bool = False,
) -> Runner:
    """Runner.start with the rig's setup and a fixed price key."""
    return await Runner.start(
        store,
        source,
        engine_setup(),
        anchors=anchors,
        full_replay=full_replay,
        new_price_key=lambda: TEST_KEY,
    )


async def run_memory(
    items: Sequence[Item], faults: Sequence[tuple[Fault, int]] = (), batch: int = 50
) -> tuple[MemoryJournal, MemoryStream, Runner, int]:
    """Publish `items`, then run one engine life per fault (each may die),
    then a final life that drains the stream. Returns the crash count."""
    store, stream = MemoryJournal(), MemoryStream()
    stream.publish(items)
    crashes = 0
    for fault, at in faults:
        runner = await start(
            FaultyStore(store, fault, at), FaultySource(stream.reader(), fault, at)
        )
        try:
            await runner.drain(batch)
        except Crash:
            crashes += 1
    final = await start(store, stream.reader())
    await final.drain(batch)
    return store, stream, final, crashes


# --- observation ----------------------------------------------------------------------


def observe(machine: Machine) -> dict[str, object]:
    sup = machine.supervisor
    engine = sup.engine
    accounts = {
        a: (
            sup.status(a),
            engine.balance(a),
            engine.equity(a),
            engine.used_margin(a),
            sup.floors(a),
            sup.trading_days(a),
            sup.consistency_ok(a),
            engine.is_locked(a),
            engine.positions(a),
            engine.pending_orders(a),
        )
        for a in ACCOUNTS
    }
    return {
        "now": engine.now,
        "accounts": accounts,
        "symbols": {s: (engine.quote(s), engine.is_frozen(s)) for s in SYMS},
        "entries": machine.ledger.entries(),
        "balances": machine.ledger.balances(),
        "seq": machine.seq,
        "head": machine.head,
        "source_id": machine.source_id,
        "state": machine.state(),  # everything the engine remembers, in its own order
    }


def records(rows: Sequence[StoredRecord]) -> list[Record]:
    return [r.record for r in rows]


async def publish_valkey(client: avalkey.Valkey, stream: str, items: Sequence[Item]) -> list[str]:
    """Publish with the ids MemoryStream uses ("1-0", "2-0", ...), so journals
    from both transports compare equal."""
    pipe = client.pipeline(transaction=False)
    for n, item in enumerate(items, start=1):
        pipe.xadd(stream, fields_of(item), id=f"{n}-0")
    return [i.decode() if isinstance(i, bytes) else str(i) for i in await pipe.execute()]
