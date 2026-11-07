"""The auditor: checks the audit chain, anchors it outside the database and
ships it to the SIEM sinks.

Each pass:
1. checks every record added since the last pass (all of them on start)
   against the hash chain, and the latest external anchor against the log:
   one matching anchor vouches for every record before it, since each hash
   covers all earlier ones. A missing anchored record means a rollback; a
   different hash means the log was replaced;
2. anchors the head when it has moved and `anchor_ms` has passed, never
   while the chain is broken;
3. sends the checked records to each sink from its cursor; a sink that
   fails is retried next pass, the others go on.

A break is reported to every sink as an `audit.chain_broken` alert on
every pass, and stored in `audit_status` for the back office.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from propfirm.audit.chain import CHAIN_ID, GENESIS, AuditEvent, AuditRecord, Break, check
from propfirm.audit.sinks import Sink, SinkError
from propfirm.audit.store import AuditStore, Status
from propfirm.journal.anchor import Anchor, AnchorError, AnchorStore

log = logging.getLogger("propfirm.audit")

BATCH = 5000


@dataclass(slots=True)
class _Progress:
    seq: int = 0
    hash: str = GENESIS
    anchored_seq: int = 0
    anchored_at: int | None = None
    broken: Break | None = None


class Auditor:
    def __init__(
        self,
        store: AuditStore,
        anchors: AnchorStore | None,
        sinks: Sequence[Sink],
        clock: Callable[[], int],
        anchor_ms: int = 300_000,
    ) -> None:
        self._store = store
        self._anchors = anchors
        self._sinks = list(sinks)
        self._clock = clock
        self._anchor_ms = anchor_ms
        self._p = _Progress()

    @property
    def broken(self) -> Break | None:
        return self._p.broken

    async def _check(self) -> None:
        p = self._p
        if p.broken is None and p.seq > 0:  # still there, unchanged, since the last pass
            seen = await self._store.record(p.seq)
            if seen is None:
                p.broken = Break(p.seq, "checked earlier, now missing (records removed)")
            elif seen.hash != p.hash:
                p.broken = Break(p.seq, "checked earlier, now different (the log was replaced)")
        while p.broken is None:
            batch = await self._store.records(after=p.seq, limit=BATCH)
            if not batch:
                break
            found = check(batch, p.seq, p.hash)
            if found is not None:
                p.broken = found
                break
            p.seq, p.hash = batch[-1].seq, batch[-1].hash
        if p.broken is None and self._anchors is not None:
            p.broken = await self._check_anchor()

    async def _check_anchor(self) -> Break | None:
        assert self._anchors is not None
        try:
            anchored = await self._anchors.anchors(CHAIN_ID)
        except AnchorError as exc:
            return Break(0, f"the anchor store is unreadable: {exc}")
        if not anchored:
            return None
        last = anchored[-1]
        self._p.anchored_seq = max(self._p.anchored_seq, last.seq)
        if last.seq > self._p.seq:
            return Break(last.seq, "anchored, but no longer in the log (rolled back)")
        record = await self._store.record(last.seq)
        if record is None or record.hash != last.hash:
            return Break(last.seq, "differs from its anchor (the log was replaced)")
        return None

    async def _anchor(self, now: int) -> None:
        p = self._p
        if self._anchors is None or p.broken is not None or p.seq <= p.anchored_seq:
            return
        if p.anchored_at is not None and now - p.anchored_at < self._anchor_ms:
            return
        try:
            await self._anchors.publish(Anchor(CHAIN_ID, p.seq, p.hash))
        except OSError as exc:  # an anchor outage never stops the trail
            log.error("audit anchor not written: %s", exc)
            return
        p.anchored_seq, p.anchored_at = p.seq, now

    async def _ship(self, now: int) -> None:
        p = self._p
        alert = None
        if p.broken is not None:
            target = f"seq={p.broken.seq}"
            event = AuditEvent(now, "auditor", "audit.chain_broken", target, 0, "", "")
            alert = AuditRecord(p.broken.seq, event, GENESIS, GENESIS)
        for sink in self._sinks:
            try:
                cursor = await self._store.cursor(sink.name)
                while cursor < p.seq:
                    size = min(BATCH, p.seq - cursor)
                    batch = await self._store.records(after=cursor, limit=size)
                    if not batch:
                        break
                    await sink.send(batch)
                    cursor = batch[-1].seq
                    await self._store.set_cursor(sink.name, cursor)
                if alert is not None:
                    await sink.send([alert])
            except SinkError as exc:
                log.error("audit sink failed, retried next pass: %s", exc)

    async def run_once(self) -> Status:
        now = self._clock()
        await self._check()
        await self._anchor(now)
        await self._ship(now)
        p = self._p
        if p.broken is not None:
            log.critical("audit chain broken at %d: %s", p.broken.seq, p.broken.reason)
        status = Status(
            now,
            p.seq,
            p.anchored_seq,
            None if p.broken is None else p.broken.seq,
            None if p.broken is None else p.broken.reason,
        )
        await self._store.set_status(status)
        return status
