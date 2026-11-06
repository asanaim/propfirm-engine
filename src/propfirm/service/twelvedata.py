"""The Twelve Data price connector (P2 step 6): live prices into the engine.

    python -m propfirm.service.twelvedata

Holds one WebSocket connection (Twelve Data allows 3 per key; more close
the earlier ones), subscribes the configured symbols, sends a heartbeat
every 10 seconds, and publishes each price to the engine's input stream as a
`PriceUpdate`: integer points (a price with more decimals than the symbol's
`digits` is refused and counted), the source time from Twelve Data's UNIX
seconds, the receive time from this machine's clock (the edge: the delay
guard compares the two, step 4).

Sequence numbers continue across restarts: the last one per symbol is kept
in Valkey (`<stream>:seq:twelve_data`) and written in the same transaction
as the update, so a crash never reuses one (at worst a gap, which the engine
reports and applies).

The trial (C10) counts per UTC day and symbol: prices, refusals, the most
decimal places seen, receive-minus-source delay buckets and the longest
silence; `scripts/c10_report.py` prints them. These counters hold no prices
(no licensed data).

Environment: PROPFIRM_PRICE_SOURCE=twelve_data (required: synthetic and
licensed prices never mix), TWELVE_DATA_API_KEY (secret; never logged),
PROPFIRM_TWELVE_DATA_SYMBOLS (the map, default
config/market/twelve_data.toml), PROPFIRM_FEED_SYMBOLS (comma-separated
subset; default all), TWELVE_DATA_URL (tests only).
"""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import valkey.asyncio as avalkey
from websockets.asyncio.client import connect as ws_connect

