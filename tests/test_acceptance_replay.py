"""Acceptance test 1: deterministic replay.

A journaled session is rebuilt from the journal alone into the identical
state: every event, every ledger entry, every balance, position, order,
quote, floor and status. Two runs of the same inputs write the identical
journal, bit for bit. Any change to the record — content, order, gaps,
prices, setup — is detected, including a forger who rewrites the hash
chain after the change.

PostgreSQL and Valkey versions of these checks: tests/integration.
"""

import asyncio
import json
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.journal.machine import (
    CODEC,
    JournalIntegrityError,
    PricesUnavailableError,
    record_hash,
)
from propfirm.journal.runner import rebuild, verify_chain
from propfirm.ledger import rebuild_balances
from tests.journal_rig import MemoryJournal, observe, rechain, records, run_memory, session

ITEMS = session(0)


def live() -> tuple[MemoryJournal, dict[str, object]]:
    store, _, runner, _ = asyncio.run(run_memory(ITEMS))
    return store, observe(runner.machine)


STORE, LIVE = live()


def rebuilt(store: MemoryJournal) -> dict[str, object]:
    """Full replay from record 1 (the audit path; snapshots ignored)."""
    return observe(asyncio.run(rebuild(store, full=True)))


def copy() -> MemoryJournal:
    return STORE.copy()


def tampered(edit: str) -> MemoryJournal:
    """A copy of the journal with one change."""
    store = copy()
    edit_journal(store, edit)
    return store


def index_of(kind: str, contains: str = "") -> int:
    return next(
        i for i, r in enumerate(STORE.rows) if r.record.kind == kind and contains in r.record.events
    )


def edit_journal(store: MemoryJournal, edit: str) -> None:
    rows = store.rows
    opened = index_of("PlaceOrder", "PositionOpened")
    record = rows[opened].record
    if edit == "events":
        rows[opened] = replace(rows[opened], record=replace(record, events="[]"))
    elif edit == "events, chain rewritten":
        rows[opened] = replace(rows[opened], record=replace(record, events="[]"))
        rechain(rows, opened)
    elif edit == "events, rehashed, next link moved":
        forged = replace(record, events="[]", hash="")
        forged = replace(forged, hash=record_hash(forged))
        rows[opened] = replace(rows[opened], record=forged)
        nxt = rows[opened + 1]
        rows[opened + 1] = replace(nxt, record=replace(nxt.record, prev_hash=forged.hash))
    elif edit == "input, chain rewritten":
        order = json.loads(record.input)
        order["request"]["volume"] += 1
        text = json.dumps(order, sort_keys=True, separators=(",", ":"))
        rows[opened] = replace(rows[opened], record=replace(record, input=text))
        rechain(rows, opened)
    elif edit in ("ledger entry", "ledger entry, chain rewritten"):
        closed = index_of("ClosePosition", "PositionClosed")
        r = rows[closed].record
        entry = r.entries[0]
        moved = tuple(replace(p, amount=p.amount * 2) for p in entry.postings)
        rows[closed] = replace(
            rows[closed], record=replace(r, entries=(replace(entry, postings=moved),))
        )
        if edit.endswith("rewritten"):
            rechain(rows, closed)
    elif edit == "engine time":
        rows[opened] = replace(rows[opened], record=replace(record, ts=record.ts + 1))
    elif edit == "record dropped":
        del rows[opened]
    elif edit == "records swapped":
        rows[opened], rows[opened + 1] = rows[opened + 1], rows[opened]
    elif edit == "records swapped, chain rewritten":
        a, b = rows[opened].record, rows[opened + 1].record
        rows[opened] = replace(rows[opened + 1], record=replace(b, seq=a.seq))
        rows[opened + 1] = replace(rows[opened], record=replace(a, seq=b.seq))
        rechain(rows, opened)
    elif edit == "price changed":
        p = next(i for i, r in enumerate(rows) if r.price is not None)
        price = rows[p].price
        assert price is not None
        rows[p] = replace(rows[p], price=replace(price, price=price.price + 1))
    elif edit == "setup changed":
        setup = json.loads(store.setup_text or "")
        setup["guard"]["delay_threshold_ms"] += 1
        store.setup_text = json.dumps(setup, sort_keys=True, separators=(",", ":"))
    else:
        raise AssertionError(edit)


