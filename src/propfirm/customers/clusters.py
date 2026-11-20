"""Identity clusters (build plan step 5, line 1; D-14.27).

Customers who are, or act as, one person form a cluster. A cluster counts
as one trader wherever the firm caps or reviews by trader: the largest
claim one trader could make on the reserve (D-14.20) now, the allocation
cap and Instant limit later.

Links, each with its evidence, found by `link` (the API loop, at most
hourly):
- identity: a verified identity the same as another customer's (the
  duplicate identity alert raised at funding: same date of birth, same
  name);
- wallet: the same payout wallet address (EVM addresses compared without
  case); a new wallet link raises a medium fraud alert, which opens a
  case and so holds payouts until staff decide;
- trades: at least 5 positions opened within 2 seconds of one another on
  the same symbol, either side, in the last 30 days, on accounts of the
  two customers (copy trading or hedging between people).

Not yet: device and payment instrument links. The platform keeps no
device fingerprint, and hosted payment pages give it no card or payer
wallet; each comes with its source.

A cluster is the connected set of linked customers; its id is its lowest
CIF, so the same links always give the same clusters.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

log = logging.getLogger(__name__)

DAY_MS = 86_400_000
TRADE_WINDOW_MS = 2_000
TRADE_MATCHES = 5
TRADE_DAYS = 30
REASONS = ("identity", "wallet", "trades")


class Alerts(Protocol):
    async def alert(
        self,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        detail: dict[str, Any],
        ts: int,
    ) -> tuple[str, str]: ...


@dataclass(frozen=True, slots=True)
class Link:
    a_cif: str
    b_cif: str
    reason: str
    evidence: dict[str, Any]


def pair(x: str, y: str) -> tuple[str, str]:
    return (x, y) if x < y else (y, x)


def clusters_of(links: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each linked customer's cluster id (the lowest CIF of its connected
    set). Customers with no link are in no cluster."""
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        ra, rb = root(a), root(b)
        if ra != rb:
            low, high = sorted((ra, rb))
            parent[high] = low
    return {x: root(x) for x in parent}


class Clusters:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        alerts: Alerts | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._alerts = alerts
        self._last = -1

    # --- finding links ---------------------------------------------------------------

    async def _identity(self) -> list[Link]:
        rows = await self._pool.fetch(
            f"SELECT cif, detail FROM {self._s}.alerts WHERE source = 'duplicate_identity'"
        )
        out = []
        for r in rows:
            detail = json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]
            for other in detail.get("same_identity_as", []):
                if other != r["cif"]:
                    a, b = pair(str(r["cif"]), str(other))
                    out.append(Link(a, b, "identity", {"onboarding": detail.get("onboarding")}))
        return out

    async def _wallets(self) -> list[Link]:
        rows = await self._pool.fetch(
            f"SELECT lower(w1.address) AS address, w1.cif AS a, w2.cif AS b "
            f"FROM {self._s}.wallets w1 JOIN {self._s}.wallets w2 "
            f"ON lower(w1.address) = lower(w2.address) AND w1.network = w2.network "
            f"AND w1.cif < w2.cif GROUP BY 1, 2, 3"
        )
        return [Link(r["a"], r["b"], "wallet", {"address": r["address"]}) for r in rows]

    async def _trades(self, now: int) -> list[Link]:
        r, s = self._r, self._s
        rows = await self._pool.fetch(
            f"WITH opened AS (SELECT e.account_id, e.seq, e.idx, e.ts, "
            f"e.event->>'symbol' AS symbol, x.cif FROM {r}.account_events e "
            f"JOIN {s}.accounts x ON x.acc = e.account_id "
            f"WHERE e.kind = 'PositionOpened' AND e.ts >= $1) "
            f"SELECT a.cif AS a, b.cif AS b, count(DISTINCT (a.seq, a.idx)) AS matches, "
            f"array_agg(DISTINCT a.symbol) AS symbols FROM opened a JOIN opened b "
            f"ON a.symbol = b.symbol AND a.cif < b.cif AND abs(a.ts - b.ts) <= $2 "
            f"GROUP BY a.cif, b.cif HAVING count(DISTINCT (a.seq, a.idx)) >= $3",
            now - TRADE_DAYS * DAY_MS,
            TRADE_WINDOW_MS,
            TRADE_MATCHES,
        )
        return [
            Link(
                row["a"],
                row["b"],
                "trades",
                {"matches": int(row["matches"]), "symbols": sorted(row["symbols"])},
            )
            for row in rows
        ]

    async def link(self, now: int, every_ms: int = 3_600_000) -> int:
        """Find links and record them; return how many are new."""
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        found = await self._identity() + await self._wallets() + await self._trades(now)
        new: list[Link] = []
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            for link in found:
                inserted = await conn.fetchval(
                    f"INSERT INTO {s}.cluster_links (a_cif, b_cif, reason, evidence, "
                    f"first_seen, last_seen) VALUES ($1, $2, $3, $4::jsonb, $5, $5) "
                    f"ON CONFLICT (a_cif, b_cif, reason) DO UPDATE SET "
                    f"evidence = excluded.evidence, last_seen = excluded.last_seen "
                    f"RETURNING (xmax = 0)",
                    link.a_cif,
                    link.b_cif,
                    link.reason,
                    json.dumps(link.evidence, sort_keys=True),
                    now,
                )
                if inserted:
                    new.append(link)
        if self._alerts is not None:
            for link in new:
                if link.reason != "wallet":
                    continue  # identity has its own alert; trades go to the detectors
                for cif, other in ((link.a_cif, link.b_cif), (link.b_cif, link.a_cif)):
                    await self._alerts.alert(
                        cif,
                        None,
                        "cluster",
                        "fraud",
                        "medium",
                        {"key": f"wallet:{other}", "linked_to": other, **link.evidence},
                        now,
                    )
        return len(new)

    # --- reading ---------------------------------------------------------------------

    async def links(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT a_cif, b_cif, reason, evidence::text AS evidence, first_seen, last_seen "
            f"FROM {self._s}.cluster_links ORDER BY a_cif, b_cif, reason"
        )
        return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]

    async def map(self) -> dict[str, str]:
        """Each linked customer's cluster id."""
        return clusters_of((x["a_cif"], x["b_cif"]) for x in await self.links())

    async def clusters(self) -> list[dict[str, Any]]:
        """Every cluster: its members and the links between them, largest first."""
        links = await self.links()
        of = clusters_of((x["a_cif"], x["b_cif"]) for x in links)
        members: dict[str, list[str]] = {}
        for cif, cluster in of.items():
            members.setdefault(cluster, []).append(cif)
        out = [
            {
                "cluster": cluster,
                "members": sorted(cifs),
                "links": [x for x in links if of[x["a_cif"]] == cluster],
            }
            for cluster, cifs in members.items()
        ]
        return sorted(out, key=lambda c: (-len(c["members"]), c["cluster"]))

    async def cluster_of(self, cif: str) -> dict[str, Any] | None:
        for c in await self.clusters():
            if cif in c["members"]:
                return c
        return None
