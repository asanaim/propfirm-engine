"""Events the engine emits. The ordered list of events is the engine's
complete observable output for a given input sequence.

`ts` is always engine time in ms UTC; on PositionOpened and PositionClosed it
is the fill time. Fills also record the feed update they used (sequence
number, source time, receive time).
"""

from dataclasses import dataclass
from enum import StrEnum

from propfirm.market.guards import FreezeReason
from propfirm.trading.orders import OrderType, Side


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


class CloseReason(StrEnum):
    CLIENT = "client"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


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
class StopsModified:
    ts: int
    position_id: str
    account_id: str
    stop_loss: int | None
    take_profit: int | None


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


type EngineEvent = (
    AccountOpened
    | OrderRejected
    | OrderPlaced
    | OrderCancelled
    | PositionOpened
    | PositionClosed
    | StopsModified
    | SymbolFrozen
    | SymbolResumed
    | FeedGap
    | DuplicateUpdate
    | StaleUpdate
)
