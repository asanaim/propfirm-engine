"""Payable profit and payout eligibility of a funded account (outline M18).

Payable profit:
    X  the closed balance, or the equity, at the time asked (`payouts.basis`,
       D22/D23);
    B  the payout baseline: the account's initial capital. A payout never
       moves it; only scaling (later) sets a new one. A payout returns a
       closed-balance account to B, so losses after a payout must be made
       back above B before the next one (high-water mark, I8);
    V  profit removed for violations: the remove_profit actions of confirmed
       case decisions (customers.cases) not yet taken out by a payout;
    P  = max(0, X - B - V), the payable profit;
    s  the product's profit split (the 90 % tier comes with scaling, later).
The trader's share is s * P rounded down to the cent (`share_floor`); the
firm's share is the rest. A fee refund (1-Step: with the 3rd payout) is a
separate line. A payout maximum, when set, caps the trader's amount.

Eligibility, all of which must hold (M18):
- funded: a funded account, active (not breached, passed or locked);
- waiting period (as FTMO): the first payout from the trading day
  `first_after_days` after the first trade's (17:00 New York days), a later
  one no sooner than `interval_days` after the last;
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
- no payout of the account already being processed;
- a payout wallet, past its cooling-off period;
- floor (rule sets whose trailing floor does not restart, review D1): the
  payout leaves the closed balance above the max loss floor, so P is at
  most the balance less V less the floor less one cent.
The amount leaving the account is P + V (G + V when a maximum caps the
trader's share: G = the share paid / s, rounded up to the cent; the rest of
P stays payable).

The period runs from funding, or from the last payout; trading days and
daily P&L count whole trading days (17:00 New York), so the trading day of
the last payout counts in the new period [DEFAULT].

Payouts are paid on request (as FTMO and FundedNext): `opens_at` is the
first moment one may be requested.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

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
    payouts_paid: int  # payouts made on this order's accounts so far
    last_payout_at: int | None  # ms UTC: the last payout debited from this account
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
    # payouts (3d)
    pending_payout: bool = False  # a payout of this account requested, not yet debited
    wallet_added_at: int | None = None  # the customer's payout wallet; None: none
    # the max loss floor when the rule set keeps it after a payout (Instant
    # from version 2, review D1); None: the floor restarts, or is static
    kept_floor_cents: int | None = None


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
    debit_cents: int  # what leaves the account: the gross paid on (G, or P) plus V
    period_start: int  # ms UTC
    trading_days: int  # in the period
    opens_at: int | None  # ms UTC: the end of the waiting period; None: no trade yet
    checks: tuple[Check, ...]

    @property
    def eligible(self) -> bool:
        return all(c.status != FAIL for c in self.checks)


def _utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).isoformat()


def eligibility(policy: Policy, account: FundedAccount, now: int) -> Eligibility:
    payouts = policy.payouts
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, PASS if ok else FAIL, detail))

    measured = account.balance_cents if payouts.basis == "closed_balance" else account.equity_cents
    violations = account.violations_cents
    payable = max(0, measured - account.baseline_cents - violations)
    room: int | None = None
    if account.kept_floor_cents is not None:
        # the floor does not move with the payout: what leaves (P + V) must
        # keep the closed balance above it (a breach is at or below)
        room = max(0, account.balance_cents - violations - account.kept_floor_cents - 1)
        payable = min(payable, room)
    split = account.product.profit_split
    trader_share = share_floor(payable, split)
    payout_number = account.payouts_paid + 1
    refund_due = account.product.fee_refund_with_payout == payout_number
    refund = account.fee_paid_cents if refund_due else 0
    share_paid = trader_share
    if payouts.maximum_cents:
        share_paid = max(0, min(trader_share, payouts.maximum_cents - refund))
    amount = share_paid + refund
    # a capped share pays out only part of P: G = W / s rounded up to the cent,
    # and the rest stays payable (B never moves)
    gross = payable if share_paid == trader_share else min(payable, math.ceil(share_paid / split))
    debit = gross + violations if gross else 0

    active = account.status == "active" and not account.locked
    funded = account.stage == "funded"
    check("funded", funded and active, f"stage {account.stage}, {account.status}")

    period_start = account.last_payout_at or account.funded_at
    opens: int | None
    if account.last_payout_at is None:
        # as FTMO: on the n-th trading day after the first trade's, or later
        funded_day = NEW_YORK_17.day_start(account.funded_at)
        traded = [d for d in account.trading_day_ids if d >= funded_day]
        n = payouts.first_after_days
        if traded:
            opens = NEW_YORK_17.day_start(min(traded) + n * _DAY_MS + _DAY_MS // 2)
            rule = f"{n} days after the first trade"
        else:
            opens, rule = None, f"no trade yet; the first payout {n} days after the first trade"
    else:
        opens = account.last_payout_at + payouts.interval_days * _DAY_MS
        rule = f"{payouts.interval_days} days after the last payout"
    check(
        "waiting_period",
        opens is not None and now >= opens,
        rule if opens is None else f"from {_utc_iso(opens)} ({rule})",
    )

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

    if room is not None and account.kept_floor_cents is not None:
        check(
            "floor",
            room > 0,
            f"the loss floor stays at {account.kept_floor_cents} cents after a payout; "
            f"at most {room} cents of profit can leave while the account stays above it",
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
    check(
        "no_pending_payout",
        not account.pending_payout,
        "a payout is being processed" if account.pending_payout else "none",
    )
    if account.wallet_added_at is None:
        check("wallet", False, "no payout wallet")
    else:
        usable = account.wallet_added_at + payouts.wallet_cooling_off_hours * 3_600_000
        check(
            "wallet",
            now >= usable,
            f"usable from {_utc_iso(usable)} "
            f"({payouts.wallet_cooling_off_hours} h after it was added)",
        )

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
        debit_cents=debit,
        period_start=period_start,
        trading_days=len(days),
        opens_at=opens,
        checks=tuple(checks),
    )


def eligibility_json(e: Eligibility) -> dict[str, Any]:
    """The eligibility as shown to the trader and staff, and as recorded with
    a payout request (a JSON snapshot)."""
    return {
        "acc": e.acc,
        "as_of": e.as_of,
        "eligible": e.eligible,
        "basis": e.basis,
        "measured_cents": e.measured_cents,
        "baseline_cents": e.baseline_cents,
        "violations_cents": e.violations_cents,
        "payable_cents": e.payable_cents,
        "split_percent": e.split_percent,
        "trader_share_cents": e.trader_share_cents,
        "firm_share_cents": e.firm_share_cents,
        "fee_refund_cents": e.fee_refund_cents,
        "trader_amount_cents": e.trader_amount_cents,
        "debit_cents": e.debit_cents,
        "period_start": e.period_start,
        "trading_days": e.trading_days,
        "opens_at": e.opens_at,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in e.checks],
    }
