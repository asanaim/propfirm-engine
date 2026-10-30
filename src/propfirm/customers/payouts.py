"""Payable profit and payout eligibility of a funded account (outline M18).

Payable profit:
    X  the closed balance, or the equity, at the time asked (`payouts.basis`,
       D22/D23);
    B  the payout baseline: the account's initial capital. A payout never
       moves it; only scaling (later) sets a new one. A payout returns a
       closed-balance account to B, so losses after a payout must be made
       back above B before the next one (high-water mark, I8);
    V  profit removed for violations: the remove_profit actions of confirmed
       case decisions (customers.cases);
    P  = max(0, X - B - V), the payable profit;
    s  the product's profit split (the 90 % tier comes with scaling, later).
The trader's share is s * P rounded down to the cent (`share_floor`); the
firm's share is the rest. A fee refund (1-Step: with the 3rd payout) is a
separate line. A payout maximum, when set, caps the trader's amount.

Eligibility, all of which must hold (M18):
- funded: a funded account, active (not breached, passed or locked);
- waiting period: the first payout no sooner than `first_after_days` after
  funding (later payouts: one per pay day, with 3d);
- trading days in the period (`min_trading_days`, D22);
- consistency (D19): when the account's rule set has the rule, its best
  day in the period is at most that share of the period's realized profit;
- minimum: the trader's amount reaches `minimum_cents`;
- flat (D23, when `flat_required`): no open position or pending order;
- identity: an approved Tier 2 check, and a customer not blocked (screening
  clean);
- residence: the country gate (when on) allows the declared and document
  countries (D32).
- no case not closed (integrity, fraud, compliance: M16, M17, M20); the
  detail never says more than "held for review" (no tipping off).
One check is recorded as not decided here: the payout wallet (checked when
a payout is requested, step 3d).

The period runs from funding, or from the last payout; trading days and
daily P&L count whole trading days (17:00 New York), so the trading day of
the last payout counts in the new period [DEFAULT].

Payouts are paid on the policy's pay days (UTC dates): the next one on or
after the end of the waiting period is shown.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from propfirm.access.gating import check_country
from propfirm.money import share_floor
from propfirm.policy import Policy, ProductPolicy, percent_text
from propfirm.rules.clock import NEW_YORK_17

PASS, FAIL, LATER = "pass", "fail", "not_decided_here"
_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str  # pass, fail, or not_decided_here
    detail: str


@dataclass(frozen=True, slots=True)
class FundedAccount:
    """What eligibility reads about the account and its customer."""

    acc: str
    product: ProductPolicy
    stage: str  # of the account's own rule set (the engine's)
    consistency: Fraction | None  # of the account's own rule set
    funded_at: int  # ms UTC: when the account opened
    baseline_cents: int  # B
    fee_paid_cents: int  # the order's price (for a fee refund)
    payouts_paid: int  # payouts made on this order's accounts so far (3d)
    last_payout_at: int | None  # ms UTC (3d)
    # live engine state
    status: str  # active, passed, breached
    locked: bool
    balance_cents: int
    equity_cents: int
    open_positions: int
    pending_orders: int
    trading_day_ids: Sequence[int]
    day_pnl: Sequence[tuple[int, int]]  # (trading day id, realized cents)
    # the customer
    cif_status: str
    tier2_approved: bool
    residence_country: str
    document_country: str | None
    # cases (M16, M17, M20)
    open_case: bool = False  # the customer has a case not closed
    violations_cents: int = 0  # profit removed by confirmed decisions (V)


@dataclass(frozen=True, slots=True)
class Eligibility:
    acc: str
    as_of: int
    basis: str
    measured_cents: int  # X
    baseline_cents: int  # B
    violations_cents: int  # V
    payable_cents: int  # P
    split_percent: str
    trader_share_cents: int
    firm_share_cents: int
    fee_refund_cents: int
    trader_amount_cents: int  # what the trader would receive (share, refund, maximum)
    period_start: int  # ms UTC
    trading_days: int  # in the period
    next_pay_date: date
    checks: tuple[Check, ...]

    @property
    def eligible(self) -> bool:
        return all(c.status != FAIL for c in self.checks)


def pay_dates(start: date, pay_days: Sequence[str]) -> date:
    """The first pay day on or after `start` ("last": the month's last day)."""
    day = start
    for _ in range(62):
        last = calendar.monthrange(day.year, day.month)[1]
        names = {str(day.day)} | ({"last"} if day.day == last else set())
        if names & set(pay_days):
            return day
        day += timedelta(days=1)
    raise ValueError(f"no pay day in {pay_days!r}")


def _utc_date(ts: int) -> date:
    return datetime.fromtimestamp(ts / 1000, UTC).date()


def eligibility(policy: Policy, account: FundedAccount, now: int) -> Eligibility:
    payouts = policy.payouts
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, PASS if ok else FAIL, detail))

    measured = account.balance_cents if payouts.basis == "closed_balance" else account.equity_cents
    violations = account.violations_cents
    payable = max(0, measured - account.baseline_cents - violations)
    split = account.product.profit_split
    trader_share = share_floor(payable, split)
    payout_number = account.payouts_paid + 1
    refund_due = account.product.fee_refund_with_payout == payout_number
    refund = account.fee_paid_cents if refund_due else 0
    amount = trader_share + refund
    if payouts.maximum_cents:
        amount = min(amount, payouts.maximum_cents)

    active = account.status == "active" and not account.locked
    funded = account.stage == "funded"
    check("funded", funded and active, f"stage {account.stage}, {account.status}")

    period_start = account.last_payout_at or account.funded_at
    if account.last_payout_at is None:
        opens = account.funded_at + payouts.first_after_days * _DAY_MS
        check(
            "waiting_period",
            now >= opens,
            f"first payout from {_utc_date(opens).isoformat()} "
            f"({payouts.first_after_days} days after funding)",
        )
        earliest = max(_utc_date(opens), _utc_date(now))
    else:
        earliest = max(_utc_date(account.last_payout_at) + timedelta(days=1), _utc_date(now))
        check("waiting_period", True, "one payout per pay day")
    next_pay = pay_dates(earliest, payouts.pay_days)

    first_day = NEW_YORK_17.day_start(period_start)
    days = [d for d in account.trading_day_ids if d >= first_day]
    check(
        "trading_days",
        len(days) >= payouts.min_trading_days,
        f"{len(days)} in the period, {payouts.min_trading_days} needed",
    )

    share = account.consistency
    if share is None:
        check("consistency", True, "no consistency rule on this rule set")
    else:
        period = [pnl for day, pnl in account.day_pnl if day >= first_day]
        total, best = sum(period), max(period, default=0)
        check(
            "consistency",
            total > 0 and best <= share * total,
            f"best day {best} of {total} cents; at most {percent_text(share)} %",
        )

    check(
        "minimum",
        amount >= payouts.minimum_cents,
        f"{amount} cents to the trader; minimum {payouts.minimum_cents}",
    )
    if payouts.flat_required:
        check(
            "flat",
            account.open_positions == 0 and account.pending_orders == 0,
            f"{account.open_positions} open positions, {account.pending_orders} pending orders",
        )
    check(
        "identity",
        account.tier2_approved and account.cif_status == "registered",
        f"Tier 2 {'approved' if account.tier2_approved else 'missing'}, "
        f"customer {account.cif_status}",
    )
    gate = check_country(
        policy.access, declared=account.residence_country, document=account.document_country
    )
    check("residence", gate.allowed, ", ".join(gate.reasons) or "not restricted")
    check("cases", not account.open_case, "held for review" if account.open_case else "none open")
    checks.append(Check("wallet", LATER, "checked when the payout is requested"))

    return Eligibility(
        acc=account.acc,
        as_of=now,
        basis=payouts.basis,
        measured_cents=measured,
        baseline_cents=account.baseline_cents,
        violations_cents=violations,
        payable_cents=payable,
        split_percent=percent_text(split) or "0",
        trader_share_cents=trader_share,
        firm_share_cents=payable - trader_share,
        fee_refund_cents=refund,
        trader_amount_cents=amount,
        period_start=period_start,
        trading_days=len(days),
        next_pay_date=next_pay,
        checks=tuple(checks),
    )
