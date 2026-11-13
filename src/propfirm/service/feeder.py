"""Synthetic prices in real time, for development only (CLAUDE.md).

    python -m propfirm.service.feeder

Generates the synthetic feed from the current time and publishes each update
to the engine's input stream when its receive time comes. Never used where
real prices are configured; the Twelve Data connector replaces it (P2 step 6).

Environment: PROPFIRM_FEED_SEED (default "dev"), PROPFIRM_FEED_SYMBOLS
(comma-separated; default all 30).

A restart continues the feed, as the Twelve Data connector does: sequence
numbers carry on from the last one per symbol and prices from the last
price, both kept in Valkey (`<stream>:seq:synthetic`, "seq price"), written
before the update is sent (a crash leaves at worst a gap, which the engine
reports). Without them the engine took every price after a restart as a
duplicate (seq 1, 2, ... already seen) and the quotes froze until the new
numbers passed the old ones. On the first start after this change the last
numbers come from the engine's published quotes.
"""

import asyncio
import dataclasses
import json
import os
import signal
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping

import valkey.asyncio as avalkey

from propfirm.journal.stream import InputStreamWriter
from propfirm.market.symbols import SYMBOLS
from propfirm.market.synthetic import DEFAULT_START_POINTS, SyntheticConfig, generate
from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.service._wait import wait_or_stop
from propfirm.service.config import ServiceConfig
from propfirm.trading.hours import TradingHours

WEEK_MS = 7 * 24 * 3_600_000


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def resume(
    stored: Mapping[str, str], live: Mapping[str, Mapping[str, object]]
) -> tuple[dict[str, int], dict[str, int]]:
    """The last sequence number and price per symbol, from the feeder's own
    record ("seq price") or else the engine's published quote: the higher
    sequence number wins."""
    seqs: dict[str, int] = {}
    prices: dict[str, int] = {}
    for symbol, quote in live.items():
        seq, mid = quote.get("seq"), quote.get("mid")
        if isinstance(seq, int) and seq > 0:
            seqs[symbol] = seq
            if isinstance(mid, int) and mid > 0:
                prices[symbol] = mid
    for symbol, text in stored.items():
        seq_text, _, price_text = text.partition(" ")
        seq, price = int(seq_text), int(price_text)
        if seq >= seqs.get(symbol, 0):
            seqs[symbol] = seq
            prices[symbol] = price
    return seqs, prices


def continued(updates: Iterator[PriceUpdate], last: Mapping[str, int]) -> Iterator[PriceUpdate]:
    """The updates numbered on from `last` per symbol (gap-free, as generated)."""
    for update in updates:
        yield dataclasses.replace(update, seq=last.get(update.symbol, 0) + update.seq)


def in_hours(
    updates: Iterator[PriceUpdate], hours: Mapping[str, TradingHours | None]
) -> Iterator[PriceUpdate]:
    """The updates whose symbol's market is open at their receive time,
    numbered on without gaps per symbol (a closed market sends nothing, as
    a real feed; the engine would ignore those prices anyway)."""
    seqs: dict[str, int] = {}
    for update in updates:
        schedule = hours.get(update.symbol)
        if schedule is not None and not schedule.is_open(update.receive_ts):
            continue
        seq = seqs[update.symbol] = seqs.get(update.symbol, 0) + 1
        yield dataclasses.replace(update, seq=seq)


async def feed(
    writer: InputStreamWriter,
    updates: Iterator[PriceUpdate],
    stop: asyncio.Event,
    clock: Callable[[], int] = now_ms,
    record: Callable[[PriceUpdate], Awaitable[None]] | None = None,
) -> int:
    """Publish each update at its receive time; return how many were sent.
    `record` keeps the update's sequence number and price first."""
    sent = 0
    for update in updates:
        wait_ms = update.receive_ts - clock()
        if wait_ms > 0:
            await wait_or_stop(stop, wait_ms / 1000)
        if stop.is_set():
            break
        if record is not None:
            await record(update)
        await writer.publish(update)
        sent += 1
    return sent


async def _main() -> None:
    config = ServiceConfig.from_env(os.environ)
    if config.price_source != "synthetic":
        raise SystemExit("synthetic prices never mix with licensed ones (CLAUDE.md)")
    symbols = tuple(s for s in os.environ.get("PROPFIRM_FEED_SYMBOLS", "").split(",") if s)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = avalkey.Valkey.from_url(config.valkey_url)
    key = f"{config.stream}:seq:synthetic"
    try:
        own: dict[bytes, bytes] = await client.hgetall(key)
        raw: dict[bytes, bytes] = await client.hgetall(f"{config.live_prefix}:quotes")
        stored = {k.decode(): v.decode() for k, v in own.items()}
        live = {k.decode(): json.loads(v) for k, v in raw.items()}
        seqs, prices = resume(stored, live)
        start = now_ms()
        chosen = symbols or tuple(SYMBOLS)
        synthetic = SyntheticConfig(
            # the start time in the seed: a restart does not replay the same path
            seed=f"{os.environ.get('PROPFIRM_FEED_SEED', 'dev')}|{start}",
            symbols=chosen,
            start_points={
                **DEFAULT_START_POINTS,
                **{s: p for s, p in prices.items() if s in chosen},
            },
        )

        async def record(update: PriceUpdate) -> None:
            await client.hset(key, update.symbol, f"{update.seq} {update.price}")

        writer = InputStreamWriter(client, config.stream)
        hours = {i.name: i.hours for i in load_policy(config.policy_path).trading.instruments}
        generated = in_hours(generate(synthetic, start, start + WEEK_MS), hours)
        updates = continued(generated, seqs)
        await feed(writer, updates, stop, record=record)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
