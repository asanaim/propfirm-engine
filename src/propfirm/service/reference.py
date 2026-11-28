"""The reference (second) price source (D-14.36): OANDA's v20 price stream
into a short rolling window in Valkey, read by the stale price detector.

    python -m propfirm.service.reference

These prices never reach the engine and never price a trade. Each quote is
kept for `WINDOW_MS` (10 minutes) under `<prefix>:<symbol>` as a sorted set
scored by the source time, then trimmed: the store holds no history, and a
cancelled subscription leaves nothing behind after 10 minutes (CLAUDE.md:
licensed prices purgeable within 30 days). The detector compares each fill
within that window and keeps only the evidence of a stale fill.

To purge (within 30 days of cancelling the vendor):
    python -m propfirm.service.reference --purge --confirm "purge reference prices"

Environment: OANDA_TOKEN (secret; never logged), OANDA_ACCOUNT, OANDA_STREAM
(default the practice stream), PROPFIRM_REFERENCE_SYMBOLS (comma separated;
default every launch symbol OANDA lists: forex, metals, oil), VALKEY_URL.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

import httpx
import valkey.asyncio as avalkey

from propfirm.market.oanda import (
    INSTRUMENTS,
    PRACTICE_STREAM,
    Heartbeat,
    OandaError,
    Quote,
    instrument,
    parse,
)
from propfirm.market.symbols import SYMBOLS
from propfirm.service.feeder import now_ms

log = logging.getLogger("propfirm.reference")

PREFIX = "reference:oanda"
WINDOW_MS = 600_000  # quotes kept this long
SILENT_SECONDS = 20  # no line at all (OANDA beats every 5 s): reconnect
BACKOFF_SECONDS = (5, 10, 20, 40, 60)
TRIM_EVERY_MS = 30_000


def member(q: Quote) -> str:
    return f"{q.ts}:{q.bid}:{q.ask}"


def unpack(raw: str | bytes) -> tuple[int, int, int]:
    text = raw.decode() if isinstance(raw, bytes) else raw
    ts, bid, ask = text.split(":")
    return int(ts), int(bid), int(ask)


class ReferenceReader:
    """The detector's view: the last reference quote at or before a time."""

    def __init__(self, client: Any, prefix: str = PREFIX) -> None:
        self._client = client
        self._prefix = prefix

    async def at(self, symbol: str, ts: int, max_age_ms: int) -> tuple[int, int, int] | None:
        """(time, bid, ask) of the last quote with time in
        [ts - max_age_ms, ts], or None."""
        found = await self._client.zrevrangebyscore(
            f"{self._prefix}:{symbol}", ts, ts - max_age_ms, start=0, num=1
        )
        return unpack(found[0]) if found else None

    async def oldest(self, symbol: str) -> int | None:
        """The time of the oldest quote kept for the symbol."""
        found = await self._client.zrange(f"{self._prefix}:{symbol}", 0, 0, withscores=True)
        return int(found[0][1]) if found else None


class Lines(Protocol):
    def __call__(self) -> contextlib.AbstractAsyncContextManager[AsyncIterator[str]]: ...


class Connector:
    def __init__(self, client: Any, open_lines: Lines, prefix: str = PREFIX) -> None:
        self._client = client
        self._open = open_lines
        self._prefix = prefix
        self._trimmed = 0

    async def store(self, quotes: Iterable[Quote], now: int) -> int:
        """Add quotes to each symbol's window; trim it now and then."""
        by_symbol: dict[str, dict[str, int]] = {}
        for q in quotes:
            by_symbol.setdefault(q.symbol, {})[member(q)] = q.ts
        async with self._client.pipeline(transaction=False) as pipe:
            for name, members in by_symbol.items():
                pipe.zadd(f"{self._prefix}:{name}", members)
            if now - self._trimmed >= TRIM_EVERY_MS:
                self._trimmed = now
                for name in SYMBOLS:
                    pipe.zremrangebyscore(f"{self._prefix}:{name}", "-inf", now - WINDOW_MS)
            await pipe.execute()
        return sum(len(m) for m in by_symbol.values())

    async def run_once(self) -> None:
        """One connection: read lines until silence or an error."""
        async with self._open() as lines:
            batch: list[Quote] = []
            iterator = lines.__aiter__()
            while True:
                try:
                    line = await asyncio.wait_for(iterator.__anext__(), SILENT_SECONDS)
                except StopAsyncIteration:
                    return
                try:
                    item = parse(line)
                except OandaError as exc:
                    log.warning("reference: bad line (%s)", exc)
                    continue
                if isinstance(item, Quote):
                    batch.append(item)
                due = bool(batch) and now_ms() - batch[0].ts > 1_000
                if isinstance(item, Heartbeat) or len(batch) >= 100 or due:
                    await self.store(batch, now_ms())
                    batch = []

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        while not stop.is_set():
            try:
                await self.run_once()
                attempt = 0
            except (TimeoutError, httpx.HTTPError, OSError) as exc:
                log.warning("reference: connection lost (%s)", type(exc).__name__)
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            attempt += 1
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)


