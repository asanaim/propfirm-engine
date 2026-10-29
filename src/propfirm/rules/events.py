"""Events the rules supervisor adds to the engine's events. Every outcome
records the rule-set version (`rsv_id`) it was decided under."""

from dataclasses import dataclass
from enum import StrEnum

from propfirm.trading.events import CloseReason


class BreachReason(StrEnum):
    DAILY_LOSS = "daily_loss"
    MAX_LOSS = "max_loss"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PASSED = "passed"  # target met; read-only, awaiting review
    BREACHED = "breached"


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
class NewsExitViolation:
    """A stop-loss, take-profit or trailing stop closed a position inside a
    news trade block on its symbol (D15). The fill stands; `excluded_cents`
    is the profit kept out of the profit target (0 for a loss, or when the
    rule set does not exclude it)."""

    ts: int
    account_id: str
    close_id: str
    symbol: str
    reason: CloseReason
    pnl_cents: int
    excluded_cents: int
    rsv_id: str


type RuleEvent = DayStarted | AccountBreached | StagePassed | NewsExitViolation
