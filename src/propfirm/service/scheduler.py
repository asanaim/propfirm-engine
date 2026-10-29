"""Clock ticks and checkpoints.

    python -m propfirm.service.scheduler

Publishes `Advance(now)` every PROPFIRM_TICK_MS (default 1,000) so the gap
guard and the 17:00 New York day roll run without prices, and
`Checkpoint(now)` every PROPFIRM_CHECKPOINT_MS (default 600,000: 10 minutes)
so start-up resumes from a recent snapshot [DEFAULT cadences; set in P3].

One scheduler per engine: duplicates would only add harmless ticks and
extra checkpoints, but waste journal space.
"""

import asyncio
import os
import re
import signal
from collections.abc import Callable

import valkey.asyncio as avalkey

from propfirm.journal.inputs import Advance, Checkpoint
from propfirm.journal.stream import InputStreamWriter
from propfirm.service._wait import wait_or_stop
from propfirm.service.config import ConfigError, ServiceConfig
from propfirm.service.feeder import now_ms


async def schedule(
    writer: InputStreamWriter,
    stop: asyncio.Event,
    tick_ms: int,
    checkpoint_ms: int,
    clock: Callable[[], int] = now_ms,
) -> tuple[int, int]:
    """Run until `stop`; return (ticks, checkpoints) sent."""
    if tick_ms < 1 or checkpoint_ms < 1:
        raise ValueError("intervals must be positive")
    ticks = checkpoints = 0
    next_checkpoint = clock() + checkpoint_ms
    while not stop.is_set():
        now = clock()
        await writer.publish(Advance(now))
        ticks += 1
        if now >= next_checkpoint:
            await writer.publish(Checkpoint(now))
            checkpoints += 1
            next_checkpoint = now + checkpoint_ms
        await wait_or_stop(stop, tick_ms / 1000)
    return ticks, checkpoints


def _interval(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ConfigError(f"{name} must be a positive whole number")
    return int(raw)


async def _main() -> None:
    config = ServiceConfig.from_env(os.environ)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    client = avalkey.Valkey.from_url(config.valkey_url)
    try:
        await schedule(
            InputStreamWriter(client, config.stream),
            stop,
            _interval("PROPFIRM_TICK_MS", 1_000),
            _interval("PROPFIRM_CHECKPOINT_MS", 600_000),
        )
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
