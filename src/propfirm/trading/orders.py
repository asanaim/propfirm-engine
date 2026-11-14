"""Order requests, pending orders, trailing orders and positions.

The account runs in hedging mode: every fill opens its own position, and
positions are closed by id (fully or partly). No partial fills.

Trailing orders (M09, D43) run in the engine, so they keep working while the
trader is offline. With distance d and step s in integer points (each >= 1):
- a trailing buy (entry) follows the ask: its level starts at ask + d and
  fills when the ask reaches it;
- a trailing sell (entry) follows the bid: level bid - d, fills when the bid
  reaches it;
- a trailing stop on a buy position follows the bid: level bid - d, closes
  the position when the bid reaches it;
- a trailing stop on a sell position follows the ask: level ask + d, closes
  it when the ask reaches it.
A level moves only in the trader's favour (towards the price), and only when
the new value beats it by at least s. Every value is an integer number of
points, so no rounding is involved.
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import added_field


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    TRAILING = "trailing"  # a filled trailing entry; requests use TrailingOrder


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A request to open a position now (market) or when a level is reached.

    Attributes:
        order_id: Unique across the engine; also the id of the position it opens.
        volume: Hundredths of a lot (0.01 lot = 1).
        level: Trigger price in points, for limit and stop orders only.
        stop_loss, take_profit: Optional protective levels in points,
            carried to the position.
    """

    order_id: str
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType
    volume: int
    level: int | None = None
    stop_loss: int | None = None
    take_profit: int | None = None

    def __post_init__(self) -> None:
        require_text("order_id", self.order_id)
        require_text("account_id", self.account_id)
        require_text("symbol", self.symbol)
        if not isinstance(self.side, Side) or not isinstance(self.order_type, OrderType):
            raise TypeError("side and order_type must be enum members")
        require_int("volume", self.volume)
        if (self.order_type is OrderType.MARKET) != (self.level is None):
            raise ValueError("level is required for limit and stop orders only")
        for name in ("level", "stop_loss", "take_profit"):
            value = getattr(self, name)
            if value is not None:
                require_int(name, value, minimum=1)


class TrailingKind(StrEnum):
    ENTRY = "entry"  # trailing buy or trailing sell: opens a position
    STOP = "stop"  # trailing stop: closes a position


@dataclass(frozen=True, slots=True)
class TrailingOrder:
    """A working trailing order and its current level.

    Attributes:
        side: The side of the position: the one an entry opens, or the one a
            stop protects (a stop on a buy position sells at the bid).
        volume: Hundredths of a lot, entries only (a stop closes the whole
            remaining position).
        position_id: The protected position, stops only.
        distance, step: Points, each at least 1, set by the trader.
        level: The current trigger level in points.
        moved_at, moved_seq: Engine time and feed update id of the price that
            set the level (at placement or at its last move).
    """

    order_id: str
    account_id: str
    symbol: str
    side: Side
    kind: TrailingKind
    volume: int | None
    position_id: str | None
    distance: int
    step: int
    level: int
    moved_at: int
    moved_seq: int

    def __post_init__(self) -> None:
        require_text("order_id", self.order_id)
        require_text("account_id", self.account_id)
        require_text("symbol", self.symbol)
        if not isinstance(self.side, Side) or not isinstance(self.kind, TrailingKind):
            raise TypeError("side and kind must be enum members")
        if self.kind is TrailingKind.ENTRY:
            if self.volume is None or self.position_id is not None:
                raise ValueError("an entry has a volume and no position")
            require_int("volume", self.volume)
        else:
            if self.volume is not None or self.position_id is None:
                raise ValueError("a stop has a position and no volume")
            require_text("position_id", self.position_id)
        for name in ("distance", "step", "level"):
            require_int(name, getattr(self, name), minimum=1)
        require_int("moved_at", self.moved_at, minimum=0)
        require_int("moved_seq", self.moved_seq, minimum=1)

    @property
    def follows_ask(self) -> bool:
        """Buy-side orders (a trailing buy, a stop on a sell position) follow
        the ask; sell-side orders follow the bid: the price each fills at."""
        return (self.kind is TrailingKind.ENTRY) == (self.side is Side.BUY)


def trailing_level(follows_ask: bool, price: int, distance: int) -> int:
    """The level `distance` points behind `price`: above the ask for
    buy-side orders, below the bid for sell-side orders."""
    return price + distance if follows_ask else price - distance


def trailing_move(order: TrailingOrder, price: int) -> int | None:
    """The new level if `price` (ask or bid, per `follows_ask`) moves the
    level by at least the step in the trader's favour, else None."""
    candidate = trailing_level(order.follows_ask, price, order.distance)
    if order.follows_ask:
        return candidate if candidate <= order.level - order.step else None
    return candidate if candidate >= order.level + order.step else None


def trailing_reached(order: TrailingOrder, price: int) -> bool:
    """Whether `price` (ask or bid, per `follows_ask`) reaches the level."""
    return price >= order.level if order.follows_ask else price <= order.level


@dataclass(frozen=True, slots=True)
class PendingOrder:
    """A working order: a limit or stop request, or a trailing order."""

    request: OrderRequest | TrailingOrder
    placed_at: int


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    account_id: str
    symbol: str
    side: Side
    volume: int
    open_price: int
    opened_at: int
    stop_loss: int | None
    take_profit: int | None
    margin_usd: Fraction  # exact margin held for the remaining volume, fixed at open
    close_count: int = 0  # closes so far (partial or full); numbers close ids
    # swap-free add-on (D-13.18) only: nights of swap counted so far, charged
    # or free; 0 and left out of records for every other account
    swap_nights: int = added_field(0)
