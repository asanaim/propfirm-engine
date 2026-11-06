"""Twelve Data stream parsing, exact points, the connector and the trial report."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from propfirm.journal.stream import decode_input
from propfirm.market.symbols import SYMBOLS
from propfirm.market.twelvedata import (
    Heartbeat,
    Other,
    Price,
    SubscribeStatus,
    SymbolMap,
    TwelveDataError,
    heartbeat,
    load_symbol_map,
    parse,
    places,
    subscribe,
    to_points,
)
from propfirm.market.types import PriceUpdate
from propfirm.service import trial
from propfirm.service.twelvedata import (
    Connector,
    TrialStats,
    _safe,
    connector_url,
    day_of,
    stats_key,
)

MAP = Path(__file__).resolve().parents[1] / "config" / "market" / "twelve_data.toml"
T = 1_762_400_000  # UNIX seconds, 2025-11-06 03:33:20 UTC
DAY = day_of(T * 1000)


# --- parsing -----------------------------------------------------------------------------


def test_a_price_is_read_as_a_decimal_never_a_float() -> None:
    text = (
        '{"event":"price","symbol":"EUR/USD","currency_base":"Euro","exchange":"PHYSICAL '
        f'CURRENCY","type":"Physical Currency","timestamp":{T},"price":1.08215,"bid":1.0821}}'
    )
    p = parse(text)
    assert p == Price("EUR/USD", T, Decimal("1.08215"))
    assert isinstance(p, Price) and isinstance(p.price, Decimal)
    assert parse(f'{{"event":"price","symbol":"BTC/USD","timestamp":{T},"price":97000}}') == (
        Price("BTC/USD", T, Decimal(97000))
    )


@pytest.mark.parametrize(
    "body",
    [
        '{"event":"price","timestamp":1,"price":1.0}',  # no symbol
        '{"event":"price","symbol":"X","timestamp":1.5,"price":1.0}',  # not whole seconds
        '{"event":"price","symbol":"X","timestamp":0,"price":1.0}',
        '{"event":"price","symbol":"X","timestamp":1,"price":"1.0"}',  # a string
        '{"event":"price","symbol":"X","timestamp":1,"price":true}',
        '{"event":"price","symbol":"X","timestamp":1,"price":-1.0}',
        '{"event":"price","symbol":"X","timestamp":1,"price":0}',
        "[1]",
        "not json",
    ],
)
def test_a_bad_message_is_refused(body: str) -> None:
    with pytest.raises(TwelveDataError):
        parse(body)


def test_status_heartbeat_and_other_events() -> None:
    status = parse(
        '{"event":"subscribe-status","status":"ok","success":[{"symbol":"EUR/USD",'
        '"exchange":"PHYSICAL CURRENCY"}],"fails":[{"symbol":"XBR/USD"}]}'
    )
    assert status == SubscribeStatus(True, ("EUR/USD",), ("XBR/USD",))
    assert parse('{"event":"subscribe-status","status":"error","fails":null}') == (
        SubscribeStatus(False, (), ())
    )
    assert parse('{"event":"heartbeat","status":"ok"}') == Heartbeat(True)
    other = parse('{"event":"reset-status","status":"ok"}')
    assert isinstance(other, Other) and other.event == "reset-status"


def test_messages_out() -> None:
    m = SymbolMap({"XAU/USD": "XAU/USD", "EUR/USD": "EUR/USD"})
    assert json.loads(subscribe(m)) == {
        "action": "subscribe",
        "params": {"symbols": "EUR/USD,XAU/USD"},
    }
    assert json.loads(heartbeat()) == {"action": "heartbeat"}


# --- exact points ----------------------------------------------------------------------------


def test_points_are_exact_and_extra_places_are_refused_not_rounded() -> None:
    eur, jpy, gold = SYMBOLS["EUR/USD"], SYMBOLS["USD/JPY"], SYMBOLS["XAU/USD"]
    assert to_points(Decimal("1.08215"), eur) == 108215
    assert to_points(Decimal("1.0821"), eur) == 108210
    assert to_points(Decimal("1.082150"), eur) == 108215  # a trailing zero is exact
    assert to_points(Decimal("1.082155"), eur) is None  # 6 places for a 5-digit symbol
    assert to_points(Decimal("151.123"), jpy) == 151123
    assert to_points(Decimal("2650.4"), gold) == 265040
    assert to_points(Decimal("2650.405"), gold) is None
    assert (places(Decimal("1.08200")), places(Decimal("97000")), places(Decimal("1E+2"))) == (
        5,
        0,
        0,
    )


# --- the symbol map --------------------------------------------------------------------------


def test_the_map_covers_the_30_launch_symbols_one_to_one() -> None:
    m = load_symbol_map(MAP)
    assert set(m.to_source) == set(SYMBOLS)
    assert len(m.from_source) == 30
    assert m.subset(("EUR/USD",)).to_source == {"EUR/USD": "EUR/USD"}
    with pytest.raises(TwelveDataError, match="no Twelve Data symbol"):
        SymbolMap({"EUR/USD": "EUR/USD"}).subset(("WTI",))
    with pytest.raises(TwelveDataError, match="not launch symbols"):
        SymbolMap({"DOGE/USD": "DOGE/USD"})
    with pytest.raises(TwelveDataError, match="two launch symbols"):
        SymbolMap({"WTI": "X", "Brent": "X"})
    with pytest.raises(TwelveDataError, match="bad Twelve Data symbol"):
        SymbolMap({"WTI": "A,B"})


def test_the_key_never_appears_in_an_error() -> None:
    url = connector_url("secret-key")
    assert url == "wss://ws.twelvedata.com/v1/quotes/price?apikey=secret-key"

    class Rejected(Exception):
        class response:
            status_code = 401

    exc = Rejected(f"server rejected {url}")
    assert _safe(exc) == "Rejected (HTTP 401)"
    assert _safe(OSError(f"cannot reach {url}")) == "OSError"


# --- the connector, against fakes --------------------------------------------------------


class FakePipe:
    def __init__(self, store: FakeValkey) -> None:
        self.store = store
        self.ops: list[tuple[Any, ...]] = []

    def hset(self, key: str, field: str, value: str) -> None:
        self.ops.append(("hset", key, field, value))

    def hincrby(self, key: str, field: str, n: int) -> None:
        self.ops.append(("hincrby", key, field, n))

    def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.ops.append(("xadd", stream, fields))

    async def execute(self) -> list[Any]:
        if self.store.fail_next:
            self.store.fail_next = False
            raise ConnectionError("down")
        for op in self.ops:
            match op:
                case ("hset", key, field, value):
                    self.store.hashes.setdefault(key, {})[field] = str(value)
                case ("hincrby", key, field, n):
                    h = self.store.hashes.setdefault(key, {})
                    h[field] = str(int(h.get(field, "0")) + n)
                case ("xadd", stream, fields):
                    self.store.streams.setdefault(stream, []).append(fields)
        return []


class FakeValkey:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.fail_next = False

    def pipeline(self, transaction: bool = True) -> FakePipe:
        assert transaction
        return FakePipe(self)

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return {k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()}

    async def hget(self, key: str, field: str) -> bytes | None:
        v = self.hashes.get(key, {}).get(field)
        return None if v is None else v.encode()

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    def updates(self, stream: str = "s") -> list[PriceUpdate]:
        out = []
        for fields in self.streams.get(stream, []):
            item = decode_input({k.encode(): v.encode() for k, v in fields.items()})
            assert isinstance(item, PriceUpdate)
            out.append(item)
        return out


def price(symbol: str, value: str, ts: int = T) -> str:
    return f'{{"event":"price","symbol":"{symbol}","timestamp":{ts},"price":{value}}}'


def connector(store: FakeValkey, clock_ms: int = T * 1000 + 400) -> Connector:
    m = SymbolMap({"EUR/USD": "EUR/USD", "Brent": "XBR/USD"})

    def no_socket() -> contextlib.AbstractAsyncContextManager[Any]:
        raise AssertionError("not used")

    return Connector(store, "s", m, no_socket, clock=lambda: clock_ms)  # type: ignore[arg-type]


def test_prices_become_updates_with_continuing_sequence_numbers() -> None:
    async def scenario() -> None:
        store = FakeValkey()
        store.hashes["s:seq:twelve_data"] = {"EUR/USD": "41"}  # before a restart
        c = connector(store)
        await c.load_seq()
        await c.handle(price("EUR/USD", "1.08215"))
        await c.handle(price("XBR/USD", "73.4"))
        await c.handle(price("EUR/USD", "1.082159"))  # too many places: refused
        await c.handle(price("GBP/USD", "1.3"))  # not subscribed here: ignored
        await c.handle("garbage")
        assert store.updates() == [
            PriceUpdate("EUR/USD", 42, T * 1000, T * 1000 + 400, 108215),
            PriceUpdate("Brent", 1, T * 1000, T * 1000 + 400, 7340),
        ]
        assert store.hashes["s:seq:twelve_data"] == {"EUR/USD": "42", "Brent": "1"}
        assert (c.published, c.refused) == (2, 1)

        # the transaction fails: neither the update nor its number is kept
        store.fail_next = True
        with pytest.raises(ConnectionError):
            await c.handle(price("EUR/USD", "1.08216"))
        await c.handle(price("EUR/USD", "1.08217"))
        assert store.updates()[-1].seq == 43

        await c.flush()
        day = store.hashes[stats_key("s", DAY)]
        assert (day["prices:EUR/USD"], day["refused:EUR/USD"], day["places:EUR/USD"]) == (
            "4",  # counted as received, the failed one too
            "1",
            "6",
        )
        assert day["unknown_symbol:GBP/USD"] == "1"
        assert day["unreadable"] == "1"
        assert day["delay_le1000:EUR/USD"] == "4"

    asyncio.run(scenario())


class FakeSocket:
    def __init__(self, messages: list[str], then: BaseException | None = None) -> None:
        self.sent: list[str] = []
        self._messages = list(messages)
        self._then = then
        self.closed = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        if self._then is not None:
            raise self._then
        await self.closed.wait()  # silent until the test stops
        raise ConnectionError("closed")


def test_a_session_subscribes_beats_and_reconnects_after_a_drop() -> None:
    async def scenario() -> None:
        store = FakeValkey()
        sockets = [
            FakeSocket(['{"event":"subscribe-status","status":"ok","success":[]}',
                        price("EUR/USD", "1.1")], then=ConnectionError("dropped")),
            FakeSocket([price("EUR/USD", "1.2")]),
        ]  # fmt: skip
        opened: list[FakeSocket] = []
        stop = asyncio.Event()

        @contextlib.asynccontextmanager
        async def connect() -> AsyncIterator[FakeSocket]:
            sock = sockets[len(opened)]
            opened.append(sock)
            yield sock

        beats = asyncio.Event()

        async def fast_sleep(seconds: float) -> None:
            beats.set()
            await asyncio.sleep(0)

        c = Connector(store, "s", SymbolMap({"EUR/USD": "EUR/USD"}), connect,  # type: ignore[arg-type]
                      clock=lambda: T * 1000, sleep=fast_sleep)  # fmt: skip
        task = asyncio.create_task(c.run(stop))
        for _ in range(500):
            await asyncio.sleep(0.01)
            if len(store.updates()) == 2:
                break
        stop.set()
        await asyncio.wait_for(task, 5)
        assert [u.price for u in store.updates()] == [110000, 120000]
        assert [u.seq for u in store.updates()] == [1, 2]
        assert json.loads(opened[0].sent[0])["action"] == "subscribe"
        assert any(json.loads(m) == {"action": "heartbeat"} for m in opened[1].sent)
        day = store.hashes[stats_key("s", DAY)]
        assert day["connected"] == "2" and day["network_error"] == "1"

    asyncio.run(scenario())


# --- the trial ------------------------------------------------------------------------------


def test_trial_stats_and_the_report() -> None:
    s = TrialStats()
    base = T * 1000
    s.price("EUR/USD", base + 500, base, 5, True)
    s.price("EUR/USD", base + 1_500, base, 5, True)
    s.price("EUR/USD", base + 9_000, base + 1_000, 6, False)  # 8 s late; 7.5 s silence
    s.event(base, "connected")
    counts, maxima = s.drain()
    assert s.drain() == ({}, {})
    day: dict[str, int] = {name: n for (_, name), n in counts.items()}
    day.update({name: n for (_, name), n in maxima.items()})
    assert day["delay_le1000:EUR/USD"] == 1 and day["delay_le2000:EUR/USD"] == 1
    assert day["delay_le10000:EUR/USD"] == 1
    assert (day["places:EUR/USD"], day["silence_max_ms:EUR/USD"]) == (6, 7_500)

    second = {"prices:EUR/USD": 1, "places:EUR/USD": 5, "delay_le1000:EUR/USD": 1}
    [row] = [r for r in trial.rows([day, second]) if r.symbol == "EUR/USD"]
    assert (row.days, row.prices, row.refused, row.places, row.digits) == (2, 4, 1, 6, 5)
    assert row.digits_verdict == "raise to 6"
    assert (row.share(1_000), row.share(2_000), row.share(5_000)) == ("50.0%", "75.0%", "75.0%")
    assert (row.delay_max_ms, row.silence_max_ms) == (8_000, 7_500)
    text = trial.render(["2026-11-09", "2026-11-10"], [day, second])
    assert (
        "| EUR/USD | 2 | 2 | 1 | 6 | 5 | raise to 6 | 50.0% | 75.0% | 75.0% | 8 s | 7 s |" in text
    )
    assert "- connected: 1" in text
    assert "| XAU/USD | 0 | - | 0 | - | 2 | no data |" in text
