"""Events the engine emits. The ordered list of events is the engine's
complete observable output for a given input sequence.

`ts` is always engine time in ms UTC; on PositionOpened and PositionClosed it
is the fill time. Fills also record the feed update they used (sequence
number, source time, receive time).
"""

from dataclasses import dataclass
from enum import StrEnum

from propfirm.market.guards import FreezeReason
from propfirm.trading.orders import OrderType, Side, TrailingKind


class RejectReason(StrEnum):
    UNKNOWN_ACCOUNT = "unknown_account"
    UNKNOWN_SYMBOL = "unknown_symbol"
    DUPLICATE_ID = "duplicate_id"
    SYMBOL_FROZEN = "symbol_frozen"
    INVALID_VOLUME = "invalid_volume"
    ORDER_VOLUME_CAP = "order_volume_cap"
    SYMBOL_VOLUME_CAP = "symbol_volume_cap"
    INVALID_LEVEL = "invalid_level"
    INVALID_STOPS = "invalid_stops"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    NO_CONVERSION_RATE = "no_conversion_rate"
    UNKNOWN_POSITION = "unknown_position"
    UNKNOWN_ORDER = "unknown_order"
    ACCOUNT_LOCKED = "account_locked"
    NEWS_WINDOW = "news_window"
    INVALID_TRAILING = "invalid_trailing"  # distance or step below 1, or no level >= 1
    POSITION_CLOSED = "position_closed"  # a trailing stop whose position closed


class CloseReason(StrEnum):
    CLIENT = "client"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    BREACH = "breach"
    VOID = "void"
    TRAILING_STOP = "trailing_stop"


@dataclass(frozen=True, slots=True)
class AccountOpened:
    ts: int
    account_id: str
    initial_balance_cents: int
    config_version: str


@dataclass(frozen=True, slots=True)
class OrderRejected:
    ts: int
    ref: str  # order or position id the request named
    account_id: str
    reason: RejectReason
    config_version: str


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    ts: int
    order_id: str
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType
    volume: int
    level: int


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    ts: int
    order_id: str
    account_id: str
    reason: RejectReason | None  # None: cancelled by the trader


@dataclass(frozen=True, slots=True)
class PositionOpened:
    ts: int
    position_id: str
    account_id: str
    symbol: str
    side: Side
    volume: int
    order_type: OrderType
    price: int
    price_seq: int  # feed update the fill used (with its source and receive times)
    price_source_ts: int
    price_receive_ts: int
    margin_cents: int
    config_version: str


@dataclass(frozen=True, slots=True)
class PositionClosed:
    ts: int
    position_id: str
    close_id: str
    account_id: str
    symbol: str
    side: Side
    volume: int
    open_price: int
    close_price: int
    price_seq: int
    price_source_ts: int
    price_receive_ts: int
    pnl_cents: int
    remaining_volume: int
    reason: CloseReason
    config_version: str


@dataclass(frozen=True, slots=True)
class CommissionCharged:
    """The commission of a position that just opened, taken from the
    account's balance (follows its PositionOpened)."""

    ts: int
    position_id: str
    account_id: str
    symbol: str
    notional_cents: int  # at the fill price, rounded half-even (for the record)
    commission_cents: int  # exact share of the exact notional, rounded half-even
    config_version: str


@dataclass(frozen=True, slots=True)
class StopsModified:
    ts: int
    position_id: str
    account_id: str
    stop_loss: int | None
    take_profit: int | None


@dataclass(frozen=True, slots=True)
class TrailingPlaced:
    """A trailing order started working at `level`, set from the price
    update `price_seq` (an entry: `volume`; a stop: `position_id`)."""

    ts: int
    order_id: str
    account_id: str
    symbol: str
    side: Side  # the position's side (opened by an entry, protected by a stop)
    kind: TrailingKind
    volume: int | None
    position_id: str | None
    distance: int
    step: int
    level: int
    price_seq: int
    config_version: str


@dataclass(frozen=True, slots=True)
class TrailingMoved:
    """A trailing order's level moved to `level`, caused by price update
    `price_seq` (replay reproduces every move)."""

    ts: int
    order_id: str
    account_id: str
    level: int
    price_seq: int


@dataclass(frozen=True, slots=True)
class SymbolFrozen:
    ts: int
    symbol: str
    reason: FreezeReason


@dataclass(frozen=True, slots=True)
class SymbolResumed:
    ts: int
    symbol: str


@dataclass(frozen=True, slots=True)
class FeedGap:
    ts: int
    symbol: str
    missing_from: int
    missing_to: int


@dataclass(frozen=True, slots=True)
class DuplicateUpdate:
    ts: int
    symbol: str
    seq: int


@dataclass(frozen=True, slots=True)
class StaleUpdate:
    """A delayed update that was recorded but not applied."""

    ts: int
    symbol: str
    seq: int
    lag_ms: int


@dataclass(frozen=True, slots=True)
class NewsScheduled:
    """A restricted event entered the calendar: no trades on `symbols` and
    wider spreads while engine time is in [start_ts, end_ts)."""

    ts: int
    event_id: str
    symbols: tuple[str, ...]  # sorted
    start_ts: int
    end_ts: int


@dataclass(frozen=True, slots=True)
class NewsCancelled:
    ts: int
    event_id: str


@dataclass(frozen=True, slots=True)
class TradingConfigChanged:
    """New trading settings apply from `ts`: leverage, lot caps and spread
    parameters of `symbols`, and the feed-guard thresholds when
    `guard_changed`. Open positions keep the margin fixed when they opened."""

    ts: int
    config_version: str
    symbols: tuple[str, ...]  # sorted
    guard_changed: bool


type EngineEvent = (
    AccountOpened
    | OrderRejected
    | OrderPlaced
    | OrderCancelled
    | PositionOpened
    | PositionClosed
    | CommissionCharged
    | StopsModified
    | TrailingPlaced
    | TrailingMoved
    | SymbolFrozen
    | SymbolResumed
    | FeedGap
    | DuplicateUpdate
    | StaleUpdate
    | NewsScheduled
    | NewsCancelled
    | TradingConfigChanged
)
