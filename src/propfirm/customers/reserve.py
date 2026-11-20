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
