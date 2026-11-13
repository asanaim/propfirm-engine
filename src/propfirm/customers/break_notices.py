"""Emails for trades closed before a market break (D-13.23).

The engine closes a funded account's positions and cancels its pending
orders shortly before a market closes for 2 hours or more (reason
`market_break`). This sweep reads the journal after its cursor and writes
one email per account and journal record to the notification outbox,
cursor moved in the same transaction (never missed or doubled); the
outbox sends it (`Notifications.advance`). A closure is not a breach: the
email says so.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import asyncpg

from propfirm.customers.notifications import Notice, insert_notice
from propfirm.journal.machine import CODEC
from propfirm.pgschema import check_schema
from propfirm.settings.notices import Notices, utc_text
from propfirm.trading.events import (
    CloseReason,
    OrderCancelled,
    PositionClosed,
    RejectReason,
)

log = logging.getLogger(__name__)
KIND = "market_break_close"


@dataclass(frozen=True, slots=True)
class BreakClosure:
    """What one journal record closed on one account before a break."""

    account_id: str
    ts: int
    lines: tuple[str, ...]


def lots_text(volume: int) -> str:
    """Hundredths of a lot as text: 100 -> "1.00 lot", 250 -> "2.50 lots"."""
    whole, hundredths = divmod(volume, 100)
    return f"{whole}.{hundredths:02d} {'lot' if volume == 100 else 'lots'}"


def usd_text(cents: int) -> str:
    """Signed US cents as text: -1000 -> "-10.00 USD"."""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{rest:02d} USD"


def closures(events: Iterable[object]) -> list[BreakClosure]:
    """The market-break closures in one record's events, per account in the
    order the engine made them."""
    lines: dict[str, list[str]] = {}
    first: dict[str, int] = {}
    for e in events:
        if isinstance(e, PositionClosed) and e.reason is CloseReason.MARKET_BREAK:
            line = (
                f"  {e.symbol} {e.side.value} {lots_text(e.volume)}: closed, "
                f"result {usd_text(e.pnl_cents)}"
            )
        elif isinstance(e, OrderCancelled) and e.reason is RejectReason.MARKET_BREAK:
            line = f"  Pending order {e.order_id}: cancelled"
        else:
            continue
        lines.setdefault(e.account_id, []).append(line)
        first.setdefault(e.account_id, e.ts)
    return [BreakClosure(a, first[a], tuple(ls)) for a, ls in lines.items()]


def break_notice(
    email: str, full_name: str, closure: BreakClosure, notices: Notices | None = None
) -> Notice:
    values = {
        "full_name": full_name,
        "acc": closure.account_id,
        "time": utc_text(closure.ts),
        "closed_lines": "".join(f"\n{line}" for line in closure.lines),
    }
    subject, body = (notices or Notices()).render(KIND, values)
    return Notice(KIND, email, subject, body)


class BreakNotices:
    def __init__(self, pool: asyncpg.Pool, customer_schema: str, journal_schema: str) -> None:
        """The customers schema must be migrated (CustomerStore.open)."""
        self._pool = pool
        self._s = check_schema(customer_schema)
        self._j = check_schema(journal_schema)

    async def sweep(self, ts: int, notices: Notices | None = None, limit: int = 500) -> list[str]:
        """Write the emails for closures journaled after the cursor; return
        their NTF ids. None while another caller holds the cursor."""
        s, j = self._s, self._j
        written: list[str] = []
        async with self._pool.acquire() as conn, conn.transaction():
            after = await conn.fetchval(
                f"SELECT seq FROM {s}.break_cursor WHERE id = 1 FOR UPDATE SKIP LOCKED"
            )
            if after is None:
                return written
            if await conn.fetchval("SELECT to_regclass($1)", f"{j}.journal") is None:
                return written
            rows = await conn.fetch(
                f"SELECT seq, events FROM {j}.journal WHERE seq > $1 ORDER BY seq LIMIT $2",
                after,
                limit,
            )
            for row in rows:
                events = CODEC.decode(row["events"])
                assert isinstance(events, tuple)
                for closure in closures(events):
                    customer = await conn.fetchrow(
                        f"SELECT c.cif, c.email, c.full_name FROM {s}.accounts a "
                        f"JOIN {s}.cif c USING (cif) WHERE a.acc = $1",
                        closure.account_id,
                    )
                    if customer is None:
                        log.warning("market break on %s, no customer record", closure.account_id)
                        continue
                    notice = break_notice(
                        customer["email"], customer["full_name"], closure, notices
                    )
                    detail = {"acc": closure.account_id, "journal_seq": row["seq"]}
                    written.append(
                        await insert_notice(conn, s, customer["cif"], notice, ts, detail)
                    )
            if rows:
                await conn.execute(
                    f"UPDATE {s}.break_cursor SET seq = $1 WHERE id = 1", rows[-1]["seq"]
                )
        return written
