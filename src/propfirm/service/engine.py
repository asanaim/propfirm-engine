"""The engine process: the only writer of the journal.

    python -m propfirm.service.engine

Starts the runner on PostgreSQL and the Valkey input stream (rebuild,
anchors, price key; see `propfirm.journal.runner`), sends the inputs that
bring the engine in line with the policy in force (the policy file with the
applied admin changes; `policy_changes`), then polls until SIGTERM or
SIGINT. A new journal starts from the policy in force.
After each batch it republishes the live state (at most every
`publish_ms`). A stop request finishes the current batch first, so
nothing is half done; a crash is safe anyway (acceptance test 4).
"""

import asyncio
import os
import signal
import time

import valkey.asyncio as avalkey

from propfirm.journal.anchor import FileAnchorStore
from propfirm.journal.postgres import PostgresJournal
from propfirm.journal.runner import Runner
from propfirm.journal.setup import EngineSetup
from propfirm.journal.stream import InputStreamReader, InputStreamWriter
from propfirm.policy import Policy, load_policy
from propfirm.service.config import ServiceConfig
from propfirm.service.feeder import now_ms
from propfirm.service.live import LivePublisher
from propfirm.service.setup import policy_changes, setup_from_policy
from propfirm.settings.store import policy_in_force


async def run(
    config: ServiceConfig,
    setup: EngineSetup,
    stop: asyncio.Event,
    policy: Policy | None = None,
) -> Runner:
    """Run until `stop` is set; return the runner (for tests). With a
    `policy`, an existing journal keeps its setup and receives the policy's
    changes as inputs; a change that needs a new version number stops the
    start with PolicyError."""
    client = avalkey.Valkey.from_url(config.valkey_url)
    store = await PostgresJournal.connect(config.database_url, config.schema)
    try:
        reader = InputStreamReader(client, config.stream, config.group, config.consumer)
        await reader.ensure_group()
        anchors = FileAnchorStore(config.anchor_path) if config.anchor_path else None
        runner = await Runner.start(
            store, reader, setup, anchors=anchors, keep_stored_setup=policy is not None
        )
        if policy is not None:
            writer = InputStreamWriter(client, config.stream)
            for item in policy_changes(policy, runner.machine, now_ms()):
                await writer.publish(item)
        publisher = LivePublisher(client, config.live_prefix)
        await publisher.publish(runner.machine)
        published = time.monotonic()
        dirty = False
        while not stop.is_set():
            dirty |= await runner.poll(config.batch, config.block_ms) > 0
            if dirty and (time.monotonic() - published) * 1000 >= config.publish_ms:
                await publisher.publish(runner.machine)
                published, dirty = time.monotonic(), False
        if dirty:
            await publisher.publish(runner.machine)
        return runner
    finally:
        await store.close()
        await client.aclose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    config = ServiceConfig.from_env(os.environ)
    policy = await policy_in_force(
        config.database_url, config.settings_schema, load_policy(config.policy_path)
    )
    await run(config, setup_from_policy(policy), stop, policy)


if __name__ == "__main__":
    asyncio.run(_main())
