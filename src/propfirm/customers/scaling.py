"""Scaling (build plan step 2, line 4; section 1, 13 Nov 2026; D-14.8).

A funded Lite, Classic or 1-Step account takes one step when all hold:
- `review_days` have passed since funding or the last step;
- in that period, net profit of at least `min_net_profit` of the starting
  balance (the balance now plus what payouts took out, less the balance at
  the period's start) and at least `min_paid_payouts` paid payouts;
- the account is class A or B (not C or D), active and not locked;
- the step keeps the trader within `max_allocation_cents`.
A step adds `step` of the account's first starting balance. A job proposes
steps (one open at a time per account); a member of staff approves; the
engine then adds the allocation (ScaleAccount). Instant is not scaled.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.journal.inputs import Input, ScaleAccount
from propfirm.pgschema import check_schema
from propfirm.policy import Policy, ScalingPolicy, instant_funding
from propfirm.service.gateway import Outcome

DAY_MS = 86_400_000


class ScalingRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Period:
    """What the check reads about one account's current review period."""

    acc: str
    size_cents: int  # the first starting balance
    since: int  # ms UTC: funding or the last step
    start_balance_cents: int  # the balance at `since`
    balance_cents: int  # now
    taken_cents: int  # what payouts debited in the period
    paid_payouts: int  # in the period
    risk_class: str | None  # None: unclassed (counts as B)
    active: bool
    trader_allocation_cents: int  # the trader's funded allocations now
    steps: int  # applied so far


@dataclass(frozen=True, slots=True)
class Verdict:
    due: bool
    add_cents: int
    checks: tuple[tuple[str, bool, str], ...]

    def view(self) -> dict[str, Any]:
        return {
            "due": self.due,
            "add_cents": self.add_cents,
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in self.checks],
        }


def step_size(policy: ScalingPolicy, size_cents: int) -> int:
    """A step's allocation: `step` of the first starting balance, rounded
    down to the cent."""
    return int(policy.step * size_cents)


def check(policy: ScalingPolicy, p: Period, now: int) -> Verdict:
    add = step_size(policy, p.size_cents)
    net = p.balance_cents + p.taken_cents - p.start_balance_cents
    needed = int(policy.min_net_profit * p.size_cents)
    days = (now - p.since) // DAY_MS
    room = policy.max_allocation_cents - p.trader_allocation_cents
    checks = (
        ("period", days >= policy.review_days, f"{days} of {policy.review_days} days"),
        ("net_profit", net >= needed, f"{net} of {needed} cents"),
        (
            "paid_payouts",
            p.paid_payouts >= policy.min_paid_payouts,
            f"{p.paid_payouts} of {policy.min_paid_payouts}",
        ),
        ("class", p.risk_class not in ("C", "D"), f"class {p.risk_class or 'not set (B)'}"),
        ("active", p.active, "active" if p.active else "not active"),
        ("allocation", add <= room, f"{add} cents to add, {room} cents of room"),
    )
    return Verdict(all(ok for _, ok, _ in checks), add, checks)


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...


