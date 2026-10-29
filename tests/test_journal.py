"""Inputs, message decoding, records and runner failure rules."""

import asyncio
from dataclasses import replace

import pytest

from propfirm.journal.inputs import (
    Advance,
    ClosePosition,
    Input,
    InputRejected,
    InputRejectReason,
    Malformed,
    ModifyStops,
    OpenAccount,
    SetTrailingStop,
    apply,
)
from propfirm.journal.machine import CODEC, Machine, PriceRef, price_ref, source_key
from propfirm.journal.runner import Runner, RunnerStoppedError, rebuild
from propfirm.journal.setup import EngineSetup, SetupError, build
from propfirm.journal.stream import decode_input, encode_input
from propfirm.market.types import PriceUpdate
from tests.journal_rig import (
    T0,
    TEST_KEY,
    Crash,
    Fault,
    FaultyStore,
    MemoryJournal,
    MemoryStream,
    engine_setup,
    start,
)

U = PriceUpdate("EUR/USD", 1, T0, T0, 110_000)


def machine() -> Machine:
    m = Machine(CODEC.encode(engine_setup()), {1: TEST_KEY})
    m.use_price_key(1)
    return m


# --- refused inputs -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "ref", "reason"),
    [
        (OpenAccount("A", "lite-s1-v1", 500_000, T0 + 9), "A", InputRejectReason.DUPLICATE_ACCOUNT),
        (OpenAccount("B", "nope-v1", 500_000, T0 + 9), "B", InputRejectReason.UNKNOWN_RULE_SET),
        (OpenAccount("B", "classic-s1-v1", 500_000, T0 + 9), "B", InputRejectReason.INVALID_SIZE),
        (PriceUpdate("GBP/USD", 1, T0 + 9, T0 + 9, 1), "GBP/USD", InputRejectReason.UNKNOWN_SYMBOL),
        (Malformed("{}", "empty"), "", InputRejectReason.MALFORMED),
    ],
)
def test_refused_inputs_leave_state_unchanged(
    item: Input, ref: str, reason: InputRejectReason
) -> None:
    setup = engine_setup()
    sup, ledger = build(setup)
    rules = setup.rule_set_map()
    apply(sup, rules, OpenAccount("A", "lite-s1-v1", 500_000, T0))
    apply(sup, rules, U)

    def state() -> object:
        engine = sup.engine
        return engine.now, engine.equity("A"), ledger.entries(), engine.quote("EUR/USD")

    before = state()
    assert apply(sup, rules, item) == [InputRejected(T0, type(item).__name__, ref, reason)]
    assert state() == before  # engine time did not move to the input's T0 + 9
    assert not sup.has_account("B")


