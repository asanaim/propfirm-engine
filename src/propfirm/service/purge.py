"""Purge Twelve Data's prices (CLAUDE.md: within 30 days of cancelling).

    python -m propfirm.service.purge --licensed --confirm "purge licensed prices"

Run with the engine, the projector and the price connector stopped (the
journal and the projector refuse a second writer, and their locks are taken
first, so a running one stops the purge before anything is deleted). Then,
in order:

1. the input stream: only when the engine has read and acknowledged every
   input (otherwise refused: an order would be lost), then trimmed to empty;
2. the journal's licensed tables (prices, price keys, snapshots); the hash
   chain still verifies, replay is no longer possible;
3. the projections: bars and account histories, the cursor moved to the
   journal's head;
4. the live state in Valkey (quotes, accounts, head).

Prints the rows and keys deleted per store. Keep the output as the record
of the purge.
"""

import argparse
import asyncio
import json
import os
from typing import Any

import valkey
import valkey.asyncio as avalkey

from propfirm.journal.postgres import PostgresJournal
from propfirm.service.config import ServiceConfig
from propfirm.service.live import LivePublisher
from propfirm.service.projector import Projector

CONFIRM = "purge licensed prices"


class PurgeRefused(RuntimeError):
    pass


async def stream_drained(client: avalkey.Valkey, stream: str, group: str) -> bool:
    """True when the group has read everything and nothing is pending."""
    try:
        groups: list[dict[str, Any]] = await client.xinfo_groups(stream)
    except valkey.ResponseError:  # no stream
        return True
    for g in groups:
        name = g.get("name")
        if (name.decode() if isinstance(name, bytes) else name) != group:
            continue
        return int(g.get("pending") or 0) == 0 and int(g.get("lag") or 0) == 0
    length = int(await client.xlen(stream))
    return length == 0


async def purge(config: ServiceConfig) -> dict[str, Any]:
    client = avalkey.Valkey.from_url(config.valkey_url)
    try:
        # the writers' locks first: a running engine or projector stops the
        # purge before anything is deleted
        journal = await PostgresJournal.connect(config.database_url, config.schema)
        try:
            projector = await Projector.connect(
                config.database_url, config.schema, config.read_schema
            )
            try:
                if not await stream_drained(client, config.stream, config.group):
                    raise PurgeRefused(
                        f"{config.stream}: the engine has unread or unacknowledged inputs; "
                        "start it until they are processed, stop it, then purge"
                    )
                out: dict[str, Any] = {}
                out["stream"] = int(await client.xtrim(config.stream, maxlen=0, approximate=False))
                out["journal"] = await journal.purge_licensed()
                out["projections"] = await projector.purge_licensed()
                out["live"] = await LivePublisher(client, config.live_prefix).flush()
                return out
            finally:
                await projector.close()
        finally:
            await journal.close()
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--licensed", action="store_true", required=True)
    parser.add_argument("--confirm", required=True, help=f'exactly "{CONFIRM}"')
    args = parser.parse_args()
    if args.confirm != CONFIRM:
        raise SystemExit(f'--confirm must be exactly "{CONFIRM}"')
    result = asyncio.run(purge(ServiceConfig.from_env(os.environ)))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
