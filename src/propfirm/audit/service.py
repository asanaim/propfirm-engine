"""The auditor service and the audit check.

    python -m propfirm.audit.service          # runs until stopped
    python -m propfirm.audit.service --verify # one full check; exit 1 if broken

Environment: DATABASE_URL; PROPFIRM_AUDIT_SCHEMA (default `audit`);
PROPFIRM_AUDIT_ANCHOR_PATH (anchors file; production: storage the
database's operators cannot change, an open decision as for the journal's
anchors); PROPFIRM_SIEM_SINKS (see `propfirm.audit.sinks`; default
`stdout`); PROPFIRM_AUDIT_PASS_MS (default 60,000) and
PROPFIRM_AUDIT_ANCHOR_MS (default 300,000) [DEFAULT cadences].
"""

import asyncio
import logging
import os
import signal
import socket
import sys
from pathlib import Path

import asyncpg

from propfirm.audit.auditor import Auditor
from propfirm.audit.sinks import sinks_from
from propfirm.audit.store import AuditStore, migrate
from propfirm.journal.anchor import FileAnchorStore
from propfirm.service._wait import wait_or_stop
from propfirm.service.config import ConfigError
from propfirm.service.feeder import now_ms
from propfirm.service.scheduler import _interval


async def _main(verify: bool) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError("DATABASE_URL is required")
    schema = os.environ.get("PROPFIRM_AUDIT_SCHEMA", "audit")
    anchor_path = os.environ.get("PROPFIRM_AUDIT_ANCHOR_PATH")
    anchors = FileAnchorStore(Path(anchor_path)) if anchor_path else None
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await migrate(conn, schema)
        store = AuditStore(pool, schema)
        if verify:
            status = await Auditor(store, anchors, [], now_ms).run_once()
            if status.broken_seq is not None:
                print(f"BROKEN at record {status.broken_seq}: {status.broken_reason}")
                return 1
            note = "" if anchors else " (no anchors configured: internal consistency only)"
            print(f"ok: {status.verified_seq} records, anchored to {status.anchored_seq}{note}")
            return 0
        sinks = sinks_from(os.environ.get("PROPFIRM_SIEM_SINKS", "stdout"), socket.gethostname())
        auditor = Auditor(
            store, anchors, sinks, now_ms, _interval("PROPFIRM_AUDIT_ANCHOR_MS", 300_000)
        )
        pass_s = _interval("PROPFIRM_AUDIT_PASS_MS", 60_000) / 1000
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        while not stop.is_set():
            await auditor.run_once()
            await wait_or_stop(stop, pass_s)
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main("--verify" in sys.argv[1:])))
