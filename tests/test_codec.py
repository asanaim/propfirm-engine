"""Canonical JSON: one encoding per value, strict decoding."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from propfirm.journal.codec import Codec, CodecError, digest
from propfirm.journal.inputs import (
    InputRejected,
    InputRejectReason,
    Malformed,
    PlaceOrder,
    SetTrailingStop,
)
from propfirm.journal.machine import CODEC, PriceRef, price_ref
from propfirm.ledger import Entry, Posting
from propfirm.market.guards import FreezeReason, GuardConfig
from propfirm.market.types import PriceUpdate
from propfirm.rules.events import (
    AccountBreached,
    BreachReason,
    DayStarted,
    NewsExitViolation,
    StagePassed,
)
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    DuplicateUpdate,
    FeedGap,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
    StaleUpdate,
    StopsModified,
    SymbolFrozen,
    SymbolResumed,
    TrailingLevelMoved,
    TrailingStopSet,
)
from propfirm.trading.orders import OrderRequest, OrderType, Side, Trail
from tests.journal_rig import engine_setup

U = PriceUpdate("EUR/USD", 7, 1_790_000_000_000, 1_790_000_000_150, 110_123)
U_TEXT = (
    '{"_t":"PriceUpdate","price":110123,"receive_ts":1790000000150,"seq":7,'
    '"source_ts":1790000000000,"symbol":"EUR/USD"}'
)

SAMPLES: list[object] = [
    U,
    PlaceOrder(OrderRequest("o1", "A", "EUR/USD", Side.BUY, OrderType.LIMIT, 100, 109_000), 5),
    PlaceOrder(
        OrderRequest(
            "o2",
            "A",
            "EUR/USD",
            Side.SELL,
            OrderType.TRAILING,
            100,
            stop_loss=111_000,
            trail=Trail(50, 5),
            trailing_stop=Trail(80, 1),
        ),
        6,
    ),
    SetTrailingStop("A", "o1", Trail(30, 2), 7),
    SetTrailingStop("A", "o1", None, 8),
    Malformed('{"x":"\\u00e9"}', "bad"),
    InputRejected(3, "OpenAccount", "A", InputRejectReason.DUPLICATE_ACCOUNT),
    AccountOpened(1, "A", 500_000, "v"),
    OrderRejected(1, "o1", "A", RejectReason.NEWS_WINDOW, "v"),
    OrderPlaced(1, "o1", "A", "EUR/USD", Side.SELL, OrderType.STOP, 10, 109_000),
    OrderPlaced(1, "o2", "A", "EUR/USD", Side.BUY, OrderType.TRAILING, 10, 110_055, Trail(50, 1)),
    OrderCancelled(1, "o1", "A", None),
    OrderCancelled(1, "o1", "A", RejectReason.ACCOUNT_LOCKED),
    PositionOpened(1, "o1", "A", "EUR/USD", Side.BUY, 10, OrderType.MARKET, 1, 2, 3, 4, 5, "v"),
    PositionClosed(
        1, "o1", "o1#1", "A", "X", Side.BUY, 1, 2, 3, 4, 5, 6, -7, 0, CloseReason.BREACH, "v"
    ),
    StopsModified(1, "o1", "A", None, 120_000),
    TrailingStopSet(1, "o1", "A", Trail(100, 1), 109_895, 3),
    TrailingStopSet(1, "o1", "A", None, None, 4),
    TrailingLevelMoved(1, "o1", "A", 109_900, 5),
    SymbolFrozen(1, "EUR/USD", FreezeReason.OUTAGE),
    SymbolResumed(1, "EUR/USD"),
    FeedGap(1, "EUR/USD", 3, 4),
    DuplicateUpdate(1, "EUR/USD", 3),
    StaleUpdate(1, "EUR/USD", 3, 2_500),
    DayStarted(1, "A", 2, 3, 4, "lite-s1-v1"),
    AccountBreached(
        1, "A", (BreachReason.DAILY_LOSS, BreachReason.MAX_LOSS), -1, 2, 3, "lite-s1-v1"
    ),
    StagePassed(1, "A", 2, 4, "lite-s1-v1"),
    NewsExitViolation(1, "A", "o1/c1", "EUR/USD", CloseReason.TAKE_PROFIT, 700, 700, "lite-s1-v1"),
    Entry("e1", 1, "trade_pnl", (Posting("a", 5), Posting("b", -5)), "o1"),
    price_ref(U),
    GuardConfig(gap_threshold_ms={"XAU/USD": 9_000, "EUR/USD": 7_000}),
    engine_setup(),
    (1, "a", None, True, False, Fraction(-3, 7), frozenset({"b", "a"}), ()),
    {"k": (1, 2), "j": None},
]


@pytest.mark.parametrize("value", SAMPLES, ids=lambda v: type(v).__name__)
def test_round_trip_is_exact(value: object) -> None:
    text = CODEC.encode(value)
    assert CODEC.decode(text) == value
    assert text.isascii()


def test_layout_is_compact_and_sorted() -> None:
    assert CODEC.encode((1, "a", None)) == '[1,"a",null]'
    assert CODEC.encode({"b": 1, "a": 2}) == '{"_t":"dict","v":{"a":2,"b":1}}'
    assert CODEC.encode(frozenset({2, 10})) == '{"_t":"frozenset","v":[10,2]}'  # by encoding
    assert CODEC.encode("é") == '"\\u00e9"'


def test_pinned_encoding_and_digest() -> None:
    """Golden values: any change to the format breaks every stored journal."""
    assert CODEC.encode(U) == U_TEXT
    assert digest(U_TEXT) == "f621370a6c9cc7effca397e9558c052ef540717c909729bf49f48b3550f85bfc"
    assert price_ref(U) == PriceRef("EUR/USD", 7, digest(U_TEXT))


def test_64_bit_limits_are_inclusive() -> None:
    for value in (2**63 - 1, -(2**63)):
        assert CODEC.decode(CODEC.encode(value)) == value


def test_bool_and_int_stay_distinct() -> None:
    assert CODEC.decode(CODEC.encode((True, 1))) == (True, 1)
    assert type(CODEC.decode(CODEC.encode(True))) is bool
    assert type(CODEC.decode(CODEC.encode(1))) is int


INTS = st.integers(min_value=-(2**63), max_value=2**63 - 1)
TEXT = st.text(st.characters(exclude_characters="\x00", exclude_categories=["Cs"]))
FRACTIONS = st.fractions(min_value=-(10**12), max_value=10**12, max_denominator=10**6)


@given(
    st.recursive(
        st.none() | st.booleans() | INTS | TEXT | FRACTIONS,
        lambda inner: st.tuples(inner, inner) | st.frozensets(INTS | TEXT),
        max_leaves=20,
    )
)
def test_any_supported_value_round_trips_canonically(value: object) -> None:
    text = CODEC.encode(value)
    assert CODEC.decode(text) == value
    assert CODEC.encode(CODEC.decode(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "1.0",  # float
        "1e3",
        "NaN",
        "Infinity",
        '{"_t":"Fraction","n":1,"n":2,"d":3}',  # duplicate key
        '{"a":1}',  # object without a tag
        '{"_t":"Nope","v":1}',  # unknown tag
        '{"_t":"Side","v":"hold"}',  # not a member
        '{"_t":"SymbolResumed","symbol":"EUR/USD"}',  # missing field
        '{"_t":"SymbolResumed","symbol":"EUR/USD","ts":1,"x":2}',  # extra field
        '{"_t":"Advance","ts":true}',  # bool for int, refused by the input's validation
        '{"_t":"PriceUpdate","price":0,"receive_ts":1,"seq":1,"source_ts":1,"symbol":"X"}',
        '{"_t":"Fraction","d":4,"n":2}',  # not reduced
        '{"_t":"Fraction","d":0,"n":2}',
        '{"_t":"Fraction","d":true,"n":2}',
        '{"_t":"frozenset","v":["b","a"]}',  # not sorted
        '{"_t":"frozenset","v":["a","a"]}',  # repeated
        '{"_t":"frozenset","v":[{"_t":"dict","v":{}}]}',  # unhashable item
        '{"_t": "Side","v":"buy"}',  # whitespace
        '{"v":"buy","_t":"Side"}',  # keys not sorted
        '"é"',  # non-ASCII where an escape is canonical
        "[1,]",
        "",
        "9223372036854775808",  # 2**63: beyond BIGINT
        "-9223372036854775809",
        '{"_t":"Fraction","d":1,"n":9223372036854775808}',
        '"a\\u0000b"',  # NUL
        '"\\ud800"',  # lone surrogate
        '{"_t":"dict","v":{"\\u0000":1}}',
    ],
)
def test_strict_decoding(text: str) -> None:
    with pytest.raises(CodecError):
        CODEC.decode(text)


@pytest.mark.parametrize(
    "value",
    [1.5, [1, 2], {1: "a"}, object(), b"bytes", {"k": 1.0}, 2**63, "\x00", "\udc80"],
    ids=[
        "float",
        "list",
        "int-key",
        "object",
        "bytes",
        "nested-float",
        "2**63",
        "nul",
        "surrogate",
    ],
)
def test_unsupported_values_are_not_encoded(value: object) -> None:
    with pytest.raises(CodecError):
        CODEC.encode(value)


def test_unregistered_types_are_refused() -> None:
    small = Codec([Side])
    with pytest.raises(CodecError):
        small.encode(OrderType.MARKET)
    with pytest.raises(CodecError):
        small.encode(U)
    with pytest.raises(CodecError):
        small.decode(U_TEXT)


def test_registration_rules() -> None:
    with pytest.raises(ValueError):
        Codec([Side, Side])
    with pytest.raises(ValueError):
        Codec([type("Fraction", (), {})])  # reserved tag
    with pytest.raises(TypeError):
        Codec([int])
