"""Cohort alarm (build plan step 5, line 5; D-14.33; strategic review v3,
D3 and D6).

A cohort's pass rate cannot honestly exceed the rate a trader with zero
edge would reach (the zero edge bound, per product, from the pricing
study). When a cohort's resolved results sit above that bound at the 1 %
level of an exact one sided binomial test, the cohort is alarmed:
- payouts of its funded accounts are held (the payout check "cohort"
  fails) until staff clear the alarm;
- for an affiliate's cohort, the affiliate's commission stops accruing
  (orders left out accrue once cleared) and an alert opens a case on the
  affiliate, where two people may terminate them;
- the risk desk is told.

Cohorts, one result per order:
- product: the product's orders bought in one calendar month (UTC);
- affiliate: one affiliate's referred orders of one product, all time.

Results:
- a challenge passes when its last evaluation stage passed, and fails when
  one of its evaluation accounts was breached or expired;
- an Instant account succeeds when its trailing floor locked at the
  starting balance, and fails when it was breached before that;
- anything still running, or voided, is not a result.

A cleared cohort alarms again only once it has `min_resolved` more
results than when it was last alarmed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any, Protocol

import asyncpg

from propfirm.customers.clusters import Alerts

log = logging.getLogger(__name__)

SOURCE = "cohort"


def binomial_tail_at_most(n: int, k: int, p: Fraction, alpha: Fraction) -> bool:
    """Whether P(X >= k) <= alpha for X ~ Binomial(n, p). Exact in
    integers: with p = a/b, compare sum C(n,i) a^i (b-a)^(n-i) against
    alpha * b^n."""
    if not 0 < p < 1:
        raise ValueError("p strictly between 0 and 1")
    if k <= 0:
        return alpha >= 1
    if k > n:
        return True  # an impossible tail has probability 0
    a, b = p.numerator, p.denominator
    c = b - a
    term = 1  # C(n,i) a^i c^(n-i), from i = n downwards
    for _ in range(n):
        term *= a
    tail = term
    for i in range(n, k, -1):  # term(i-1) = term(i) * i * c / ((n-i+1) * a)
        term = term * i * c // ((n - i + 1) * a)
        tail += term
    return bool(tail * alpha.denominator <= alpha.numerator * b**n)


@dataclass(frozen=True, slots=True)
class Tally:
    kind: str  # "product" or "affiliate"
    key: str  # "lite:2026-11" or "CIF00000007:lite"
    product_id: str
    resolved: int
    passed: int
    bound: Fraction

    @property
    def rate(self) -> Fraction:
        return Fraction(self.passed, self.resolved) if self.resolved else Fraction(0)


def alarmed(t: Tally, alpha: Fraction, min_resolved: int) -> bool:
    """Above the bound, with enough results, and significant."""
    return (
        t.resolved >= min_resolved
        and t.rate > t.bound
        and binomial_tail_at_most(t.resolved, t.passed, t.bound, alpha)
    )


@dataclass(frozen=True, slots=True)
class Result:
    ord: str
    product_id: str
    bought_at: int  # ms UTC
    referrer: str | None
    passed: bool


def month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts // 1000, tz=UTC).strftime("%Y-%m")


def tallies(results: Iterable[Result], bounds: Mapping[str, Fraction]) -> list[Tally]:
    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        if r.product_id not in bounds:
            continue
        keys = [("product", f"{r.product_id}:{month_of(r.bought_at)}")]
        if r.referrer is not None:
            keys.append(("affiliate", f"{r.referrer}:{r.product_id}"))
        for kind, key in keys:
            c = counts[(kind, key, r.product_id)]
            c[0] += 1
            c[1] += r.passed
    return [
        Tally(kind, key, product, n, k, bounds[product])
        for (kind, key, product), (n, k) in sorted(counts.items())
    ]


def last_evaluation_stage(product: Any) -> str | None:
    """The stage whose pass completes the challenge; None for a product
    funded from purchase."""
    stages = [r.stage.value for r in product.rule_sets if r.stage.value != "funded"]
    return stages[-1] if stages else None


def outcome(
    accounts: Iterable[Mapping[str, Any]], instant: bool, last_stage: str | None
) -> bool | None:
    """An order's result from its accounts' first events: True passed,
    False failed, None not resolved (running, or voided)."""
    rows = list(accounts)
    if any(a["voided"] for a in rows):
        return None
    if instant:
        locked = min((a["locked_at"] for a in rows if a["locked_at"] is not None), default=None)
        failed = min((a["failed_at"] for a in rows if a["failed_at"] is not None), default=None)
        if locked is not None and (failed is None or locked <= failed):
            return True
        return False if failed is not None else None
    if any(a["stage"] == last_stage and a["passed_at"] is not None for a in rows):
        return True
    if any(a["stage"] != "funded" and a["failed_at"] is not None for a in rows):
        return False
    return None


class Notifier(Protocol):
    async def notify(
        self,
        audience: str,
        kind: str,
        ref: str,
        title: str,
        body: str,
        ts: int,
        urgent: bool = False,
    ) -> int: ...


class Cohorts:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        policy: Any,
        alerts: Alerts,
        notifier: Notifier | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._policy = policy
        self._alerts = alerts
        self._notifier = notifier
        self._last = -1

    async def results(self) -> list[Result]:
        """One result per resolved order (see the module text)."""
        s, r = self._s, self._r
        current = await self._policy.current()
        instant = set(current.cohorts.instant_products)
        last_stage = {p.id: last_evaluation_stage(p) for p in current.products}
        rows = await self._pool.fetch(
            f"SELECT o.ord, o.product_id, o.created_at, ref.referrer_cif, a.acc, a.stage, "
            f"min(e.ts) FILTER (WHERE e.kind = 'StagePassed') AS passed_at, "
            f"min(e.ts) FILTER (WHERE e.kind IN ('AccountBreached', 'AccountExpired')) "
            f"  AS failed_at, "
            f"bool_or(e.kind = 'AccountVoided') AS voided, "
            f"min(e.ts) FILTER (WHERE e.kind IN ('DayStarted', 'AccountBreached') AND "
            f"  (e.event->>'max_floor_cents')::bigint >= a.size_cents) AS locked_at "
            f"FROM {s}.orders o JOIN {s}.accounts a ON a.ord = o.ord "
            f"LEFT JOIN {r}.account_events e ON e.account_id = a.acc AND e.kind IN "
            f"  ('StagePassed', 'AccountBreached', 'AccountExpired', 'AccountVoided', "
            f"  'DayStarted') "
            f"LEFT JOIN {s}.referrals ref ON ref.cif = o.cif "
            f"WHERE o.rsv_id NOT LIKE '%-trial' "
            f"GROUP BY o.ord, o.product_id, o.created_at, ref.referrer_cif, a.acc, a.stage "
            f"ORDER BY o.ord, a.acc"
        )
        by_order: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            by_order[str(row["ord"])].append(dict(row))
        out = []
        for ord_id, accounts in by_order.items():
            first = accounts[0]
            product = str(first["product_id"])
            passed = outcome(accounts, product in instant, last_stage.get(product))
            if passed is None:
                continue
            referrer = None if first["referrer_cif"] is None else str(first["referrer_cif"])
            out.append(Result(ord_id, product, int(first["created_at"]), referrer, passed))
        return out

    async def policy(self) -> Any:
        return (await self._policy.current()).cohorts

    async def tallies(self) -> list[Tally]:
        policy = (await self._policy.current()).cohorts
        return tallies(await self.results(), policy.bounds)

    async def alarms(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT a.id, a.kind, a.key, a.product_id, a.resolved, a.passed, a.bound, "
            f"a.raised_at, c.cleared_at, c.cleared_by, c.reason "
            f"FROM {self._s}.cohort_alarms a "
            f"LEFT JOIN {self._s}.cohort_clears c ON c.alarm = a.id ORDER BY a.id DESC"
        )
        return [dict(r) for r in rows]

    async def watch(self, now: int) -> list[Tally]:
        """Alarm every cohort newly above its bound; return them."""
        policy = (await self._policy.current()).cohorts
        if not policy.on:
            return []
        if self._last >= 0 and now - self._last < policy.every_hours * 3_600_000:
            return []
        self._last = now
        raised = []
        s = self._s
        for t in await self.tallies():
            if not alarmed(t, policy.significance, policy.min_resolved):
                continue
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{s}.cohorts")
                last = await conn.fetchrow(
                    f"SELECT a.resolved, c.alarm IS NOT NULL AS cleared "
                    f"FROM {s}.cohort_alarms a LEFT JOIN {s}.cohort_clears c ON c.alarm = a.id "
                    f"WHERE a.kind = $1 AND a.key = $2 ORDER BY a.id DESC LIMIT 1",
                    t.kind,
                    t.key,
                )
                if last is not None and (
                    not last["cleared"] or t.resolved < last["resolved"] + policy.min_resolved
                ):
                    continue  # still open, or cleared on much the same results
                await conn.execute(
                    f"INSERT INTO {s}.cohort_alarms (kind, key, product_id, resolved, passed, "
                    f"bound, raised_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    t.kind,
                    t.key,
                    t.product_id,
                    t.resolved,
                    t.passed,
                    f"{t.bound.numerator}/{t.bound.denominator}",
                    now,
                )
            raised.append(t)
            await self._tell(t, now)
        return raised

    async def _tell(self, t: Tally, now: int) -> None:
        rate = f"{t.passed} of {t.resolved} ({percent(t.rate)})"
        if t.kind == "affiliate":
            referrer = t.key.split(":", 1)[0]
            await self._alerts.alert(
                referrer,
                None,
                SOURCE,
                "fraud",
                "high",
                {
                    "key": f"cohort:{t.key}",
                    "detector": "cohort",
                    "product_id": t.product_id,
                    "resolved": t.resolved,
                    "passed": t.passed,
                    "bound": f"{t.bound.numerator}/{t.bound.denominator}",
                },
                now,
            )
        if self._notifier is not None:
            await self._notifier.notify(
                "settings.change",
                "cohort",
                f"COHORT:{t.kind}:{t.key}",
                "Cohort pass rate above the zero edge bound",
                f"{t.kind} cohort {t.key}: {rate} passed against a bound of "
                f"{percent(t.bound)}. Its payouts are held"
                + (" and the affiliate's commission is paused" if t.kind == "affiliate" else "")
                + " until staff clear the alarm.",
                now,
                urgent=True,
            )

    async def clear(self, alarm: int, by: str, reason: str, now: int) -> bool:
        """Clear an open alarm; False when there is none open by that id."""
        done = await self._pool.fetchval(
            f"INSERT INTO {self._s}.cohort_clears (alarm, cleared_at, cleared_by, reason) "
            f"SELECT id, $2, $3, $4 FROM {self._s}.cohort_alarms WHERE id = $1 "
            f"ON CONFLICT (alarm) DO NOTHING RETURNING alarm",
            alarm,
            now,
            by,
            reason,
        )
        return done is not None

    async def held(self, acc: str) -> bool:
        """Whether the account's payouts are held by an open alarm on a
        cohort its order belongs to."""
        s = self._s
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {s}.accounts a JOIN {s}.orders o ON o.ord = a.ord "
            f"LEFT JOIN {s}.referrals ref ON ref.cif = o.cif "
            f"JOIN {s}.cohort_alarms al ON al.product_id = o.product_id AND ("
            f"  (al.kind = 'product' AND al.key = o.product_id || ':' || "
            f"   to_char(to_timestamp(o.created_at / 1000.0) AT TIME ZONE 'UTC', 'YYYY-MM')) "
            f"  OR (al.kind = 'affiliate' AND al.key = ref.referrer_cif || ':' || o.product_id)) "
            f"WHERE a.acc = $1 AND NOT EXISTS "
            f"(SELECT 1 FROM {s}.cohort_clears c WHERE c.alarm = al.id) LIMIT 1",
            acc,
        )
        return found is not None


def percent(x: Fraction) -> str:
    """Rounded down to one decimal."""
    tenths = x * 1000
    whole = tenths.numerator // tenths.denominator
    return f"{whole // 10}.{whole % 10} %"
