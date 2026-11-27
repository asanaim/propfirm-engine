"""Behaviour detectors (build plan step 5, line 3; D-14.29).

Run by the API loop at most hourly. Each detector raises a high fraud
alert, which opens a case on the customer and so holds their payouts until
staff decide (two people confirm a void). One alert per finding: the alert
carries a key, and a key already raised on the account is not raised again.

Built:
- hedging across accounts: opposite positions on one symbol on two
  accounts of one customer, or of customers in one identity cluster
  (D-14.27), opened within 120 seconds of each other and open together for
  at least half of the shorter position's holding time; 3 such pairs in 30
  days on one pair of accounts. An open position counts as held until now.

Order of expected loss, and what waits (strategic review v3, D6):
- stale price needs the second price source (step 8);
- copy cluster is the trades link of D-14.27, with class C (D-14.28);
- account sharing needs device and sign in records the platform does not
  keep yet;
- gap trading, consistency gaming, request rate and payment risk follow.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.customers.clusters import Alerts

log = logging.getLogger(__name__)

DAY_MS = 86_400_000
SOURCE = "detector"
EXAMPLES = 5  # position pairs kept as evidence on an alert


@dataclass(frozen=True, slots=True)
class Leg:
    """One position, as the detectors see it."""

    acc: str
    cif: str
    position_id: str
    symbol: str
    side: str  # "buy" or "sell"
    opened: int  # ms UTC
    closed: int  # ms UTC; now for a position still open


@dataclass(frozen=True, slots=True)
class Hedge:
    a: Leg  # a.acc < b.acc
    b: Leg


def hedged(a: Leg, b: Leg, window_ms: int, overlap_percent: int) -> bool:
    """Whether two positions hedge each other: two accounts, one symbol,
    opposite sides, opened within `window_ms`, and open together for at
    least `overlap_percent` of the shorter one's holding time. Exact in
    integers: overlap * 100 >= percent * shorter."""
    if a.acc == b.acc or a.symbol != b.symbol or a.side == b.side:
        return False
    if abs(a.opened - b.opened) > window_ms:
        return False
    overlap = min(a.closed, b.closed) - max(a.opened, b.opened)
    shorter = min(a.closed - a.opened, b.closed - b.opened)
    return overlap > 0 and overlap * 100 >= overlap_percent * shorter


def hedges(
    legs: Iterable[Leg],
    group_of: Callable[[str], str],
    window_ms: int,
    overlap_percent: int,
) -> list[Hedge]:
    """Every hedged pair of positions on accounts of one group (a cluster,
    or the customer when in none), in a fixed order."""
    by_key: dict[tuple[str, str], list[Leg]] = defaultdict(list)
    for leg in legs:
        by_key[(group_of(leg.cif), leg.symbol)].append(leg)
    out: list[Hedge] = []
    for key in sorted(by_key):
        ordered = sorted(by_key[key], key=lambda x: (x.opened, x.acc, x.position_id))
        for i, x in enumerate(ordered):
            for y in ordered[i + 1 :]:
                if y.opened - x.opened > window_ms:
                    break  # sorted by opening: no later leg is in the window
                if hedged(x, y, window_ms, overlap_percent):
                    a, b = (x, y) if x.acc < y.acc else (y, x)
                    out.append(Hedge(a, b))
    return out


@dataclass(frozen=True, slots=True)
class Finding:
    """Hedged pairs between two accounts."""

    a_acc: str
    a_cif: str
    b_acc: str
    b_cif: str
    pairs: tuple[Hedge, ...]

    def detail(self, acc: str) -> dict[str, Any]:
        """The alert detail as seen from one of the two accounts."""
        mine_is_a = acc == self.a_acc
        other_acc = self.b_acc if mine_is_a else self.a_acc
        other_cif = self.b_cif if mine_is_a else self.a_cif
        return {
            "key": f"hedging:{other_acc}",
            "detector": "hedging",
            "with_account": other_acc,
            "with_customer": other_cif,
            "pairs": len(self.pairs),
            "symbols": sorted({h.a.symbol for h in self.pairs}),
            "examples": [
                {
                    "symbol": h.a.symbol,
                    "positions": [h.a.position_id, h.b.position_id]
                    if mine_is_a
                    else [h.b.position_id, h.a.position_id],
                    "opened": [h.a.opened, h.b.opened] if mine_is_a else [h.b.opened, h.a.opened],
                }
                for h in self.pairs[:EXAMPLES]
            ],
        }


def findings(pairs: Iterable[Hedge], matches: int) -> list[Finding]:
    """Account pairs with at least `matches` hedged pairs of positions."""
    by_accounts: dict[tuple[str, str], list[Hedge]] = defaultdict(list)
    for h in pairs:
        by_accounts[(h.a.acc, h.b.acc)].append(h)
    out = []
    for (a, b), found in sorted(by_accounts.items()):
        if len(found) >= matches:
            first = found[0]
            out.append(Finding(a, first.a.cif, b, first.b.cif, tuple(found)))
    return out


def legs_from(rows: Iterable[Mapping[str, Any]], cif_of: Mapping[str, str], now: int) -> list[Leg]:
    """Positions from projected PositionOpened and PositionClosed events;
    a position is closed by the close that leaves no volume."""
    opened: dict[tuple[str, str], Mapping[str, Any]] = {}
    closed: dict[tuple[str, str], int] = {}
    for e in rows:
        key = (str(e["account_id"]), str(e["position_id"]))
        if e["type"] == "PositionOpened":
            opened[key] = e
        elif e["type"] == "PositionClosed" and int(e["remaining_volume"]) == 0:
            closed[key] = int(e["ts"])
    out = []
    for (acc, pid), e in sorted(opened.items()):
        if acc not in cif_of:
            continue
        out.append(
            Leg(
                acc,
                cif_of[acc],
                pid,
                str(e["symbol"]),
                str(e["side"]),
                int(e["ts"]),
                closed.get((acc, pid), now),
            )
        )
    return out


class ClusterMap(Protocol):
    async def map(self) -> dict[str, str]: ...


class Detectors:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        policy: Any,
        clusters: ClusterMap,
        alerts: Alerts,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._policy = policy
        self._clusters = clusters
        self._alerts = alerts
        self._last = -1

    async def _legs(self, since: int, now: int) -> list[Leg]:
        """Positions opened since `since`, with their closes."""
        rows = await self._pool.fetch(
            f"SELECT e.event::text AS event FROM {self._r}.account_events e "
            f"WHERE e.kind IN ('PositionOpened', 'PositionClosed') AND e.ts >= $1 "
            f"AND e.account_id IN "
            f"(SELECT account_id FROM {self._r}.account_events WHERE kind = 'PositionOpened' "
            f"AND ts >= $1) ORDER BY e.seq, e.idx",
            since,
        )
        accounts = await self._pool.fetch(f"SELECT acc, cif FROM {self._s}.accounts")
        cif_of = {str(a["acc"]): str(a["cif"]) for a in accounts}
        events = [json.loads(r["event"]) for r in rows]
        return [x for x in legs_from(events, cif_of, now) if x.opened >= since]

    async def hedging(self, now: int) -> list[Finding]:
        policy = (await self._policy.current()).detectors
        legs = await self._legs(now - policy.hedging_days * DAY_MS, now)
        groups = await self._clusters.map()
        pairs = hedges(
            legs,
            lambda cif: groups.get(cif, cif),
            policy.hedging_window_seconds * 1000,
            policy.hedging_overlap_percent,
        )
        return findings(pairs, policy.hedging_matches)

    async def _raised(self, acc: str, key: str) -> bool:
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.alerts WHERE acc = $1 AND source = $2 "
            f"AND detail->>'key' = $3 LIMIT 1",
            acc,
            SOURCE,
            key,
        )
        return found is not None

    async def run(self, now: int, every_ms: int = 3_600_000) -> int:
        """Run every detector switched on; return how many alerts were raised."""
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        policy = (await self._policy.current()).detectors
        raised = 0
        if policy.hedging:
            for f in await self.hedging(now):
                for acc, cif in ((f.a_acc, f.a_cif), (f.b_acc, f.b_cif)):
                    detail = f.detail(acc)
                    if await self._raised(acc, detail["key"]):
                        continue
                    await self._alerts.alert(cif, acc, SOURCE, "fraud", "high", detail, now)
                    raised += 1
        return raised
