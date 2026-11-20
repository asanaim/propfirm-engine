"""Daily trader classes (build plan step 5, line 2; D-14.28; strategic
review v3, D5). Every funded account is classed once a day from its own
closed trades and records; a change is recorded with its reasons and the
measures behind it, so the history explains every class.

- A, proven: at least `a_min_trading_days` trading days (UTC days with a
  close) and `a_min_trades` trades; the t statistic of daily results above
  `a_min_t_stat`; profit factor above `a_min_profit_factor`; at least
  `a_min_paid_payouts` payouts paid; under `a_max_short_percent` of profit
  from short holds; no case actioned.
- C, watch: any of: over `c_short_percent` of profit from short holds; a
  chargeback on the customer (any state but won); a trades link in an
  identity cluster (D-14.27).
- D, restricted: a case on the customer actioned within `d_case_days`.
- B, standard: everyone else.
D wins over C, C over A.

What a class does now:
- C and D: no scaling step is proposed or approved; the customer cannot
  buy Instant; a payout request raises a low integrity alert, so the
  payout waits for a case decision (reviewed by hand).
- A: no relaxation yet. Its permissions (payouts cleared automatically
  under a threshold, scaling first) widen the baseline, which needs a
  second person's approval (D10 of the review): a later decision.
Not yet: lot caps halved for C and close-only for D need an engine input
journaled with the risk policy version (line 6); markout and news-window
measures need the second price source and the release calendar join.

Records: every change goes to `account_class_changes` (D-14.5) with the
reason prefixed "daily: ", the reasons and the measures, set by "system".
A class set by staff or by the payout check stands; the job overrides it
only with a worse class on a fact newer than it (see `decide`).

Exact arithmetic: t > k is tested as mean > 0 and mean^2 * n > k^2 * var
(sample variance), and the profit factor as gross profit > k * gross loss,
in fractions, so no rounding decides a class.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any, Protocol

import asyncpg

from propfirm.analysis import Trade, short_hold, trades_from
from propfirm.policy import ClassesPolicy

DAY_MS = 86_400_000
CLASSES = ("A", "B", "C", "D")
WATCH = ("C", "D")


@dataclass(frozen=True, slots=True)
class Facts:
    """What a class is decided from, for one account. Times are when each
    fact arose, so a class set by hand gives way only to newer facts."""

    trades: tuple[Trade, ...]
    paid_payouts: int
    chargeback_at: int | None  # the latest chargeback not won
    trades_cluster_at: int | None  # the first trades link in an identity cluster
    actioned_case_at: int | None  # the latest case actioned on the customer


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d")


def t_above(daily: list[int], k: Fraction) -> bool:
    """Whether the t statistic of `daily` results is above `k`, exactly."""
    n = len(daily)
    if n < 2:
        return False
    mean = Fraction(sum(daily), n)
    if mean <= 0:
        return False
    var = sum((Fraction(x) - mean) ** 2 for x in daily) / (n - 1)
    if var == 0:
        return True  # every day the same gain: no doubt at all
    return mean * mean * n > k * k * var


def measures(facts: Facts, policy: ClassesPolicy) -> dict[str, Any]:
    trades = facts.trades
    daily: dict[str, int] = {}
    for t in trades:
        daily[_day(t.closed)] = daily.get(_day(t.closed), 0) + t.result_cents
    gain = sum(t.result_cents for t in trades if t.result_cents > 0)
    loss = -sum(t.result_cents for t in trades if t.result_cents < 0)
    short = short_hold(trades, policy.short_seconds)
    return {
        "trades": len(trades),
        "trading_days": len(daily),
        "gross_profit_cents": gain,
        "gross_loss_cents": loss,
        "t_above": t_above(list(daily.values()), policy.a_min_t_stat),
        "short_share_basis_points": short.share_basis_points,
        "paid_payouts": facts.paid_payouts,
    }


def classify(
    facts: Facts, policy: ClassesPolicy, now: int
) -> tuple[str, list[str], dict[str, Any]]:
    """The class, its reasons, and the measures behind it."""
    m = measures(facts, policy)
    short = short_hold(facts.trades, policy.short_seconds)
    if facts.actioned_case_at is not None and now - facts.actioned_case_at <= (
        policy.d_case_days * DAY_MS
    ):
        return "D", ["case_actioned"], m
    watch = []
    if short.over(policy.c_short_percent):
        watch.append("short_holds")
    if facts.chargeback_at is not None:
        watch.append("chargeback")
    if facts.trades_cluster_at is not None:
        watch.append("trades_cluster")
    if watch:
        return "C", watch, m
    gain, loss = m["gross_profit_cents"], m["gross_loss_cents"]
    proven = (
        m["trading_days"] >= policy.a_min_trading_days
        and m["trades"] >= policy.a_min_trades
        and m["t_above"]
        and gain > policy.a_min_profit_factor * loss
        and facts.paid_payouts >= policy.a_min_paid_payouts
        and short.short_profit_cents * 100 < policy.a_max_short_percent * max(gain, 1)
        and facts.actioned_case_at is None
    )
    return ("A", ["proven"], m) if proven else ("B", [], m)


RANK = {c: i for i, c in enumerate(CLASSES)}
PREFIX = "daily: "  # the reason of every class this job sets


def reason_text(reasons: list[str], m: Mapping[str, Any]) -> str:
    """The recorded reason: the job's prefix, the reasons, the measures."""
    return f"{PREFIX}{','.join(reasons) or 'standard'}; {json.dumps(dict(m), sort_keys=True)}"


