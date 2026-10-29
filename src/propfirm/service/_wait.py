"""Waiting that a stop request cuts short."""

import asyncio
import contextlib


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep up to `seconds`, returning early once `stop` is set."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