def oanda_lines(base: str, account: str, token: str, symbols: Iterable[str]) -> Lines:
    url = f"{base}/v3/accounts/{account}/pricing/stream"
    params = {"instruments": ",".join(instrument(s) for s in symbols)}
    headers = {"Authorization": f"Bearer {token}", "Accept-Datetime-Format": "RFC3339"}

    @contextlib.asynccontextmanager
    async def open_lines() -> AsyncIterator[AsyncIterator[str]]:
        timeout = httpx.Timeout(10.0, read=SILENT_SECONDS)
        async with (
            httpx.AsyncClient(timeout=timeout) as http,
            http.stream("GET", url, params=params, headers=headers) as response,
        ):
            response.raise_for_status()
            yield response.aiter_lines()

    return open_lines


PURGE_CONFIRM = "purge reference prices"
SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")


async def purge(
    client: Any, pool: Any, customer_schema: str, prefix: str = PREFIX
) -> dict[str, int]:
    """Delete every reference price kept: the rolling window in Valkey and
    the reference quotes kept as stale fill evidence (CLAUDE.md: within 30
    days of cancelling the vendor). Stale fills go with their evidence."""
    if not SCHEMA.fullmatch(customer_schema):
        raise ValueError("bad schema name")
    s = customer_schema
    keys = [k async for k in client.scan_iter(match=f"{prefix}:*")]
    deleted = int(await client.delete(*keys)) if keys else 0
    rows = await pool.execute(f"DELETE FROM {s}.stale_fills")
    return {"valkey_keys": deleted, "stale_fills": int(rows.split()[-1])}


async def purge_main(confirm: str) -> None:
    if confirm != PURGE_CONFIRM:
        raise SystemExit(f'--confirm must be exactly "{PURGE_CONFIRM}"')
    import asyncpg

    from propfirm.service.config import ServiceConfig

    config = ServiceConfig.from_env(os.environ)
    client = avalkey.Valkey.from_url(config.valkey_url)
    pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=1)
    try:
        print(json.dumps(await purge(client, pool, config.customer_schema), sort_keys=True))
    finally:
        await pool.close()
        await client.aclose()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if "--purge" in sys.argv:
        confirm = sys.argv[sys.argv.index("--confirm") + 1] if "--confirm" in sys.argv else ""
        await purge_main(confirm)
        return
    token = os.environ.get("OANDA_TOKEN", "")
    account = os.environ.get("OANDA_ACCOUNT", "")
    if not token or not account:
        raise SystemExit("set OANDA_TOKEN and OANDA_ACCOUNT")
    wanted = [s for s in os.environ.get("PROPFIRM_REFERENCE_SYMBOLS", "").split(",") if s]
    symbols = wanted or sorted(INSTRUMENTS)
    unknown = sorted(set(symbols) - set(INSTRUMENTS))
    if unknown:
        raise SystemExit(f"no OANDA instrument for: {unknown}")
    base = os.environ.get("OANDA_STREAM", PRACTICE_STREAM)
    client = avalkey.Valkey.from_url(os.environ["VALKEY_URL"])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await Connector(client, oanda_lines(base, account, token, symbols)).run(stop)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
