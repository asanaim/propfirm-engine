"""Payout reserve coverage (build plan step 3, line 5; D-13.7, D-14.20).

    R* = certain + open_weight x open profit + expected_weight x expected
         on evaluations + the most one customer could claim now
    K  = reserve / R*

- certain: the liability desk's certain, open and expected tiers together
  (D-14.18); they never overlap, so this is every claim on closed profit.
- open profit: the traders' share of profit in open positions (contingent).
- expected on evaluations: for each active evaluation, its order's price x
  the product's sweep share / sweep factor (the sweep is that factor times
  the expected payout share). No cohort decay is applied yet, which
  overstates it: the safe side.
- the most one customer could claim: by customer, standing in for the
  identity cluster until clusters exist (step 5).

K is an exact fraction; it is shown rounded down to two decimals, so the
shown figure never looks better than the real one. With nothing owed
(R* = 0) the state is normal and K has no value.

States, with the policy's thresholds: normal (K >= amber_below), amber,
red (below red_below), breach (below breach_below). This module only
reads and classifies; the automatic responses of each state come later.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Protocol

import asyncpg

from propfirm.customers.liability import Desk
from propfirm.policy import ReservePolicy

NORMAL, AMBER, RED, BREACH = "normal", "amber", "red", "breach"


class LiveAccounts(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class Coverage:
    reserve_cents: int
    certain_cents: int
    open_profit_cents: int
    expected_cents: int  # on evaluations, before its weight
    evaluations: int  # active evaluations counted
    largest_customer_cents: int
    required_cents: int  # R*, rounded up to the cent
    k: Fraction | None  # None when nothing is required
    state: str
    releasable_cents: int  # above release_above x R*; information only


def ceil_cents(value: Fraction) -> int:
    """A cent amount rounded up (the reserve requirement is never
    understated)."""
    return -(-value.numerator // value.denominator)


def k_text(k: Fraction | None) -> str | None:
    """K rounded down to two decimals, as text: 1.2999 -> "1.29"."""
    if k is None:
        return None
    hundredths = k.numerator * 100 // k.denominator
    whole, part = divmod(hundredths, 100)
    return f"{whole}.{part:02d}"


def state_of(k: Fraction | None, policy: ReservePolicy) -> str:
    if k is None or k >= policy.amber_below:
        return NORMAL
    if k >= policy.red_below:
        return AMBER
    if k >= policy.breach_below:
        return RED
    return BREACH


def coverage(
    reserve_cents: int,
    desk: Desk,
    expected_cents: int,
    evaluations: int,
    policy: ReservePolicy,
) -> Coverage:
    certain = desk.certain_cents + desk.open_cents + desk.expected_cents
    required = ceil_cents(
        certain
        + policy.open_weight * desk.open_profit_cents
        + policy.expected_weight * expected_cents
        + desk.largest_customer_cents
    )
    k = None if required == 0 else Fraction(reserve_cents, required)
    releasable = max(0, reserve_cents - ceil_cents(policy.release_above * required))
    return Coverage(
        reserve_cents=reserve_cents,
        certain_cents=certain,
        open_profit_cents=desk.open_profit_cents,
        expected_cents=expected_cents,
        evaluations=evaluations,
        largest_customer_cents=desk.largest_customer_cents,
        required_cents=required,
        k=k,
        state=state_of(k, policy),
        releasable_cents=releasable,
    )


class Evaluations:
    """The expected payout of every active evaluation (stage 1 or 2)."""

    def __init__(self, pool: asyncpg.Pool, schema: str, live: LiveAccounts) -> None:
        self._pool = pool
        self._s = schema
        self._live = live

    async def expected(self, policy: ReservePolicy) -> tuple[int, int]:
        """(expected payout in cents, active evaluations counted)."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT a.acc, o.product_id, o.price_cents FROM {s}.accounts a "
            f"JOIN {s}.orders o ON o.ord = a.ord WHERE a.stage IN ('s1', 's2') "
            f"AND o.price_cents > 0 ORDER BY a.acc"
        )
        total = Fraction(0)
        counted = 0
        for r in rows:
            share = policy.sweep.get(str(r["product_id"]))
            if share is None:
                continue
            view = await self._live.account(str(r["acc"]))
            if view is None or view.get("status") != "active":
                continue
            counted += 1
            total += int(r["price_cents"]) * share / policy.sweep_factor
        return ceil_cents(total), counted


# --- the states acted on (D-14.23) ----------------------------------------------------

