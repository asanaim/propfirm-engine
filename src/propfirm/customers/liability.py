"""Liability desk (build plan step 3, line 4; D-14.18): what the firm owes
or may owe traders, in three tiers, and the largest single claim.

- certain: payouts approved, taken from the account (debited) or sent and
  not yet paid; and commissions payable from the operator ledger;
- open: payouts requested and not yet decided;
- expected: what each active funded account would receive if it asked
  now (the payout rules' own amount: share, fee refund, maximum), less
  any payout of that account already counted above and still in its
  balance. Eligibility checks (days, wallet, identity) are ignored: a
  claim not yet due is still a claim.

Beside them, contingent (D-14.20): the trader's share of profit still in
open positions, s x [(E - I - V)+ - (B - I - V)+]+, rounded up to the
cent; zero when payouts are measured on equity (it is in `expected`).

Read on demand; nothing is stored. Amounts in cents.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Protocol

import asyncpg

from propfirm.customers.payouts import Eligibility

log = logging.getLogger(__name__)

CERTAIN = ("approved", "debited", "sent")
OPEN = ("requested",)
IN_BALANCE = ("requested", "approved")  # not yet taken from the account


class Eligibilities(Protocol):
    async def eligibility(self, acc: str, now: int) -> Eligibility: ...


class LiveAccounts(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class Claim:
    acc: str
    cif: str
    product_id: str
    certain_cents: int
    open_cents: int
    expected_cents: int
    open_profit_cents: int = 0  # contingent, not in the total

    @property
    def total_cents(self) -> int:
        return self.certain_cents + self.open_cents + self.expected_cents


def open_profit_cents(
    equity: int, balance: int, baseline: int, violations: int, split_percent: str
) -> int:
    """The trader's share of profit in open positions above what the
    closed balance already earns; rounded up, so never understated."""
    extra = max(0, max(0, equity - baseline - violations) - max(0, balance - baseline - violations))
    share = Fraction(split_percent) / 100
    return -(-extra * share.numerator // share.denominator)


def largest_trader(claims: Iterable[Claim], cluster: Mapping[str, str]) -> int:
    """The most one trader could claim now: claims summed by customer, and
    by identity cluster where a customer is in one (D-14.27)."""
    totals: dict[str, int] = {}
    for c in claims:
        who = cluster.get(c.cif, c.cif)
        totals[who] = totals.get(who, 0) + c.total_cents
    return max(totals.values(), default=0)


class ClusterMap(Protocol):
    async def map(self) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class Desk:
    as_of: int
    certain_cents: int  # payouts and commissions
    payouts_certain_cents: int
    commissions_cents: int
    open_cents: int
    expected_cents: int
    funded_accounts: int
    largest: Claim | None
    claims: tuple[Claim, ...]  # largest first
    unread: tuple[str, ...]  # funded accounts whose expected claim could not be read
    open_profit_cents: int = 0  # contingent: profit in open positions
    largest_customer_cents: int = 0  # the most one customer (or identity cluster) could claim now

    @property
    def total_cents(self) -> int:
        return self.certain_cents + self.open_cents + self.expected_cents


class Liabilities:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        payouts: Eligibilities,
        live: LiveAccounts,
        clusters: ClusterMap | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._payouts = payouts
        self._live = live
        self._clusters = clusters  # D-14.27: one trader per identity cluster

    async def desk(self, now: int, top: int = 20) -> Desk:
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT p.acc, p.cif, o.product_id, p.status, p.trader_amount_cents "
            f"FROM {s}.payouts p JOIN {s}.orders o ON o.ord = p.ord "
            f"WHERE p.status = ANY($1::text[])",
            [*CERTAIN, *OPEN],
        )
        funded = await self._pool.fetch(
            f"SELECT a.acc, a.cif, a.product_id FROM {s}.accounts a "
            f"WHERE a.stage = 'funded' ORDER BY a.acc"
        )
        commissions = await self._pool.fetchval(
            f"SELECT coalesce(-sum(amount_cents), 0) FROM {s}.ops_postings "
            f"WHERE account = 'liability:commissions_payable'"
        )
        certain: dict[str, int] = {}
        opened: dict[str, int] = {}
        in_balance: dict[str, int] = {}
        owner: dict[str, tuple[str, str]] = {}
        for r in rows:
            acc, amount = r["acc"], int(r["trader_amount_cents"])
            owner[acc] = (r["cif"], r["product_id"])
            target = certain if r["status"] in CERTAIN else opened
            target[acc] = target.get(acc, 0) + amount
            if r["status"] in IN_BALANCE:
                in_balance[acc] = in_balance.get(acc, 0) + amount
        expected: dict[str, int] = {}
        floating: dict[str, int] = {}
        unread: list[str] = []
        active = 0
        for r in funded:
            acc = r["acc"]
            owner.setdefault(acc, (r["cif"], r["product_id"]))
            view = await self._live.account(acc)
            if view is None or view.get("status") != "active":
                continue
            active += 1
            try:
                e = await self._payouts.eligibility(acc, now)
            except Exception:  # a broken account must not hide the others
                log.exception("liability: eligibility of %s", acc)
                unread.append(acc)
                continue
            amount = max(0, e.trader_amount_cents - in_balance.get(acc, 0))
            if amount:
                expected[acc] = amount
            if e.basis == "closed_balance":
                extra = open_profit_cents(
                    int(view.get("equity_cents", 0)),
                    int(view.get("balance_cents", 0)),
                    e.baseline_cents,
                    e.violations_cents,
                    e.split_percent,
                )
                if extra:
                    floating[acc] = extra
        claims = sorted(
            (
                Claim(
                    acc,
                    owner[acc][0],
                    owner[acc][1],
                    certain.get(acc, 0),
                    opened.get(acc, 0),
                    expected.get(acc, 0),
                    floating.get(acc, 0),
                )
                for acc in set(certain) | set(opened) | set(expected) | set(floating)
            ),
            key=lambda c: (-c.total_cents, c.acc),
        )
        payouts_certain = sum(certain.values())
        cluster = {} if self._clusters is None else await self._clusters.map()
        return Desk(
            as_of=now,
            certain_cents=payouts_certain + int(commissions),
            payouts_certain_cents=payouts_certain,
            commissions_cents=int(commissions),
            open_cents=sum(opened.values()),
            expected_cents=sum(expected.values()),
            funded_accounts=active,
            largest=claims[0] if claims else None,
            claims=tuple(claims[:top]),
            unread=tuple(unread),
            open_profit_cents=sum(floating.values()),
            largest_customer_cents=largest_trader(claims, cluster),
        )
