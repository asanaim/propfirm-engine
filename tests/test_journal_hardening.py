"""Keyed price digests, news events and rule-set versions as inputs,
snapshots, and external anchors."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from propfirm.journal.anchor import Anchor, AnchorError, FileAnchorStore
from propfirm.journal.inputs import (
    AddRuleSet,
    CancelNews,
    InputRejected,
    InputRejectReason,
    OpenAccount,
    PlaceOrder,
    RuleSetAdded,
    ScheduleNews,
)
from propfirm.journal.machine import (
    CODEC,
    JournalIntegrityError,
    Machine,
    PriceRef,
    Snapshot,
    StoredRecord,
    check_price,
    price_ref,
)
from propfirm.journal.runner import rebuild, verify_chain
from propfirm.market.types import PriceUpdate
from propfirm.rules.news import EventScope, RestrictedEvent
from propfirm.trading.events import NewsCancelled, NewsScheduled, OrderRejected, PositionOpened
from propfirm.trading.orders import OrderRequest, OrderType, Side
from tests.journal_rig import (
    LITE_V2,
    T0,
    TEST_KEY,
    MemoryJournal,
    MemoryStream,
    engine_setup,
    observe,
    rechain,
    run_memory,
    session,
    start,
)

U = PriceUpdate("EUR/USD", 7, T0, T0, 110_123)
OTHER_KEY = bytes(range(32, 64))
ITEMS = session(0, seconds=120)
STORE, _stream, _runner, _crashes = asyncio.run(run_memory(ITEMS))
LIVE = observe(_runner.machine)


class Driver:
    """A machine fed inputs with increasing source ids."""

    def __init__(self) -> None:
        self.machine = Machine(CODEC.encode(engine_setup()), {1: TEST_KEY})
        self.machine.use_price_key(1)
        self.n = 0
        self.prices = 0

    def send(self, item: object) -> list[object]:
        self.n += 1
        stored = self.machine.apply(f"{self.n}-0", item)  # type: ignore[arg-type]
        events = CODEC.decode(stored.record.events)
        assert isinstance(events, tuple)
        return list(events)

    def price(self, price: int, at: int, symbol: str = "EUR/USD") -> list[object]:
        self.prices += 1
        return self.send(PriceUpdate(symbol, self.prices, at, at, price))

    def order(self, order_id: str, at: int, account: str = "A") -> list[object]:
        request = OrderRequest(order_id, account, "EUR/USD", Side.BUY, OrderType.MARKET, 10)
        return self.send(PlaceOrder(request, at))


def without_head(state: dict[str, object]) -> dict[str, object]:
    """Engine state alone: the journal head differs when record content does."""
    return {k: v for k, v in state.items() if k != "head"}


def rejected(events: Sequence[object]) -> object:
    (event,) = [e for e in events if isinstance(e, InputRejected | OrderRejected)]
    return event.reason


# --- keyed price digests ------------------------------------------------------------


def test_price_digest_is_keyed() -> None:
    plain = hashlib.sha256(CODEC.encode(U).encode()).hexdigest()
    keyed = price_ref(U, 1, TEST_KEY)
    assert keyed.digest != plain  # cannot be recomputed (or brute-forced) without the key
    assert price_ref(U, 1, OTHER_KEY).digest != keyed.digest
    assert price_ref(U, 2, TEST_KEY).key_id == 2


def test_price_keys_must_be_32_bytes_and_stable() -> None:
    m = Driver().machine
    with pytest.raises(ValueError):
        m.add_price_key(2, b"short")
    with pytest.raises(ValueError):
        m.add_price_key(1, OTHER_KEY)  # id 1 already holds another secret
    with pytest.raises(KeyError):
        m.use_price_key(9)


def test_a_new_key_applies_to_new_prices_and_old_ones_still_verify() -> None:
    async def scenario() -> MemoryJournal:
        store, stream = MemoryJournal(), MemoryStream()
        stream.publish(ITEMS)
        first = await start(store, stream.reader())
        await first.poll(500)
        assert list(store.keys) == [1]  # created for the new journal
        await store.add_price_key(OTHER_KEY)  # rotation: the next start uses key 2
        second = await start(store, stream.reader())
        assert list(store.keys) == [1, 2]  # existing keys reused, none created
        await second.drain()
        return store

    store = asyncio.run(scenario())
    refs = [CODEC.decode(r.record.input) for r in store.rows if r.price is not None]
    assert {ref.key_id for ref in refs if isinstance(ref, PriceRef)} == {1, 2}
    assert without_head(observe(asyncio.run(rebuild(store, full=True)))) == without_head(LIVE)


def test_purge_removes_prices_keys_and_snapshots() -> None:
    store = STORE.copy()
    store.purge_licensed()
    assert asyncio.run(verify_chain(store)) == (LIVE["seq"], LIVE["head"])
    priced = next(r for r in STORE.rows if r.price is not None)
    with pytest.raises(JournalIntegrityError, match="without its key"):
        check_price(priced, {})  # a price left behind without its key is flagged


# --- news events and rule-set versions as inputs ------------------------------------

ECB = RestrictedEvent("ecb", T0 + 600_000, EventScope.CURRENCY, "EUR")  # blocks T0+480s..+720s


def opened_driver(rsv_id: str = "lite-s1-v2") -> Driver:
    d = Driver()
    d.send(OpenAccount("A", rsv_id, 10_000_000, T0))
    d.price(110_000, T0)
    return d


def test_scheduled_news_blocks_trades_and_widens_spreads() -> None:
    d = opened_driver("lite-funded-v1")  # news windows bind funded accounts only
    events = d.send(ScheduleNews(ECB, T0 + 1))
    assert events == [NewsScheduled(T0 + 1, "ecb", ("EUR/USD",), T0 + 480_000, T0 + 720_001)]
    normal = d.machine.supervisor.engine.quote("EUR/USD")
    d.price(110_000, T0 + 480_000)  # window start: inclusive
    assert rejected(d.order("o1", T0 + 480_000)) == "news_window"
    widened = d.machine.supervisor.engine.quote("EUR/USD")
    assert normal is not None and widened is not None and widened.spread > normal.spread
    d.price(110_000, T0 + 720_000)  # last blocked millisecond
    assert rejected(d.order("o2", T0 + 720_000)) == "news_window"
    d.price(110_000, T0 + 720_001)
    assert any(isinstance(e, PositionOpened) for e in d.order("o3", T0 + 720_001))


def test_a_cancelled_event_no_longer_blocks() -> None:
    d = opened_driver()
    d.send(ScheduleNews(ECB, T0 + 1))
    assert d.send(CancelNews("ecb", T0 + 2)) == [NewsCancelled(T0 + 2, "ecb")]
    d.price(110_000, T0 + 500_000)  # inside the cancelled window
    assert any(isinstance(e, PositionOpened) for e in d.order("o1", T0 + 500_000))


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        (
            [ScheduleNews(ECB, T0), ScheduleNews(replace(ECB, ts=T0 + 9), T0)],
            "duplicate_news_event",
        ),
        (
            [ScheduleNews(RestrictedEvent("eia", T0 + 9, EventScope.OIL_INVENTORY), T0)],
            "no_affected_symbols",
        ),
        ([CancelNews("ecb", T0)], "unknown_news_event"),
        ([ScheduleNews(ECB, T0), CancelNews("ecb", T0 + 480_000)], "news_started"),
        ([ScheduleNews(ECB, T0), CancelNews("ecb", T0 + 800_000)], "news_started"),  # over
        ([AddRuleSet(LITE_V2, T0), AddRuleSet(LITE_V2, T0)], "duplicate_rule_set"),
        ([OpenAccount("F", "lite-s1-v9", 500_000, T0)], "unknown_rule_set"),  # not published
    ],
)
def test_refused_calendar_and_rule_set_inputs(inputs: list[object], reason: str) -> None:
    d = opened_driver()
    before = d.machine.state()
    events: list[object] = []
    for item in inputs:
        events = d.send(item)
    assert rejected(events) == InputRejectReason(reason)
    if len(inputs) == 1:
        assert d.machine.state().engine.calendar == before.engine.calendar


def test_an_event_id_is_never_reused_even_after_its_window() -> None:
    d = opened_driver()
    d.send(ScheduleNews(ECB, T0))
    d.price(110_000, T0 + 800_000)  # window over and dropped from the calendar
    assert d.machine.supervisor.engine.news_event("ecb") is None
    events = d.send(ScheduleNews(replace(ECB, ts=T0 + 990_000), T0 + 800_001))
    assert rejected(events) == InputRejectReason.DUPLICATE_NEWS_EVENT


def test_published_rule_set_opens_accounts_and_old_versions_stay() -> None:
    d = opened_driver()
    assert d.send(AddRuleSet(LITE_V2, T0 + 5)) == [RuleSetAdded(T0, "lite-s1-v9")]
    d.send(OpenAccount("F", "lite-s1-v9", 500_000, T0 + 6))
    sup = d.machine.supervisor
    assert sup.floors("F")[0] == 500_000 - 20_000  # 4% daily on v2
    assert sup.floors("A")[0] == 10_000_000 - 300_000  # A keeps v1's 3%
    assert set(d.machine.rule_sets) >= {"lite-s1-v2", "lite-s1-v9"}


# --- snapshots ----------------------------------------------------------------------


class CountingStore(MemoryJournal):
    """Records where each replay started."""

    def __init__(self, inner: MemoryJournal) -> None:
        super().__init__()
        self.setup_text, self.rows = inner.setup_text, inner.rows
        self.snapshots, self.keys = inner.snapshots, inner.keys
        self.starts: list[int] = []

    async def records(self, after: int = 0) -> AsyncIterator[StoredRecord]:
        self.starts.append(after)
        async for row in super().records(after):
            yield row


def with_events(rows: Sequence[StoredRecord], seq: int, *, after: bool) -> int:
    """Index of the nearest record after (or before) record `seq` that has
    events, so blanking them is a real change."""
    order = range(seq, len(rows)) if after else range(seq - 2, -1, -1)
    return next(i for i in order if rows[i].record.events != "[]")


def with_snapshots_up_to(seq: int) -> MemoryJournal:
    store = STORE.copy()
    store.snapshots = [s for s in store.snapshots if s.seq <= seq]
    return store


def test_resuming_from_any_snapshot_equals_a_full_replay() -> None:
    assert len(STORE.snapshots) >= 4
    for snapshot in STORE.snapshots:
        store = CountingStore(with_snapshots_up_to(snapshot.seq))
        assert observe(asyncio.run(rebuild(store))) == LIVE
        assert store.starts == [snapshot.seq]  # only the tail was replayed


def test_restart_after_a_checkpoint_resumes_and_continues_identically() -> None:
    async def scenario() -> tuple[MemoryJournal, list[int]]:
        store, stream = MemoryJournal(), MemoryStream()
        stream.publish(ITEMS)
        await (await start(store, stream.reader())).poll(1_000)
        counting = CountingStore(store)
        runner = await start(counting, stream.reader())
        await runner.drain()
        return store, counting.starts

    store, starts = asyncio.run(scenario())
    assert starts == [max(s.seq for s in STORE.snapshots if s.seq <= 1_000)]
    assert store.rows == STORE.rows
    assert store.snapshots == STORE.snapshots


def test_snapshot_is_pinned_by_its_checkpoint_record() -> None:
    latest = STORE.snapshots[-1]
    state = json.loads(latest.state)
    state["engine"]["now"] += 1
    forged = Snapshot(latest.seq, json.dumps(state, sort_keys=True, separators=(",", ":")))
    store = STORE.copy()
    store.snapshots[-1] = forged
    with pytest.raises(JournalIntegrityError, match="does not match its record"):
        asyncio.run(rebuild(store))


def test_ledger_before_a_snapshot_must_match_its_summary() -> None:
    store = STORE.copy()
    first = store.snapshots[0].seq
    index = next(i for i, r in enumerate(store.rows) if r.record.entries and r.record.seq < first)
    r = store.rows[index].record
    entry = r.entries[0]
    doubled = tuple(replace(p, amount=p.amount * 2) for p in entry.postings)
    record = replace(r, entries=(replace(entry, postings=doubled), *r.entries[1:]))
    store.rows[index] = replace(store.rows[index], record=record)
    store.snapshots = store.snapshots[:1]
    with pytest.raises(JournalIntegrityError, match="ledger differs from snapshot"):
        asyncio.run(rebuild(store))


def test_tampering_after_the_snapshot_fails_start_before_it_fails_the_audit() -> None:
    store = STORE.copy()
    store.snapshots = store.snapshots[:1]
    cut = store.snapshots[0].seq
    for resumed_catches in (True, False):
        forged = store.copy()
        i = with_events(forged.rows, cut, after=resumed_catches)
        forged.rows[i] = replace(forged.rows[i], record=replace(forged.rows[i].record, events="[]"))
        rechain(forged.rows, i)
        if resumed_catches:
            with pytest.raises(JournalIntegrityError, match="replay differs"):
                asyncio.run(rebuild(forged))
        else:
            # A resumed start trusts the records before its snapshot (the
            # anchors pin them; see test_a_rewritten_prefix_fails_its_anchor)...
            assert without_head(observe(asyncio.run(rebuild(forged)))) == without_head(LIVE)
        with pytest.raises(JournalIntegrityError, match="replay differs"):
            asyncio.run(rebuild(forged, full=True))  # ...the full audit replays them


def test_engine_restore_needs_a_fresh_engine_with_the_same_symbols() -> None:
    d = opened_driver()
    state = d.machine.supervisor.engine.state()
    with pytest.raises(RuntimeError):
        d.machine.supervisor.engine.restore(state)
    fresh = Driver().machine.supervisor.engine
    bad = replace(state, spreads=state.spreads[1:])
    with pytest.raises(ValueError):
        fresh.restore(bad)


# --- external anchors ---------------------------------------------------------------

FP = "a" * 64


def test_file_anchor_store_round_trip(tmp_path: Path) -> None:
    store = FileAnchorStore(tmp_path / "anchors.jsonl")

    async def scenario() -> list[Anchor]:
        await store.publish(Anchor(FP, 5, "b" * 64))
        await store.publish(Anchor("c" * 64, 1, "d" * 64))  # another journal
        await store.publish(Anchor(FP, 2, "e" * 64))
        await store.publish(Anchor(FP, 5, "b" * 64))  # repeat: harmless
        return await store.anchors(FP)

    assert asyncio.run(scenario()) == [Anchor(FP, 2, "e" * 64), Anchor(FP, 5, "b" * 64)]


@pytest.mark.parametrize(
    "lines",
    [
        ["not json"],
        ['{"journal":"' + FP + '","seq":0,"hash":"' + "b" * 64 + '"}'],
        ['{"journal":"' + FP + '","seq":1,"hash":"' + "b" * 64 + '","x":1}'],
        [
            '{"hash":"' + "b" * 64 + '","journal":"' + FP + '","seq":1}',
            '{"hash":"' + "c" * 64 + '","journal":"' + FP + '","seq":1}',
        ],
    ],
)
def test_invalid_anchor_files_are_refused(tmp_path: Path, lines: list[str]) -> None:
    path = tmp_path / "anchors.jsonl"
    path.write_text("\n".join(lines) + "\n", "ascii")
    with pytest.raises(AnchorError):
        asyncio.run(FileAnchorStore(path).anchors(FP))


def anchored_run(path: Path) -> tuple[MemoryJournal, list[Anchor]]:
    async def scenario() -> tuple[MemoryJournal, list[Anchor]]:
        store, stream = MemoryJournal(), MemoryStream()
        stream.publish(ITEMS)
        anchors = FileAnchorStore(path)
        runner = await start(store, stream.reader(), anchors=anchors)
        await runner.drain()
        return store, await anchors.anchors(runner.machine.fingerprint)

    return asyncio.run(scenario())


def test_every_checkpoint_is_anchored(tmp_path: Path) -> None:
    store, anchors = anchored_run(tmp_path / "a.jsonl")
    assert store.rows == STORE.rows
    hashes = {r.record.seq: r.record.hash for r in store.rows}
    assert [a.seq for a in anchors] == [s.seq for s in store.snapshots]
    assert all(a.hash == hashes[a.seq] for a in anchors)


def test_a_rolled_back_journal_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    store, anchors = anchored_run(path)
    cut = anchors[-1].seq - 1
    store.rows = store.rows[:cut]
    store.snapshots = [s for s in store.snapshots if s.seq <= cut]
    with pytest.raises(JournalIntegrityError, match="rolled back"):
        asyncio.run(start(store, MemoryStream().reader(), anchors=FileAnchorStore(path)))


def test_a_replaced_journal_is_refused(tmp_path: Path) -> None:
    """A different but fully consistent journal (same setup) fails its anchors."""
    path = tmp_path / "a.jsonl"
    anchored_run(path)
    other, _, _, _ = asyncio.run(run_memory(session(7, seconds=120)))
    with pytest.raises(JournalIntegrityError, match="differs from its anchor"):
        asyncio.run(start(other, MemoryStream().reader(), anchors=FileAnchorStore(path)))


class FlakyAnchors(FileAnchorStore):
    def __init__(self, path: Path, failures: int) -> None:
        super().__init__(path)
        self.failures = failures

    async def publish(self, anchor: Anchor) -> None:
        if self.failures:
            self.failures -= 1
            raise OSError("anchor store unreachable")
        await super().publish(anchor)


def test_an_anchor_outage_does_not_stop_trading(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, stream = MemoryJournal(), MemoryStream()
        stream.publish(ITEMS)
        flaky = FlakyAnchors(tmp_path / "a.jsonl", failures=1_000)
        runner = await start(store, stream.reader(), anchors=flaky)
        await runner.drain()
        assert store.rows == STORE.rows  # trading went on
        assert isinstance(runner.anchor_error, OSError)
        assert await flaky.anchors(runner.machine.fingerprint) == []
        flaky.failures = 0  # the store is back: the next start catches up
        restarted = await start(store, stream.reader(), anchors=flaky)
        assert restarted.anchor_error is None
        (anchor,) = await flaky.anchors(runner.machine.fingerprint)
        assert anchor.seq == store.snapshots[-1].seq

    asyncio.run(scenario())


def test_a_rewritten_prefix_fails_its_anchor(tmp_path: Path) -> None:
    """What a resumed start does not replay, the anchors still pin."""
    path = tmp_path / "a.jsonl"
    store, anchors = anchored_run(path)
    i = with_events(store.rows, anchors[0].seq, after=False)
    store.rows[i] = replace(store.rows[i], record=replace(store.rows[i].record, events="[]"))
    rechain(store.rows, i)
    with pytest.raises(JournalIntegrityError, match="differs from its anchor"):
        asyncio.run(start(store, MemoryStream().reader(), anchors=FileAnchorStore(path)))


def test_anchor_values_are_validated() -> None:
    with pytest.raises(ValueError):
        Anchor("A" * 64, 1, "b" * 64)  # not lowercase hex
    with pytest.raises(ValueError):
        Anchor(FP, 0, "b" * 64)
    with pytest.raises(ValueError):
        PriceRef("X", 1, 0, "0" * 64)  # key ids start at 1
