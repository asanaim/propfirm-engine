"""Behaviour detectors (build plan step 5, line 3; D-14.29).

Run by the API loop at most hourly. Each finding raises an alert, which
opens a case on the customer and so holds their payouts until staff decide
(two people confirm a void or a profit removal). One alert per finding: the alert
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
- request rate and payment risk follow.

Also built (D-14.30):
- gap trading (low integrity alert per position, profitable ones only):
  opened at most 2 hours before a long market break and held through it,
  or opened within 5 minutes of a listed release affecting the symbol at
  above twice the account's median volume;
- consistency gaming (medium integrity alert, one a month per account):
  the same trade (account, symbol, side) closed before the 17:00 New York
  boundary and opened again within 60 seconds after it, 3 times in 30
  days. The payout's consistency check merges the two days of every such
  re-entry, whatever the count (`payouts.merged_day_pnl`).
"""

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any, Protocol

import asyncpg

from propfirm.customers.clusters import Alerts
from propfirm.market.symbols import SymbolSpec
from propfirm.rules.clock import NEW_YORK_17
from propfirm.rules.news import RestrictedEvent, affected_symbols

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
    volume: int = 0  # hundredths of a lot, as opened
    result_cents: int | None = None  # realized profit; None while open


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
    """Positions from projected PositionOpened and PositionClosed events
    (any other event is skipped); a position is closed by the close that
    leaves no volume."""
    opened: dict[tuple[str, str], Mapping[str, Any]] = {}
    closed: dict[tuple[str, str], int] = {}
    pnl: dict[tuple[str, str], int] = defaultdict(int)
    for e in rows:
        if e.get("type") not in ("PositionOpened", "PositionClosed"):
            continue
        key = (str(e["account_id"]), str(e["position_id"]))
        if e["type"] == "PositionOpened":
            opened[key] = e
        elif e["type"] == "PositionClosed":
            pnl[key] += int(e.get("pnl_cents", 0))
            if int(e["remaining_volume"]) == 0:
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
                int(e.get("volume", 0)),
                pnl[(acc, pid)] if (acc, pid) in closed else None,
            )
        )
    return out


# --- consistency gaming ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reentry:
    """A position closed before a trading day boundary and the same trade
    (account, symbol, side) opened again just after it."""

    first: Leg
    second: Leg
    boundary: int  # ms UTC

    def days(self, day_of: Callable[[int], int]) -> tuple[int, int]:
        """The trading days the two results fall on (each on its close)."""
        return day_of(self.first.closed), day_of(self.second.closed)


def reentries(
    legs: Iterable[Leg], next_boundary: Callable[[int], int], window_ms: int
) -> list[Reentry]:
    """Every re-entry: the first position closed, a boundary at or before
    the second's opening, and the second opened within `window_ms` of the
    first's close. Ordered by the second's opening."""
    by_key: dict[tuple[str, str, str], list[Leg]] = defaultdict(list)
    for leg in legs:
        by_key[(leg.acc, leg.symbol, leg.side)].append(leg)
    out = []
    for key in sorted(by_key):
        ordered = sorted(by_key[key], key=lambda x: (x.opened, x.position_id))
        for first in ordered:
            if first.result_cents is None:
                continue  # still open
            boundary = next_boundary(first.closed)
            for second in ordered:
                if second is first or second.opened < boundary:
                    continue
                if second.opened - first.closed > window_ms:
                    break
                out.append(Reentry(first, second, boundary))
                break  # the first re-entry after this close
    return sorted(out, key=lambda r: (r.second.opened, r.second.acc, r.second.position_id))


# --- gap trading -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gap:
    """A profitable position opened just before a long break and held
    through it, or opened near a release at an outsized volume."""

    leg: Leg
    reason: str  # "break" or "release"
    at: int  # the break's close, or the release time (ms UTC)
    median_volume: Fraction | None = None  # the account's, for a release


class Hours(Protocol):
    def next_long_break(self, ts: int) -> tuple[int, int] | None: ...