def test_rebuild_from_the_journal_gives_the_identical_state() -> None:
    assert rebuilt(STORE) == LIVE
    entries = LIVE["entries"]
    assert isinstance(entries, tuple)
    assert rebuild_balances(entries) == LIVE["balances"]


def test_the_session_covers_the_decisions_that_matter() -> None:
    text = "".join(r.record.events for r in STORE.rows)
    for needle in (
        "AccountBreached",
        "DayStarted",
        "PositionOpened",
        '"reason":{"_t":"CloseReason","v":"breach"}',
        '"reason":{"_t":"CloseReason","v":"stop_loss"}',
        '"v":"news_window"',
        "StaleUpdate",
        "DuplicateUpdate",
        '"v":"overdue"',
        '"v":"outage"',
        '"v":"malformed"',
        '"v":"unknown_symbol"',
        '"v":"duplicate_account"',
    ):
        assert needle in text, needle
    assert sum(len(r.record.entries) for r in STORE.rows) > 50


def test_same_inputs_write_the_identical_journal() -> None:
    again, _ = live()
    assert again.setup_text == STORE.setup_text
    assert again.rows == STORE.rows


@settings(max_examples=8, deadline=None)
@given(st.integers(min_value=1, max_value=2**32))
def test_replay_is_identical_for_any_session(seed: int) -> None:
    store, _, runner, _ = asyncio.run(run_memory(session(seed, seconds=90)))
    assert rebuilt(store) == observe(runner.machine)


HASH = "hash does not match its content"
REPLAY = "replay differs"


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        ("events", HASH),
        ("events, rehashed, next link moved", REPLAY),
        ("events, chain rewritten", REPLAY + " in events"),
        ("input, chain rewritten", REPLAY + " in events"),
        ("ledger entry", HASH),
        ("ledger entry, chain rewritten", REPLAY + " in entries"),
        ("engine time", HASH),
        ("record dropped", "expected record"),
        ("records swapped", "expected record"),
        # whichever comes first: the earlier input's result, or the id order
        ("records swapped, chain rewritten", "replay differs|source id not increasing"),
        ("price changed", "price store differs"),
        ("setup changed", "record 1: chain broken"),
    ],
)
def test_replay_detects_any_change_to_the_record(edit: str, match: str) -> None:
    with pytest.raises(JournalIntegrityError, match=match):
        rebuilt(tampered(edit))


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        ("events", HASH),
        ("events, rehashed, next link moved", HASH),  # the next record's hash covers the link
        ("ledger entry", HASH),
        ("engine time", HASH),
        ("record dropped", "expected record"),
        ("price changed", "price store differs"),
        ("setup changed", "record 1: chain broken"),
    ],
)
def test_chain_check_detects_changes_without_replaying(edit: str, match: str) -> None:
    with pytest.raises(JournalIntegrityError, match=match):
        asyncio.run(verify_chain(tampered(edit)))


def test_purged_prices_stop_replay_but_not_the_chain_check() -> None:
    store = copy()
    store.purge_licensed()
    with pytest.raises(PricesUnavailableError):
        rebuilt(store)
    assert asyncio.run(verify_chain(store)) == (LIVE["seq"], LIVE["head"])
    forged = tampered("ledger entry")
    forged.purge_licensed()
    with pytest.raises(JournalIntegrityError, match=HASH):
        asyncio.run(verify_chain(forged))


def test_no_price_is_in_the_journal_itself() -> None:
    prices = {r.price.price for r in STORE.rows if r.price is not None}
    for stored in STORE.rows:
        if stored.price is not None:
            assert f'"price":{stored.price.price}' not in stored.record.input
    assert len(prices) > 100
    assert all('"_t":"PriceRef"' in r.record.input for r in STORE.rows if r.price is not None)


def test_records_are_numbered_without_gaps() -> None:
    assert [r.seq for r in records(STORE.rows)] == list(range(1, len(ITEMS) + 1))
    assert CODEC.decode(STORE.setup_text or "") is not None