RANK = {NORMAL: 0, AMBER: 1, RED: 2, BREACH: 3}
DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class Limits:
    """What the firm does in a state; each limit holds from its state on."""

    codes_paused: bool  # amber: no promotion below list (discount codes)
    scaling_frozen: bool  # amber
    instant: str  # "open"; "capped" (amber: the trailing 30 day average); "stopped" (red)
    funded_queue: bool  # red: new funded accounts wait
    payouts_delayed: bool  # breach: published, never silent


def limits(state: str) -> Limits:
    rank = RANK[state]
    return Limits(
        codes_paused=rank >= 1,
        scaling_frozen=rank >= 1,
        instant="open" if rank == 0 else "capped" if rank == 1 else "stopped",
        funded_queue=rank >= 2,
        payouts_delayed=rank >= 3,
    )


def instant_cap(sold_last_30_days: int) -> int:
    """Instant sales allowed per UTC day in amber: the trailing 30 day
    daily average, rounded up, at least 1 (so the product is capped, not
    stopped, which is red's response)."""
    return max(1, -(-sold_last_30_days // 30))


class Book(Protocol):
    async def reserve_cents(self) -> int: ...


class DeskSource(Protocol):
    async def desk(self, now: int, top: int = 20) -> Desk: ...


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

    async def close(self, ref: str, reason: str, ts: int, kind: str | None = None) -> int: ...


class PolicyNow(Protocol):
    async def current(self) -> Any: ...


class MoveRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Reserve:
    """Coverage now, the state as last seen, owners' top-ups and releases.

    `watch` (the API loop, at most once a minute) records each change of
    state and alerts the settings change audience; a return to normal
    closes the alert. Readers use `state()`, the last recorded state, so a
    request never computes the desk. With the reserve off the state is
    normal."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        policy: PolicyNow,
        books: Book,
        desk: DeskSource,
        evaluations: Evaluations,
        notifier: Notifier | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._books = books
        self._desk = desk
        self._evaluations = evaluations
        self._notifier = notifier
        self._last = -1

    async def coverage(self, now: int) -> Coverage | None:
        policy = (await self._policy.current()).reserve
        if not policy.on:
            return None
        d = await self._desk.desk(now)
        expected, counted = await self._evaluations.expected(policy)
        return coverage(await self._books.reserve_cents(), d, expected, counted, policy)

    async def state(self) -> str:
        if not (await self._policy.current()).reserve.on:
            return NORMAL
        found = await self._pool.fetchval(
            f"SELECT state FROM {self._s}.reserve_states ORDER BY seq DESC LIMIT 1"
        )
        return NORMAL if found is None else str(found)

    async def since(self) -> int | None:
        """When the state now in force began."""
        found = await self._pool.fetchval(
            f"SELECT at FROM {self._s}.reserve_states ORDER BY seq DESC LIMIT 1"
        )
        return None if found is None else int(found)

    async def watch(self, now: int, every_ms: int = 60_000) -> str | None:
        """Record the state if it changed; return the new state, else None."""
        if self._last >= 0 and now - self._last < every_ms:
            return None
        self._last = now
        c = await self.coverage(now)
        if c is None:
            return None
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{s}.reserve")
            last = await conn.fetchval(
                f"SELECT state FROM {s}.reserve_states ORDER BY seq DESC LIMIT 1"
            )
            if (last or NORMAL) == c.state and last is not None:
                return None
            if last is None and c.state == NORMAL:
                return None  # nothing to say yet
            await conn.execute(
                f"INSERT INTO {s}.reserve_states (at, state, k, reserve_cents, required_cents) "
                f"VALUES ($1, $2, $3, $4, $5)",
                now,
                c.state,
                k_text(c.k),
                c.reserve_cents,
                c.required_cents,
            )
        await self._alert(c, str(last or NORMAL), now)
        return c.state

    async def _alert(self, c: Coverage, before: str, now: int) -> None:
        if self._notifier is None:
            return
        await self._notifier.close("RESERVE", f"reserve {c.state}", now, "reserve")
        if c.state == NORMAL:
            return
        worse = RANK[c.state] > RANK[before]
        body = (
            f"Coverage K {k_text(c.k)}: reserve {c.reserve_cents / 100:,.2f}, "
            f"requirement {c.required_cents / 100:,.2f} USD. "
            + {
                AMBER: "Discount codes paused, scaling frozen, Instant capped.",
                RED: "New funded accounts queue; Instant sales stopped.",
                BREACH: "Payout delay published. Owners: add capital or stop the worst product.",
            }[c.state]
        )
        if c.state in (RED, BREACH):
            worst = await self.worst_product(now)
            if worst is not None:
                body += f" Highest payout ratio, 90 days: {worst[0]} {worst[1]}%; reprice it."
        await self._notifier.notify(
            "settings.change",
            "reserve",
            "RESERVE",
            f"Reserve {c.state}" + ("" if worse else f" (better than {before})"),
            body,
            now,
            urgent=c.state in (RED, BREACH),
        )

    async def worst_product(self, now: int) -> tuple[str, int] | None:
        """The product with the highest share of its 90-day sales paid out
        in the same 90 days, as a whole percent rounded down."""
        s = self._s
        rows = await self._pool.fetch(
            f"WITH sold AS (SELECT product_id, sum(price_cents) AS cents FROM {s}.orders "
            f"WHERE status IN ('paid', 'provisioned') AND paid_at >= $1 GROUP BY product_id), "
            f"paid AS (SELECT o.product_id, sum(p.trader_amount_cents) AS cents "
            f"FROM {s}.payouts p JOIN {s}.accounts a ON a.acc = p.acc "
            f"JOIN {s}.orders o ON o.ord = a.ord WHERE p.status = 'paid' AND p.paid_at >= $1 "
            f"GROUP BY o.product_id) "
            f"SELECT sold.product_id, sold.cents AS sold, coalesce(paid.cents, 0) AS paid "
            f"FROM sold LEFT JOIN paid USING (product_id) WHERE sold.cents > 0",
            now - 90 * DAY_MS,
        )
        best: tuple[str, int] | None = None
        for r in rows:
            pct = int(r["paid"]) * 100 // int(r["sold"])
            if best is None or pct > best[1] or (pct == best[1] and r["product_id"] < best[0]):
                best = (str(r["product_id"]), pct)
        return best

    async def instant_sold(self, product_id: str, now: int) -> tuple[int, int]:
        """(sold today, sold in the 30 UTC days before today) of a product."""
        today = now - now % DAY_MS
        row = await self._pool.fetchrow(
            f"SELECT count(*) FILTER (WHERE paid_at >= $2) AS today, "
            f"count(*) FILTER (WHERE paid_at < $2 AND paid_at >= $3) AS before "
            f"FROM {self._s}.orders WHERE product_id = $1 AND status IN ('paid', 'provisioned') "
            f"AND price_cents > 0",
            product_id,
            today,
            today - 30 * DAY_MS,
        )
        assert row is not None
        return int(row["today"]), int(row["before"])

    # --- top-ups and releases, two people ---------------------------------------------

    async def moves(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.reserve_moves ORDER BY proposed_at DESC, rsm DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def propose(
        self, kind: str, amount_cents: int, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        if kind not in ("topup", "release"):
            raise MoveRefused("invalid", "a move is a topup or a release")
        if amount_cents <= 0:
            raise MoveRefused("invalid", "the amount is more than 0")
        if kind == "release":
            await self._releasable(amount_cents, now)
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            number = await conn.fetchval(f"SELECT nextval('{s}.rsm_seq')")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.reserve_moves (rsm, kind, amount_cents, reason, proposed_by, "
                f"proposed_at, status) VALUES ($1, $2, $3, $4, $5, $6, 'proposed') RETURNING *",
                f"RSM{int(number):08d}",
                kind,
                amount_cents,
                reason,
                by,
                now,
            )
        assert row is not None
        return dict(row)

    async def decide(
        self, rsm: str, approve: bool, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.reserve_moves WHERE rsm = $1 FOR UPDATE", rsm
            )
            if row is None:
                raise MoveRefused("unknown", f"no move {rsm}")
            if row["status"] != "proposed":
                raise MoveRefused("decided", "this move was decided already")
            if row["proposed_by"] == by:
                raise MoveRefused("same_person", "a move is decided by someone else")
            if approve and row["kind"] == "release":
                await self._releasable(int(row["amount_cents"]), now)  # checked again now
            found = await conn.fetchrow(
                f"UPDATE {s}.reserve_moves SET status = $2, decided_by = $3, decided_at = $4, "
                f"decision_reason = $5 WHERE rsm = $1 RETURNING *",
                rsm,
                "approved" if approve else "rejected",
                by,
                now,
                reason,
            )
        assert found is not None
        return dict(found)

    async def _releasable(self, amount_cents: int, now: int) -> None:
        c = await self.coverage(now)
        if c is None:
            raise MoveRefused("off", "the reserve is off")
        pending = int(
            await self._pool.fetchval(
                f"SELECT coalesce(sum(amount_cents), 0) FROM {self._s}.reserve_moves "
                f"WHERE kind = 'release' AND status = 'approved' AND NOT EXISTS "
                f"(SELECT 1 FROM {self._s}.ops_entries e "
                f"WHERE e.entry_id = 'reserve-release:' || rsm)"
            )
        )
        if amount_cents > c.releasable_cents - pending:
            raise MoveRefused(
                "too_much",
                f"at most {max(0, c.releasable_cents - pending)} cents are above the release line",
            )