def median(values: Iterable[int]) -> Fraction:
    v = sorted(values)
    if not v:
        raise ValueError("no values")
    n = len(v)
    return Fraction(v[n // 2]) if n % 2 else Fraction(v[n // 2 - 1] + v[n // 2], 2)


def gaps(
    legs: Iterable[Leg],
    hours_of: Callable[[str], Hours | None],
    releases: Iterable[tuple[int, frozenset[str]]],
    before_break_ms: int,
    release_ms: int,
    size_multiple: Fraction,
) -> list[Gap]:
    """Profitable closed positions that gap trade:
    - break: opened at most `before_break_ms` before a long break and
      closed at or after the market reopened;
    - release: opened within `release_ms` of a release affecting its
      symbol (either side, inclusive), at a volume above `size_multiple`
      times the account's median volume (all its positions given).
    A position flagged for a break is not flagged again for a release."""
    legs = list(legs)
    volumes: dict[str, list[int]] = defaultdict(list)
    for leg in legs:
        volumes[leg.acc].append(leg.volume)
    medians = {acc: median(v) for acc, v in volumes.items()}
    events = sorted(releases)
    out = []
    for leg in sorted(legs, key=lambda x: (x.acc, x.opened, x.position_id)):
        if leg.result_cents is None or leg.result_cents <= 0:
            continue
        hours = hours_of(leg.symbol)
        span = hours.next_long_break(leg.opened) if hours is not None else None
        if span is not None and span[0] - leg.opened <= before_break_ms and leg.closed >= span[1]:
            out.append(Gap(leg, "break", span[0]))
            continue
        m = medians[leg.acc]
        if leg.volume <= size_multiple * m:
            continue
        for ts, symbols in events:
            if leg.symbol in symbols and abs(leg.opened - ts) <= release_ms:
                out.append(Gap(leg, "release", ts, m))
                break
    return out


class ClusterMap(Protocol):
    async def map(self) -> dict[str, str]: ...


def release_times(
    events: Iterable[RestrictedEvent], symbols: Iterable[SymbolSpec]
) -> list[tuple[int, frozenset[str]]]:
    """Each release with the symbols it affects (the news rule's map)."""
    specs = tuple(symbols)
    out = []
    for e in events:
        affected = affected_symbols(e, specs)
        if affected:
            out.append((e.ts, affected))
    return sorted(out, key=lambda x: (x[0], sorted(x[1])))


def month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts // 1000, tz=UTC).strftime("%Y-%m")


class Detectors:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        policy: Any,
        clusters: ClusterMap,
        alerts: Alerts,
        events: Iterable[RestrictedEvent] = (),
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._policy = policy
        self._clusters = clusters
        self._alerts = alerts
        self._events = tuple(events)  # the listed releases (the news calendars)
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

    async def hedging(self, now: int, legs: list[Leg] | None = None) -> list[Finding]:
        policy = (await self._policy.current()).detectors
        since = now - policy.hedging_days * DAY_MS
        legs = await self._legs(since, now) if legs is None else legs
        groups = await self._clusters.map()
        pairs = hedges(
            [x for x in legs if x.opened >= since],
            lambda cif: groups.get(cif, cif),
            policy.hedging_window_seconds * 1000,
            policy.hedging_overlap_percent,
        )
        return findings(pairs, policy.hedging_matches)

    async def gaps(self, now: int, legs: list[Leg] | None = None) -> list[Gap]:
        current = await self._policy.current()
        policy = current.detectors
        since = now - policy.gap_days * DAY_MS
        legs = await self._legs(since, now) if legs is None else legs
        instruments = current.trading.instruments
        hours = {i.symbol.name: i.hours for i in instruments}
        return gaps(
            [x for x in legs if x.opened >= since],
            hours.get,
            release_times(self._events, (i.symbol for i in instruments)),
            policy.gap_before_break_minutes * 60_000,
            policy.gap_release_minutes * 60_000,
            policy.gap_size_multiple,
        )

    async def reentries(self, now: int, legs: list[Leg] | None = None) -> dict[str, list[Reentry]]:
        """Each account's re-entries in the window, when they reach the count."""
        policy = (await self._policy.current()).detectors
        since = now - policy.reentry_days * DAY_MS
        legs = await self._legs(since, now) if legs is None else legs
        found: dict[str, list[Reentry]] = defaultdict(list)
        for r in reentries(
            [x for x in legs if x.opened >= since],
            NEW_YORK_17.next_boundary,
            policy.reentry_seconds * 1000,
        ):
            found[r.first.acc].append(r)
        return {acc: rs for acc, rs in found.items() if len(rs) >= policy.reentry_matches}

    async def _raised(self, acc: str, key: str) -> bool:
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.alerts WHERE acc = $1 AND source = $2 "
            f"AND detail->>'key' = $3 LIMIT 1",
            acc,
            SOURCE,
            key,
        )
        return found is not None

    async def _raise(
        self, cif: str, acc: str, kind: str, severity: str, detail: dict[str, Any], now: int
    ) -> int:
        if await self._raised(acc, detail["key"]):
            return 0
        await self._alerts.alert(cif, acc, SOURCE, kind, severity, detail, now)
        return 1

    async def run(self, now: int, every_ms: int = 3_600_000) -> int:
        """Run every detector switched on; return how many alerts were raised."""
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        policy = (await self._policy.current()).detectors
        days = [
            d
            for on, d in (
                (policy.hedging, policy.hedging_days),
                (policy.gap, policy.gap_days),
                (policy.reentry, policy.reentry_days),
            )
            if on
        ]
        if not days:
            return 0
        legs = await self._legs(now - max(days) * DAY_MS, now)
        raised = 0
        if policy.hedging:
            for f in await self.hedging(now, legs):
                for acc, cif in ((f.a_acc, f.a_cif), (f.b_acc, f.b_cif)):
                    raised += await self._raise(cif, acc, "fraud", "high", f.detail(acc), now)
        if policy.gap:
            for g in await self.gaps(now, legs):
                detail = {
                    "key": f"gap:{g.leg.position_id}",
                    "detector": "gap_trading",
                    "reason": g.reason,
                    "at": g.at,
                    "position": g.leg.position_id,
                    "symbol": g.leg.symbol,
                    "side": g.leg.side,
                    "volume": g.leg.volume,
                    "opened": g.leg.opened,
                    "closed": g.leg.closed,
                    "result_cents": g.leg.result_cents,
                }
                if g.median_volume is not None:
                    detail["median_volume"] = str(g.median_volume)
                raised += await self._raise(g.leg.cif, g.leg.acc, "integrity", "low", detail, now)
        if policy.reentry:
            for acc, found in sorted((await self.reentries(now, legs)).items()):
                last = found[-1]
                detail = {
                    "key": f"reentry:{month_of(last.second.opened)}",
                    "detector": "consistency_gaming",
                    "reentries": len(found),
                    "examples": [
                        {
                            "symbol": r.first.symbol,
                            "side": r.first.side,
                            "positions": [r.first.position_id, r.second.position_id],
                            "closed": r.first.closed,
                            "reopened": r.second.opened,
                        }
                        for r in found[-EXAMPLES:]
                    ],
                }
                raised += await self._raise(last.first.cif, acc, "integrity", "medium", detail, now)
        return raised
