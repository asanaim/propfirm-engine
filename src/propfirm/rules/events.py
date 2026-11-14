"""Events the rules supervisor adds to the engine's events. Every outcome
records the rule-set version (`rsv_id`) it was decided under."""

from dataclasses import dataclass
from enum import StrEnum


class BreachReason(StrEnum):
    DAILY_LOSS = "daily_loss"
    MAX_LOSS = "max_loss"
    NEWS = "news"  # a fill inside a restricted news window (RuleSet.news_breaches)


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PASSED = "passed"  # target met; read-only, awaiting review
    BREACHED = "breached"
    VOIDED = "voided"  # an integrity decision (a case confirmed by two employees)


@dataclass(frozen=True, slots=True)
class DayStarted:
    """A new trading day began for an account at `ts` (17:00 New York)."""

    ts: int
    account_id: str
    base_cents: int  # higher of balance and equity at the reset
    daily_floor_cents: int
    max_floor_cents: int
    rsv_id: str


@dataclass(frozen=True, slots=True)
class AccountBreached:
    """Equity reached a floor. Positions are closed and the account locked;
    the close-out events follow this one."""

    ts: int
    account_id: str
    reasons: tuple[BreachReason, ...]
    equity_cents: int
    daily_floor_cents: int
    max_floor_cents: int
    rsv_id: str


@dataclass(frozen=True, slots=True)
class StagePassed:
    ts: int
    account_id: str
    balance_cents: int
    trading_days: int
    rsv_id: str


@dataclass(frozen=True, slots=True)
class AccountVoided:
    """An integrity decision voided the account: its positions close at the
    current prices (the close-out events follow) and it is locked. `case`
    names the decision (a CAS id)."""

    ts: int
    account_id: str
    case: str
    equity_cents: int
    rsv_id: str


@dataclass(frozen=True, slots=True)
class PayoutDebited:
    """A payout's amount (the payable profit plus any profit removed for
    violations) left the account's balance. The day's base and the
    high-water mark drop by the same amount, so the payout itself never
    moves a loss floor closer (a trailing floor restarts from the balance
    after the payout [DEFAULT], D42)."""

    ts: int
    account_id: str
    payout: str  # PO id
    amount_cents: int
    balance_cents: int  # after the payout
    rsv_id: str


@dataclass(frozen=True, slots=True)
class NewsViolation:
    """A fill inside a restricted news window on a news-restricted account
    (M13). The engine refuses every order and close there, so only a
    stop-loss, take-profit or trailing stop can fill (breach and void
    close-outs are exempt). As FTMO, it does not change the figures; it
    raises an integrity case, which can end the account [decided 31 Oct
    2026]."""

    ts: int
    account_id: str
    position_id: str
    close_id: str
    symbol: str
    reason: str  # the close reason: stop_loss, take_profit or trailing_stop
    pnl_cents: int
    event_ids: tuple[str, ...]  # the restricted events whose window covered it
    rsv_id: str


@dataclass(frozen=True, slots=True)
class BalanceCorrected:
    """A bad price's effect on the account reversed (D-14.4): `amount_cents`
    (signed) added to the balance; `correction` names the decision (a COR
    id, approved by two people). The day's base moves by the same amount,
    so the correction is not counted as today's profit or loss."""

    ts: int
    account_id: str
    correction: str
    amount_cents: int
    balance_cents: int
    rsv_id: str


@dataclass(frozen=True, slots=True)
class AccountReinstated:
    """A breach caused by a bad price is undone (D-14.4): the account is
    active and unlocked again; positions the breach closed stay closed."""

    ts: int
    account_id: str
    correction: str
    rsv_id: str


@dataclass(frozen=True, slots=True)
class AccountScaled:
    """A scaling step (D-14.8): `add_cents` of allocation added to the
    balance and to the starting balance the loss limits are measured from;
    `scaling` names the approved step (an SCL id)."""

    ts: int
    account_id: str
    scaling: str
    add_cents: int
    initial_balance_cents: int
    balance_cents: int
    rsv_id: str


type RuleEvent = (
    DayStarted
    | AccountBreached
    | StagePassed
    | AccountVoided
    | PayoutDebited
    | NewsViolation
    | BalanceCorrected
    | AccountReinstated
    | AccountScaled
)