@pytest.mark.parametrize(
    "build_bad",
    [
        lambda: OpenAccount("A", "lite-s1-v1", 0, T0),
        lambda: OpenAccount(" A", "lite-s1-v1", 500_000, T0),
        lambda: Advance(-1),
        lambda: ModifyStops("A", "p", 0, None, T0),
        lambda: ClosePosition("A", "p", T0, True),
        lambda: SetTrailingStop("A", "p", 50, T0),  # type: ignore[arg-type]
        lambda: SetTrailingStop("A", " p", None, T0),
    ],
)
def test_inputs_validate_themselves(build_bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_bad()  # type: ignore[operator]


# --- stream messages ----------------------------------------------------------------


def test_input_round_trips_through_message_fields() -> None:
    item = OpenAccount("A", "lite-s1-v1", 500_000, T0)
    fields = encode_input(item)
    assert decode_input(fields) == item
    assert decode_input({k.encode(): v.encode() for k, v in fields.items()}) == item


@pytest.mark.parametrize(
    "fields",
    [
        None,
        {},
        {"input": "{not json"},
        {"input": CODEC.encode(U), "extra": "1"},
        {"symbol": "EUR/USD"},
        {"input": CODEC.encode(price_ref(U, 1, TEST_KEY))},  # a journal reference is not an input
        {"input": CODEC.encode(Malformed("{}", "x"))},  # nor is Malformed
        {"input": '{"_t":"Advance","ts":-5}'},  # fails validation
        {b"input": b"\xff\xfe"},  # not UTF-8
        {"input": '{"_t":"CancelOrder","account_id":"A\\u0000","order_id":"o","ts":1}'},  # NUL
        {"input": '{"_t":"Advance","ts":9223372036854775808}'},  # beyond BIGINT
    ],
)
def test_bad_messages_become_malformed_inputs(fields: dict[object, object] | None) -> None:
    item = decode_input(fields)
    assert isinstance(item, Malformed)
    assert item.error
    if fields:
        assert item.raw != "{}"  # what arrived is kept


def test_non_utf8_bytes_are_kept_escaped() -> None:
    item = decode_input({b"input": b"\xff"})
    assert isinstance(item, Malformed)
    assert item.raw == '{"input":"\\\\xff"}'


def test_malformed_and_references_are_never_sent() -> None:
    with pytest.raises(TypeError):
        encode_input(Malformed("{}", "x"))
    with pytest.raises(TypeError):
        encode_input(PriceRef("EUR/USD", 1, 1, "0" * 64))  # type: ignore[arg-type]


# --- records ------------------------------------------------------------------------


def test_source_ids_order_numerically() -> None:
    assert source_key("10-0") > source_key("9-5") > source_key("9-4")
    for bad in ("10", "1-a", "01-0", "-1-0", "1-0 ", ""):
        with pytest.raises(ValueError):
            source_key(bad)


def test_machine_refuses_a_source_id_that_does_not_increase() -> None:
    m = machine()
    m.apply("5-0", U)
    for repeat in ("5-0", "4-9"):
        with pytest.raises(ValueError):
            m.apply(repeat, replace(U, seq=2))
    assert m.seq == 1


def test_price_is_journaled_by_reference_only() -> None:
    stored = machine().apply("1-0", U)
    assert CODEC.decode(stored.record.input) == price_ref(U, 1, TEST_KEY)
    assert str(U.price) not in stored.record.input
    assert stored.price == U


def test_first_record_links_to_the_setup_fingerprint() -> None:
    m = machine()
    head = m.head
    assert m.apply("1-0", U).record.prev_hash == head
    assert m.head != head


# --- runner -------------------------------------------------------------------------


def test_setup_must_match_the_journal() -> None:
    async def scenario() -> None:
        store = MemoryJournal()
        await start(store, MemoryStream().reader())
        other = replace(engine_setup(), config_version="something-else")
        with pytest.raises(SetupError):
            await Runner.start(store, MemoryStream().reader(), other)

    asyncio.run(scenario())


def test_other_tzdata_release_is_refused() -> None:
    setup: EngineSetup = replace(engine_setup(), tzdata_version="1999.1")
    with pytest.raises(SetupError):
        build(setup)


def test_rebuild_needs_a_setup() -> None:
    with pytest.raises(SetupError):
        asyncio.run(rebuild(MemoryJournal()))


def test_runner_stops_after_a_failure() -> None:
    async def scenario() -> None:
        store, stream = MemoryJournal(), MemoryStream()
        stream.publish([U, replace(U, seq=2)])
        faulty = FaultyStore(store, Fault.BEFORE_COMMIT, 1)
        runner = await start(faulty, stream.reader())
        with pytest.raises(Crash):
            await runner.poll(1)
        with pytest.raises(RunnerStoppedError):
            await runner.poll(1)
        assert store.rows == []
        restarted = await start(store, stream.reader())
        assert await restarted.drain() == 2
        assert [r.record.source_id for r in store.rows] == ["1-0", "2-0"]
        assert restarted.machine.seq == 2

    asyncio.run(scenario())