from propfirm.journal.stream import encode_input
from propfirm.market.symbols import SYMBOLS
from propfirm.market.twelvedata import (
    HEARTBEAT_SECONDS,
    URL,
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
from propfirm.service.config import ServiceConfig
from propfirm.service.feeder import now_ms

log = logging.getLogger("propfirm.twelvedata")

DEFAULT_MAP = Path(__file__).resolve().parents[3] / "config" / "market" / "twelve_data.toml"
SILENT_SECONDS = 3 * HEARTBEAT_SECONDS  # nothing at all received: reconnect
BACKOFF_SECONDS = (5, 10, 20, 40, 60)  # between connection attempts
DELAY_BUCKETS_MS = (1_000, 2_000, 5_000, 10_000, 30_000)  # receive - source, "le" buckets
FLUSH_SECONDS = 60


class Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


Connect = Callable[[], contextlib.AbstractAsyncContextManager[Socket]]


# --- the trial's counters (pure) -----------------------------------------------------------


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


@dataclass
class TrialStats:
    """Per UTC day and symbol. `drain` returns what to add (counts) and to
    raise (maxima) since the last drain."""

    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (day, field) -> n
    maxima: dict[tuple[str, str], int] = field(default_factory=dict)
    _last_seen: dict[str, int] = field(default_factory=dict)  # symbol -> receive ms

    def _add(self, day: str, name: str, n: int = 1) -> None:
        self.counts[(day, name)] = self.counts.get((day, name), 0) + n

    def _max(self, day: str, name: str, value: int) -> None:
        if value > self.maxima.get((day, name), -1):
            self.maxima[(day, name)] = value

    def price(self, symbol: str, receive_ms: int, source_ms: int, decimals: int, ok: bool) -> None:
        day = day_of(receive_ms)
        self._add(day, f"prices:{symbol}")
        if not ok:
            self._add(day, f"refused:{symbol}")
        self._max(day, f"places:{symbol}", decimals)
        delay = receive_ms - source_ms
        bucket = next((f"le{b}" for b in DELAY_BUCKETS_MS if delay <= b), "gt30000")
        self._add(day, f"delay_{bucket}:{symbol}")
        self._max(day, f"delay_max:{symbol}", max(delay, 0))
        last = self._last_seen.get(symbol)
        if last is not None:
            self._max(day, f"silence_max_ms:{symbol}", receive_ms - last)
        self._last_seen[symbol] = receive_ms

    def event(self, receive_ms: int, name: str) -> None:
        self._add(day_of(receive_ms), name)

    def drain(self) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
        counts, maxima = self.counts, self.maxima
        self.counts, self.maxima = {}, {}
        return counts, maxima


def stats_key(stream: str, day: str) -> str:
    return f"{stream}:trial:twelve_data:{day}"


# --- the connector ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Converted:
    update: PriceUpdate | None  # None: refused
    symbol: str
    decimals: int


class Connector:
    def __init__(
        self,
        client: avalkey.Valkey,
        stream: str,
        symbols: SymbolMap,
        connect: Connect,
        clock: Callable[[], int] = now_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._stream = stream
        self._map = symbols
        self._from_source = symbols.from_source
        self._connect = connect
        self._clock = clock
        self._sleep = sleep
        self._seq: dict[str, int] = {}
        self.stats = TrialStats()
        self.published = 0
        self.refused = 0

    @property
    def seq_key(self) -> str:
        return f"{self._stream}:seq:twelve_data"

    async def load_seq(self) -> None:
        raw: Mapping[bytes, bytes] = await self._client.hgetall(self.seq_key)  # type: ignore[misc]
        self._seq = {k.decode(): int(v) for k, v in raw.items()}

    def convert(self, p: Price, receive_ms: int) -> Converted | None:
        """None: not one of ours (ignored)."""
        name = self._from_source.get(p.source_symbol)
        if name is None:
            return None
        decimals = places(p.price)
        points = to_points(p.price, SYMBOLS[name])
        if points is None or points < 1:
            return Converted(None, name, decimals)
        seq = self._seq.get(name, 0) + 1
        update = PriceUpdate(name, seq, p.ts_s * 1000, receive_ms, points)
        return Converted(update, name, decimals)

    async def publish(self, update: PriceUpdate) -> None:
        """The sequence number and the update together, or neither."""
        pipe = self._client.pipeline(transaction=True)
        pipe.hset(self.seq_key, update.symbol, str(update.seq))
        pipe.xadd(self._stream, encode_input(update))
        await pipe.execute()
        self._seq[update.symbol] = update.seq
        self.published += 1

    async def handle(self, text: str | bytes) -> None:
        now = self._clock()
        try:
            event = parse(text)
        except TwelveDataError as exc:
            log.warning("unreadable message: %s", exc)
            self.stats.event(now, "unreadable")
            return
        match event:
            case Price():
                converted = self.convert(event, now)
                if converted is None:
                    self.stats.event(now, f"unknown_symbol:{event.source_symbol}")
                    return
                ok = converted.update is not None
                self.stats.price(converted.symbol, now, event.ts_s * 1000, converted.decimals, ok)
                if converted.update is None:
                    self.refused += 1
                    return
                await self.publish(converted.update)
            case SubscribeStatus():
                log.info("subscribed %d, failed %s", len(event.success), list(event.fails))
                for s in event.fails:
                    self.stats.event(now, f"subscribe_failed:{s}")
                if not event.ok:
                    self.stats.event(now, "subscribe_not_ok")
            case Heartbeat():
                if not event.ok:
                    self.stats.event(now, "heartbeat_not_ok")
            case Other():
                log.info("event %s", event.event)
                self.stats.event(now, f"event:{event.event}")

    async def flush(self) -> None:
        counts, maxima = self.stats.drain()
        if not counts and not maxima:
            return
        pipe = self._client.pipeline(transaction=True)
        for (day, name), n in counts.items():
            pipe.hincrby(stats_key(self._stream, day), name, n)
        await pipe.execute()
        for (day, name), value in maxima.items():  # one writer: read, compare, write
            key = stats_key(self._stream, day)
            old = await self._client.hget(key, name)  # type: ignore[misc]
            if old is None or int(old) < value:
                await self._client.hset(key, name, str(value))  # type: ignore[misc]

    async def session(self, stop: asyncio.Event) -> None:
        """One connection until it fails, goes silent or `stop`."""
        async with self._connect() as ws:
            await ws.send(subscribe(self._map))
            self.stats.event(self._clock(), "connected")

            async def beat() -> None:
                while True:
                    await self._sleep(HEARTBEAT_SECONDS)
                    await ws.send(heartbeat())

            beating = asyncio.create_task(beat())
            try:
                while not stop.is_set():
                    try:
                        async with asyncio.timeout(SILENT_SECONDS):
                            message = await _recv_or_stop(ws, stop)
                    except TimeoutError:
                        log.warning("nothing received for %d s: reconnecting", SILENT_SECONDS)
                        self.stats.event(self._clock(), "silent_reconnect")
                        break
                    if message is None:
                        break
                    await self.handle(message)
            finally:
                beating.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beating

    async def run(self, stop: asyncio.Event) -> None:
        await self.load_seq()
        attempt = 0

        async def flushing() -> None:
            while True:
                await asyncio.sleep(FLUSH_SECONDS)
                await self.flush()

        flusher = asyncio.create_task(flushing())
        try:
            while not stop.is_set():
                before = self.published + self.refused
                try:
                    await self.session(stop)
                    attempt += 1
                except (OSError, TimeoutError) as exc:  # includes a dropped connection
                    attempt += 1
                    log.warning("connection failed: %s", _safe(exc))
                    self.stats.event(self._clock(), "network_error")
                except Exception as exc:  # websockets' own errors; never echo the URL (key)
                    attempt += 1
                    log.warning("connection closed: %s", _safe(exc))
                    self.stats.event(self._clock(), "closed_by_server")
                if self.published + self.refused > before:
                    attempt = 0  # prices came: the next try is quick
                if stop.is_set():
                    break
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] if attempt else 1
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(wait):
                        await stop.wait()
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
            await self.flush()


async def _recv_or_stop(ws: Socket, stop: asyncio.Event) -> str | bytes | None:
    recv = asyncio.ensure_future(ws.recv())
    stopped = asyncio.ensure_future(stop.wait())
    done, _ = await asyncio.wait({recv, stopped}, return_when=asyncio.FIRST_COMPLETED)
    if recv in done:
        stopped.cancel()
        return recv.result()
    recv.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await recv
    return None


def _safe(exc: BaseException) -> str:
    """The error's kind only: messages may carry the URL, and so the key."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return type(exc).__name__ + ("" if status is None else f" (HTTP {status})")


def connector_url(key: str, base: str = URL) -> str:
    return f"{base}?{urlencode({'apikey': key})}"


def websocket(url: str) -> Connect:
    def open_() -> contextlib.AbstractAsyncContextManager[Socket]:
        return _opened(url)

    return open_


@contextlib.asynccontextmanager
async def _opened(url: str) -> AsyncIterator[Socket]:
    # websockets' own pings stay on (20 s); Twelve Data's heartbeat is extra
    async with ws_connect(url, open_timeout=15, close_timeout=5, max_size=2**20) as ws:
        yield ws


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = ServiceConfig.from_env(os.environ)
    if config.price_source != "twelve_data":
        raise SystemExit("PROPFIRM_PRICE_SOURCE must be twelve_data to run the connector")
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not key:
        raise SystemExit("TWELVE_DATA_API_KEY is required")
    path = Path(os.environ.get("PROPFIRM_TWELVE_DATA_SYMBOLS", "") or DEFAULT_MAP)
    full = load_symbol_map(path)
    names = tuple(s for s in os.environ.get("PROPFIRM_FEED_SYMBOLS", "").split(",") if s)
    symbols = full.subset(names) if names else full
    url = connector_url(key, os.environ.get("TWELVE_DATA_URL", "") or URL)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = avalkey.Valkey.from_url(config.valkey_url)
    try:
        connector = Connector(client, config.stream, symbols, websocket(url))
        log.info("Twelve Data: %d symbols", len(symbols.to_source))
        await connector.run(stop)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
