"""Synthetic prices in real time, for development only (CLAUDE.md).

    python -m propfirm.service.feeder

Generates the synthetic feed from the current time and publishes each update
to the engine's input stream when its receive time comes. Never used where
real prices are configured; the Twelve Data connector replaces it (P2 step 6).

Environment: PROPFIRM_FEED_SEED (default "dev"), PROPFIRM_FEED_SYMBOLS
(comma-separated; default all 30).
"""

import asyncio
import os
import signal
import time
from collections.abc import Callable, Iterator

import valkey.asyncio as avalkey

from propfirm.journal.stream import InputStreamWriter
from propfirm.market.symbols import SYMBOLS
from propfirm.market.synthetic import SyntheticConfig, generate
from propfirm.market.types import PriceUpdate
from propfirm.service._wait import wait_or_stop
from propfirm.service.config import ServiceConfig

WEEK_MS = 7 * 24 * 3_600_000


def now_ms() -> int:
    return time.time_ns() // 1_000_000


async def feed(
    writer: InputStreamWriter,
    updates: Iterator[PriceUpdate],
    stop: asyncio.Event,
    clock: Callable[[], int] = now_ms,
) -> int:
    """Publish each update at its receive time; return how many were sent."""
    sent = 0
    for update in updates:
        wait_ms = update.receive_ts - clock()
        if wait_ms > 0:
            await wait_or_stop(stop, wait_ms / 1000)
        if stop.is_set():
            break
        await writer.publish(update)
        sent += 1
    return sent


async def _main() -> None:
    config = ServiceConfig.from_env(os.environ)
    if config.price_source != "synthetic":
        raise SystemExit("synthetic prices never mix with licensed ones (CLAUDE.md)")
    symbols = tuple(s for s in os.environ.get("PROPFIRM_FEED_SYMBOLS", "").split(",") if s)
    synthetic = SyntheticConfig(
        seed=os.environ.get("PROPFIRM_FEED_SEED", "dev"),
        symbols=symbols or tuple(SYMBOLS),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = avalkey.Valkey.from_url(config.valkey_url)
    try:
        start = now_ms()
        writer = InputStreamWriter(client, config.stream)
        await feed(writer, generate(synthetic, start, start + WEEK_MS), stop)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