class Live(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


class PolicySource(Protocol):
    async def current(self) -> Policy: ...


class Scaling:
    def __init__(
        self,
        pool: asyncpg.Pool,
        customer_schema: str,
        policy: PolicySource,
        live: Live,
        gateway: Submitter,
        wait_s: float = 5.0,
    ) -> None:
        self._pool = pool
        self._s = check_schema(customer_schema)
        self._policy = policy
        self._live = live
        self._gateway = gateway
        self._wait_s = wait_s
        self._last_run: int | None = None

    async def steps_taken(self, ord_id: str) -> int:
        """Applied steps on the order's accounts (the split ladder reads it)."""
        count: int = await self._pool.fetchval(
            f"SELECT count(*) FROM {self._s}.scaling_steps WHERE ord = $1 AND status = 'applied'",
            ord_id,
        )
        return count

    async def _period(self, conn: Any, acc: Any) -> Period | None:
        s = self._s
        live = await self._live.account(acc["acc"])
        if live is None:
            return None
        last = await conn.fetchrow(
            f"SELECT decided_at, balance_after_cents FROM {s}.scaling_steps "
            f"WHERE acc = $1 AND status = 'applied' ORDER BY step DESC LIMIT 1",
            acc["acc"],
        )
        steps: int = await conn.fetchval(
            f"SELECT count(*) FROM {s}.scaling_steps WHERE acc = $1 AND status = 'applied'",
            acc["acc"],
        )
        since = acc["created_at"] if last is None else last["decided_at"]
        start = acc["size_cents"] if last is None else last["balance_after_cents"]
        taken = await conn.fetchrow(
            f"SELECT coalesce(sum(debit_cents), 0) AS cents, "
            f"count(*) FILTER (WHERE status = 'paid') AS paid "
            f"FROM {s}.payouts WHERE acc = $1 AND debited_at >= $2",
            acc["acc"],
            since,
        )
        cls = await conn.fetchval(
            f"SELECT class FROM {s}.account_class_changes WHERE acc = $1 ORDER BY at DESC LIMIT 1",
            acc["acc"],
        )
        allocation: int = await conn.fetchval(
            f"SELECT coalesce(sum(a.size_cents), 0) + coalesce((SELECT sum(add_cents) "
            f"FROM {s}.scaling_steps t JOIN {s}.accounts b USING (acc) "
            f"WHERE b.cif = $1 AND t.status = 'applied'), 0) "
            f"FROM {s}.accounts a WHERE a.cif = $1 AND a.stage = 'funded'",
            acc["cif"],
        )
        return Period(
            acc=acc["acc"],
            size_cents=acc["size_cents"],
            since=since,
            start_balance_cents=start,
            balance_cents=int(live["balance_cents"]),
            taken_cents=int(taken["cents"]),
            paid_payouts=int(taken["paid"]),
            risk_class=cls,
            active=live.get("status") == "active" and not live.get("locked", False),
            trader_allocation_cents=int(allocation),
            steps=int(steps),
        )

    async def run(self, now: int, every_ms: int = 3_600_000) -> list[str]:
        """Propose each step that is due (at most once per `every_ms`);
        return the new SCL ids."""
        if self._last_run is not None and now - self._last_run < every_ms:
            return []
        self._last_run = now
        policy = await self._policy.current()
        scalable = {p.id for p in policy.products if not instant_funding(p)}
        s = self._s
        made: list[str] = []
        async with self._pool.acquire() as conn:
            accounts = await conn.fetch(
                f"SELECT a.* FROM {s}.accounts a WHERE a.stage = 'funded' "
                f"AND a.product_id = ANY($1::text[]) AND NOT EXISTS (SELECT 1 FROM "
                f"{s}.scaling_steps t WHERE t.acc = a.acc AND t.status = 'proposed') "
                f"ORDER BY a.acc",
                sorted(scalable),
            )
            for acc in accounts:
                period = await self._period(conn, acc)
                if period is None:
                    continue
                verdict = check(policy.scaling, period, now)
                if not verdict.due:
                    continue
                number = await conn.fetchval(f"SELECT nextval('{s}.scl_seq')")
                scl = f"SCL{number:08d}"
                try:
                    await conn.execute(
                        f"INSERT INTO {s}.scaling_steps (scl, acc, ord, step, add_cents, "
                        f"measures, status, proposed_at) "
                        f"VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'proposed', $7)",
                        scl,
                        acc["acc"],
                        acc["ord"],
                        period.steps + 1,
                        verdict.add_cents,
                        json.dumps(verdict.view()),
                        now,
                    )
                except asyncpg.UniqueViolationError:
                    continue
                made.append(scl)
        return made

    async def get(self, scl: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT *, measures::text AS m, outcome::text AS o "
            f"FROM {self._s}.scaling_steps WHERE scl = $1",
            scl,
        )
        return None if row is None else _view(row)

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT *, measures::text AS m, outcome::text AS o "
            f"FROM {self._s}.scaling_steps ORDER BY scl DESC LIMIT $1",
            limit,
        )
        return [_view(r) for r in rows]

    async def reject(self, scl: str, by: str, ts: int) -> dict[str, Any]:
        await self._decide(scl, by, ts, "rejected", None, None)
        found = await self.get(scl)
        assert found is not None
        return found

    async def approve(self, scl: str, by: str, ts: int) -> dict[str, Any]:
        """Staff approve a proposed step: the engine adds the allocation."""
        found = await self.get(scl)
        if found is None:
            raise ScalingRefused("unknown", f"no scaling step {scl}")
        if found["status"] != "proposed":
            raise ScalingRefused("decided", f"{scl} is {found['status']}")
        outcome = await self._gateway.submit(
            ScaleAccount(found["acc"], found["add_cents"], scl, ts), self._wait_s
        )
        scaled = next((e for e in outcome.events if e.get("type") == "AccountScaled"), None)
        if scaled is None:
            raise ScalingRefused(
                "engine",
                f"the engine did not scale {found['acc']}: "
                + ", ".join(str(e.get("reason", e.get("type"))) for e in outcome.events),
            )
        await self._decide(
            scl,
            by,
            ts,
            "applied",
            int(scaled["balance_cents"]),
            {"initial_balance_cents": scaled["initial_balance_cents"]},
        )
        done = await self.get(scl)
        assert done is not None
        return done

    async def _decide(
        self,
        scl: str,
        by: str,
        ts: int,
        status: str,
        balance_after: int | None,
        outcome: dict[str, Any] | None,
    ) -> None:
        updated = await self._pool.execute(
            f"UPDATE {self._s}.scaling_steps SET status = $2, decided_by = $3, decided_at = $4, "
            f"balance_after_cents = $5, outcome = $6::jsonb WHERE scl = $1 AND status = 'proposed'",
            scl,
            status,
            by,
            ts,
            balance_after,
            None if outcome is None else json.dumps(outcome),
        )
        if updated.endswith(" 0"):
            raise ScalingRefused("decided", f"{scl} is not proposed")


def _view(row: Any) -> dict[str, Any]:
    keys = (
        "scl",
        "acc",
        "ord",
        "step",
        "add_cents",
        "status",
        "proposed_at",
        "decided_by",
        "decided_at",
        "balance_after_cents",
    )
    out = {k: row[k] for k in keys}
    out["measures"] = json.loads(row["m"])
    out["outcome"] = None if row["o"] is None else json.loads(row["o"])
    return out
