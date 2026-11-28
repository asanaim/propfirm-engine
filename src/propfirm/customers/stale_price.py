"""Stale price detector (build plan step 5, line 3; D-14.36; strategic
review v3, D6).

Every minute the API loop checks the client fills of the last minutes
against the reference (second) price source (`propfirm.service.reference`):
- a market order's open: a buy pays the ask, a sell gets the bid;
- a close the trader asked for: a buy's close gets the bid, a sell's pays
  the ask.
Stops, limits, take profits and forced closes are left out: the engine
fills those at its own levels.

The reference quote is the last one at or before the fill, at most 2
seconds older. The trader's advantage is how much better the fill was than
that quote's same side, in points. A fill is stale when the advantage is
more than one reference spread (`stale_spreads`). Each stale fill is kept
as evidence (`stale_fills`), with the reference quote it was measured
against.

Five stale fills on one account in 30 days: a high fraud alert, which
opens a case and holds payouts, and class C (lot caps halved through the
engine, D-14.34), set by the system so the daily job does not lift it.
Two people decide a void or a profit removal on the case.

A fill older than the reference window (10 minutes) cannot be checked;
it is counted in the logs, not guessed.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.customers.clusters import Alerts

log = logging.getLogger(__name__)

SOURCE = "detector"
DAY_MS = 86_400_000
SETTLE_MS = 5_000  # fills younger than this wait for the reference store
WINDOW_MS = 600_000  # the reference store keeps this much


@dataclass(frozen=True, slots=True)
class Fill:
    acc: str
    position_id: str
    kind: str  # "open" or "close"
    ts: int
    symbol: str
    pays_ask: bool  # the trader bought at this fill (else sold)
    price: int  # points


def fills_from(events: Iterable[Mapping[str, Any]]) -> list[Fill]:
    """The fills the trader chose the time of: market opens and client
    closes (any other event is skipped)."""
    out = []
    for e in events:
        kind = e.get("type")
        if kind == "PositionOpened" and e.get("order_type") == "market":
            out.append(
                Fill(
                    str(e["account_id"]),
                    str(e["position_id"]),
                    "open",
                    int(e["ts"]),
                    str(e["symbol"]),
                    e["side"] == "buy",
                    int(e["price"]),
                )
            )
        elif kind == "PositionClosed" and e.get("reason") == "client":
            out.append(
                Fill(
                    str(e["account_id"]),
                    str(e["position_id"]),
                    "close",
                    int(e["ts"]),
                    str(e["symbol"]),
                    e["side"] == "sell",  # closing a sell buys it back
                    int(e["close_price"]),
                )
            )
    return out


def advantage(fill: Fill, bid: int, ask: int) -> int:
    """Points the fill was better for the trader than the reference."""
    return ask - fill.price if fill.pays_ask else fill.price - bid


def stale(fill: Fill, bid: int, ask: int, spreads: int) -> bool:
    """Better than the reference by more than `spreads` reference spreads."""
    return advantage(fill, bid, ask) > spreads * (ask - bid)


class Reference(Protocol):
    async def at(self, symbol: str, ts: int, max_age_ms: int) -> tuple[int, int, int] | None: ...


class ClassRecords(Protocol):
    async def account_class(self, acc: str) -> tuple[str, str, int] | None: ...

    async def set_account_class(
        self, acc: str, cls: str, reason: str, by: str, ts: int
    ) -> None: ...


class StalePrice:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        policy: Any,
        reference: Reference | None,
        alerts: Alerts,
        classes: ClassRecords,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._policy = policy
        self._reference = reference
        self._alerts = alerts
        self._classes = classes
        self._cursor: int | None = None
        self._last = -1

    async def _fills(self, after: int, until: int) -> list[Fill]:
        rows = await self._pool.fetch(
            f"SELECT event::text AS event FROM {self._r}.account_events "
            f"WHERE kind IN ('PositionOpened', 'PositionClosed') AND ts > $1 AND ts <= $2 "
            f"ORDER BY seq, idx",
            after,
            until,
        )
        return fills_from(json.loads(r["event"]) for r in rows)

    async def check(self, now: int, every_ms: int = 60_000) -> int:
        """Check the fills since the last run; return how many were stale."""
        policy = (await self._policy.current()).detectors
        if not policy.stale or self._reference is None:
            return 0
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        until = now - SETTLE_MS
        after = self._cursor if self._cursor is not None else until - WINDOW_MS
        after = max(after, now - WINDOW_MS)
        found: list[tuple[Fill, tuple[int, int, int]]] = []
        unchecked = 0
        for fill in await self._fills(after, until):
            quote = await self._reference.at(fill.symbol, fill.ts, policy.stale_quote_age_ms)
            if quote is None:
                unchecked += 1
                continue
            _, bid, ask = quote
            if stale(fill, bid, ask, policy.stale_spreads):
                found.append((fill, quote))
        self._cursor = until
        if unchecked:
            log.info("stale price: %d fills had no reference quote", unchecked)
        accounts = set()
        for fill, (ref_ts, bid, ask) in found:
            inserted = await self._pool.fetchval(
                f"INSERT INTO {self._s}.stale_fills (acc, position_id, kind, ts, symbol, "
                f"price, ref_ts, ref_bid, ref_ask, advantage) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                f"ON CONFLICT DO NOTHING RETURNING 1",
                fill.acc,
                fill.position_id,
                fill.kind,
                fill.ts,
                fill.symbol,
                fill.price,
                ref_ts,
                bid,
                ask,
                advantage(fill, bid, ask),
            )
            if inserted is not None:
                accounts.add(fill.acc)
        for acc in sorted(accounts):
            await self._act(acc, now, policy)
        return len(found)

    async def _act(self, acc: str, now: int, policy: Any) -> None:
        since = now - policy.stale_days * DAY_MS
        count = await self._pool.fetchval(
            f"SELECT count(*) FROM {self._s}.stale_fills WHERE acc = $1 AND ts >= $2", acc, since
        )
        if int(count) < policy.stale_fills:
            return
        raised = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.alerts WHERE acc = $1 AND source = $2 "
            f"AND detail->>'detector' = 'stale_price' AND ts >= $3 LIMIT 1",
            acc,
            SOURCE,
            since,
        )
        if raised is not None:
            return  # the case already holds the account
        cif = await self._pool.fetchval(f"SELECT cif FROM {self._s}.accounts WHERE acc = $1", acc)
        if cif is None:
            return
        await self._alerts.alert(
            str(cif),
            acc,
            SOURCE,
            "fraud",
            "high",
            {
                "key": f"stale:{now}",
                "detector": "stale_price",
                "fills": int(count),
                "days": policy.stale_days,
            },
            now,
        )
        current = await self._classes.account_class(acc)
        if current is None or current[0] not in ("C", "D"):
            await self._classes.set_account_class(
                acc,
                "C",
                f"stale price: {int(count)} fills in {policy.stale_days} days",
                "system",
                now,
            )

    async def of(self, acc: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.stale_fills WHERE acc = $1 ORDER BY ts DESC LIMIT 100", acc
        )
        return [dict(r) for r in rows]
