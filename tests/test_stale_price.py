"""Stale price (D-14.36): the reference stream and the pure rules."""

import pytest

from propfirm.customers.stale_price import Fill, advantage, fills_from, stale
from propfirm.market.oanda import (
    INSTRUMENTS,
    Heartbeat,
    OandaError,
    Quote,
    instrument,
    parse,
    symbol,
    time_ms,
    to_points,
)
from propfirm.market.symbols import SYMBOLS

LINE = (
    '{"type":"PRICE","time":"2026-11-28T10:15:03.123456789Z","instrument":"EUR_USD",'
    '"tradeable":true,"bids":[{"price":"1.082151","liquidity":1000000}],'
    '"asks":[{"price":"1.082289","liquidity":1000000}]}'
)


def test_instruments() -> None:
    assert instrument("EUR/USD") == "EUR_USD" and instrument("WTI") == "WTICO_USD"
    assert symbol("BCO_USD") == "Brent" and symbol("SPX500_USD") is None
    assert "BTC/USD" not in INSTRUMENTS
    with pytest.raises(OandaError):
        instrument("BTC/USD")


def test_a_quote_widens_when_rounded() -> None:
    quote = parse(LINE)
    # bid 1.082151 down to 108215; ask 1.082289 up to 108229
    assert quote == Quote("EUR/USD", time_ms("2026-11-28T10:15:03.123Z"), 108_215, 108_229)
    assert to_points("1.08215", SYMBOLS["EUR/USD"], up=True) == 108_215  # exact stays


def test_times_and_other_lines() -> None:
    assert time_ms("1970-01-01T00:00:01.9999Z") == 1_999  # truncated
    assert time_ms("1970-01-01T00:00:01Z") == 1_000
    assert parse('{"type":"HEARTBEAT","time":"1970-01-01T00:00:02Z"}') == Heartbeat(2_000)
    assert parse("") is None
    assert parse(LINE.replace('"tradeable":true', '"tradeable":false')) is None
    assert parse(LINE.replace("EUR_USD", "SPX500_USD")) is None
    for bad in ("[]", "{", LINE.replace('"1.082289"', '"1.08"'), LINE.replace('Z"', '+01:00"')):
        with pytest.raises(OandaError):
            parse(bad)


def event(kind: str, **kw: object) -> dict[str, object]:
    return {
        "type": kind,
        "account_id": "ACC1",
        "position_id": "P1",
        "ts": 5,
        "symbol": "EUR/USD",
    } | kw


def test_only_fills_the_trader_timed() -> None:
    events = [
        event("PositionOpened", side="buy", order_type="market", price=100),
        event("PositionOpened", side="buy", order_type="limit", price=100),
        event("PositionClosed", side="buy", reason="client", close_price=120),
        event("PositionClosed", side="sell", reason="client", close_price=90),
        event("PositionClosed", side="buy", reason="stop_loss", close_price=80),
        {"type": "CommissionCharged", "account_id": "ACC1", "ts": 6},
    ]
    found = fills_from(events)
    assert [(f.kind, f.pays_ask, f.price) for f in found] == [
        ("open", True, 100),
        ("close", False, 120),
        ("close", True, 90),
    ]


def fill(pays_ask: bool, price: int) -> Fill:
    return Fill("ACC1", "P1", "open", 0, "EUR/USD", pays_ask, price)


def test_advantage_and_the_one_spread_line() -> None:
    bid, ask = 1_000, 1_010  # spread 10
    assert advantage(fill(True, 990), bid, ask) == 20  # bought 20 below the ask
    assert advantage(fill(False, 1_015), bid, ask) == 15  # sold 15 above the bid
    assert not stale(fill(True, 1_000), bid, ask, 1)  # exactly one spread better
    assert stale(fill(True, 999), bid, ask, 1)
    assert not stale(fill(False, 1_005), bid, ask, 1)  # worse than mid: normal
    assert not stale(fill(True, 999), bid, ask, 2)
