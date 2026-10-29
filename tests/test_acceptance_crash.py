"""Acceptance test 4: crash and restart.

The engine dies at every kind of point: after reading (messages pending,
nothing applied), before the commit, after the commit but before the
acknowledgement, and around the acknowledgement; any number of times, with
any batch size. After restarting, the journal, ledger and state equal an
uninterrupted run exactly: no input lost, none applied twice, nothing left
pending.

The same against Valkey and PostgreSQL, with real process kills:
tests/integration/test_journal_services.py.
"""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.journal.inputs import Malformed
from propfirm.journal.machine import CODEC
from propfirm.journal.runner import Runner
from tests.journal_rig import (
    CONSUMER,
    Crash,
    Fault,
    FaultySource,
    FaultyStore,
    Item,
    MemoryJournal,
    MemoryStream,
    observe,
    run_memory,
    session,
    start,
)

ITEMS = session(1, seconds=30)
REFERENCE, _stream, REFERENCE_RUNNER, _crashes = asyncio.run(run_memory(ITEMS))
REFERENCE_STATE = observe(REFERENCE_RUNNER.machine)


def check(store: MemoryJournal, stream: MemoryStream, runner: Runner) -> None:
    assert store.rows == REFERENCE.rows
    assert observe(runner.machine) == REFERENCE_STATE
    journaled = [r.record.source_id for r in store.rows]
    assert journaled == [entry_id for entry_id, _ in stream.entries]  # each once, in order
    assert stream.pending[CONSUMER] == {}


@pytest.mark.parametrize("at", [1, 2, 5])
@pytest.mark.parametrize("fault", list(Fault))
def test_each_crash_point(fault: Fault, at: int) -> None:
    store, stream, runner, crashes = asyncio.run(run_memory(ITEMS, [(fault, at)], batch=40))
    assert crashes == 1
    check(store, stream, runner)


@settings(max_examples=25, deadline=None)
@given(
    faults=st.lists(
        st.tuples(st.sampled_from(list(Fault)), st.integers(min_value=1, max_value=12)),
        min_size=1,
        max_size=5,
    ),
    batch=st.integers(min_value=1, max_value=64),
)
def test_any_sequence_of_crashes(faults: list[tuple[Fault, int]], batch: int) -> None:
    store, stream, runner, _ = asyncio.run(run_memory(ITEMS, faults, batch))
    check(store, stream, runner)


def test_repeated_crashes_at_the_same_message() -> None:
    faults = [(Fault.BEFORE_COMMIT, 1)] * 3 + [(Fault.AFTER_COMMIT, 1)] * 3
    store, stream, runner, crashes = asyncio.run(run_memory(ITEMS, faults, batch=1))
    assert crashes == 6
    check(store, stream, runner)


def test_an_entry_lost_from_the_stream_is_recorded_not_skipped() -> None:
    """A delivered entry whose data is deleted before it is processed (e.g.
    trimmed) is journaled as Malformed: the loss is on the record."""

    async def scenario() -> tuple[MemoryJournal, list[Item]]:
        store, stream = MemoryJournal(), MemoryStream()
        ids = stream.publish(ITEMS)
        crash = Fault.AFTER_READ
        first = await start(FaultyStore(store, crash, 1), FaultySource(stream.reader(), crash, 1))
        with pytest.raises(Crash):
            await first.drain(5)
        stream.delete(ids[2])
        final = await start(store, stream.reader())
        await final.drain(5)
        return store, ITEMS

    store, items = asyncio.run(scenario())
    assert len(store.rows) == len(items)
    lost = store.rows[2].record
    assert lost.kind == "Malformed"
    item = CODEC.decode(lost.input)
    assert isinstance(item, Malformed) and "empty entry" in item.error
    assert '"v":"malformed"' in lost.events
