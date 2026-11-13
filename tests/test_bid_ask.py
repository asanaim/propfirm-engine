"""The source's bid and ask in the price type (build plan step 1, line 2;
decision D-13.10).

A price update may carry the source's own bid and ask. Older records have
neither and keep their exact encoding, digest and replay. When present, the
engine's quote covers them: it is never tighter than the real market.
"""

from dataclasses import replace

import pytest

from propfirm.journal.machine import CODEC, price_ref
from propfirm.market.spread import MarketSpreadConfig, SpreadModel
from propfirm.market.stream import MalformedMessageError, decode, encode
from propfirm.market.types import PriceUpdate
from propfirm.trading.events import PositionOpened
from propfirm.trading.orders import Side
from tests.engine_rig import T0, Rig

PLAIN = PriceUpdate("EUR/USD", 7, T0, T0 + 5, 110_000)
QUOTED = replace(PLAIN, bid=109_990, ask=110_020)
KEY = bytes(range(32))


# --- the type ---------------------------------------------------------------------


def test_bid_and_ask_come_together_and_in_order() -> None:
    with pytest.raises(ValueError, match="together"):
        replace(PLAIN, bid=109_990)
    with pytest.raises(ValueError, match="together"):
        replace(PLAIN, ask=110_020)
    with pytest.raises(ValueError, match="exceed"):
        replace(PLAIN, bid=110_021, ask=110_020)
    with pytest.raises(ValueError):
        replace(PLAIN, bid=0, ask=1)
    assert replace(PLAIN, bid=110_000, ask=110_000).bid == 110_000  # a zero spread is a quote


def test_an_older_price_keeps_its_encoding_and_digest() -> None:
    text = CODEC.encode(PLAIN)
    assert "bid" not in text and "ask" not in text  # as written before 13 Nov
    assert CODEC.decode(text) == PLAIN
    assert price_ref(PLAIN, 1, KEY) == price_ref(PriceUpdate(*_fields(PLAIN)), 1, KEY)


def _fields(u: PriceUpdate) -> tuple[str, int, int, int, int]:
    return (u.symbol, u.seq, u.source_ts, u.receive_ts, u.price)


def test_a_quoted_price_round_trips_and_digests_differently() -> None:
    assert CODEC.decode(CODEC.encode(QUOTED)) == QUOTED
    assert price_ref(QUOTED, 1, KEY) != price_ref(PLAIN, 1, KEY)  # bid and ask are signed too


# --- the price stream ----------------------------------------------------------------


def test_the_price_stream_carries_bid_and_ask_when_present() -> None:
    assert set(encode(PLAIN)) == {"symbol", "seq", "source_ts", "receive_ts", "price"}
    assert decode(encode(PLAIN)) == PLAIN
    assert decode(encode(QUOTED)) == QUOTED
    half = {**encode(PLAIN), "bid": "1"}
    with pytest.raises(MalformedMessageError):
        decode(half)


# --- the quote -------------------------------------------------------------------------


def test_the_quote_covers_a_wider_source_spread() -> None:
    rig = Rig()  # fixed 10-point model spread
    rig.engine.on_price(replace(QUOTED, seq=1))
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None
    assert (quote.bid, quote.ask, quote.mid) == (109_990, 110_020, 110_000)


def test_a_tighter_source_spread_changes_nothing() -> None:
    rig = Rig()
    rig.engine.on_price(replace(PLAIN, seq=1, bid=109_998, ask=110_002))
    tight = rig.engine.quote("EUR/USD")
    plain = Rig()
    plain.engine.on_price(replace(PLAIN, seq=1))
    assert tight == plain.engine.quote("EUR/USD")  # the firm's 10 points stand
    assert tight is not None and (tight.bid, tight.ask) == (109_995, 110_005)


def test_each_side_is_covered_on_its_own() -> None:
    rig = Rig()
    rig.engine.on_price(replace(PLAIN, seq=1, bid=109_998, ask=110_030))  # skewed up
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None and (quote.bid, quote.ask) == (109_995, 110_030)


def test_orders_fill_at_the_covered_ask() -> None:
    rig = Rig()
    rig.engine.on_price(replace(QUOTED, seq=1))
    events = rig.order("o1", Side.BUY)
    opened = [e for e in events if isinstance(e, PositionOpened)]
    assert opened and opened[0].price == 110_020


def test_the_market_model_also_covers_the_source() -> None:
    model = SpreadModel(MarketSpreadConfig(min_points=4, max_points=40))
    model.observe(110_000)
    quote = model.quote("EUR/USD", 110_000, 1, T0, T0, False, T0, 109_980, 110_016)
    assert (quote.bid, quote.ask) == (109_980, 110_016)
    plain = model.quote("EUR/USD", 110_000, 1, T0, T0, False, T0)
    assert (plain.bid, plain.ask) == (109_998, 110_002)