def _reasons_of(reason: str) -> str:
    return reason[len(PREFIX) :].split(";", 1)[0]


def newest_fact(facts: Facts, reasons: list[str]) -> int | None:
    """When the newest dated fact behind `reasons` arose; short holds carry
    no single date, so they never count as new."""
    times = {
        "case_actioned": facts.actioned_case_at,
        "chargeback": facts.chargeback_at,
        "trades_cluster": facts.trades_cluster_at,
    }
    dated = [t for r in reasons if (t := times.get(r)) is not None]
    return max(dated) if dated else None


def decide(last: tuple[str, str, int] | None, cls: str, reasons: list[str], facts: Facts) -> bool:
    """Whether the job records `cls` over the class in force `last`
    (class, reason, at). The job owns only the classes it set: a class set
    by staff or by the payout check (D-14.5) stands, unless the job finds a
    worse class on a fact newer than that class. So the job never lifts
    a person's decision, and never repeats an unchanged class."""
    if last is None:
        return cls != "B"
    was, why, at = last
    if why.startswith(PREFIX):
        return was != cls or _reasons_of(why) != (",".join(reasons) or "standard")
    newest = newest_fact(facts, reasons)
    return RANK[cls] > RANK[was] and newest is not None and newest > at


class Live(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


class ClassRecords(Protocol):
    async def account_class(self, acc: str) -> tuple[str, str, int] | None: ...

    async def set_account_class(
        self, acc: str, cls: str, reason: str, by: str, ts: int
    ) -> None: ...


class Classes:
    """The daily job, and the staff view of classes in force."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        read_schema: str,
        policy: Any,
        live: Live,
        records: ClassRecords,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._r = read_schema
        self._policy = policy
        self._live = live
        self._records = records
        self._last = -1

    async def _events(self, acc: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT event::text AS event FROM {self._r}.account_events WHERE account_id = $1 "
            f"AND kind IN ('PositionOpened', 'PositionClosed', 'CommissionCharged') "
            f"ORDER BY seq, idx",
            acc,
        )
        return [json.loads(r["event"]) for r in rows]

    async def _facts(self, acc: str, cif: str) -> Facts:
        s = self._s
        row = await self._pool.fetchrow(
            f"SELECT (SELECT count(*) FROM {s}.payouts WHERE acc = $1 AND status = 'paid') "
            f"AS paid, (SELECT max(recorded_at) FROM {s}.chargebacks WHERE cif = $2 "
            f"AND status <> 'won') AS chargeback, (SELECT min(first_seen) FROM "
            f"{s}.cluster_links WHERE reason = 'trades' AND (a_cif = $2 OR b_cif = $2)) "
            f"AS cluster, (SELECT max(coalesce(closed_at, opened_at)) FROM {s}.cases "
            f"WHERE cif = $2 AND outcome = 'actioned') AS actioned",
            acc,
            cif,
        )
        assert row is not None

        def at(v: Any) -> int | None:
            return None if v is None else int(v)

        return Facts(
            tuple(trades_from(await self._events(acc))),
            int(row["paid"]),
            at(row["chargeback"]),
            at(row["cluster"]),
            at(row["actioned"]),
        )

    async def run(self, now: int, every_ms: int = DAY_MS) -> int:
        """Class every active funded account; record changes; return how many."""
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        policy = (await self._policy.current()).classes
        if not policy.on:
            return 0
        accounts = await self._pool.fetch(
            f"SELECT acc, cif FROM {self._s}.accounts WHERE stage = 'funded' ORDER BY acc"
        )
        changed = 0
        for a in accounts:
            acc = str(a["acc"])
            view = await self._live.account(acc)
            if view is None or view.get("status") != "active":
                continue
            facts = await self._facts(acc, str(a["cif"]))
            cls, reasons, m = classify(facts, policy, now)
            if decide(await self._records.account_class(acc), cls, reasons, facts):
                await self._records.set_account_class(
                    acc, cls, reason_text(reasons, m), "system", now
                )
                changed += 1
        return changed

    # --- reading ---------------------------------------------------------------------

    async def watched(self, cif: str) -> bool:
        """Whether any of the customer's accounts is in class C or D now,
        whoever set it."""
        found = await self._pool.fetchval(
            f"SELECT 1 FROM (SELECT DISTINCT ON (c.acc) c.class FROM "
            f"{self._s}.account_class_changes c JOIN {self._s}.accounts a USING (acc) "
            f"WHERE a.cif = $1 ORDER BY c.acc, c.at DESC) latest "
            f"WHERE class IN ('C', 'D') LIMIT 1",
            cif,
        )
        return found is not None

    async def current(self) -> list[dict[str, Any]]:
        """The class in force of every classed account."""
        rows = await self._pool.fetch(
            f"SELECT DISTINCT ON (c.acc) c.acc, a.cif, c.class, c.reason, c.set_by, c.at "
            f"FROM {self._s}.account_class_changes c JOIN {self._s}.accounts a USING (acc) "
            f"ORDER BY c.acc, c.at DESC"
        )
        return [dict(r) for r in rows]

    async def history(self, acc: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT class, reason, set_by, at FROM {self._s}.account_class_changes "
            f"WHERE acc = $1 ORDER BY at",
            acc,
        )
        return [dict(r) for r in rows]


def counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out = dict.fromkeys(CLASSES, 0)
    for r in rows:
        out[str(r["class"])] += 1
    return out
