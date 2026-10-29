"""Order requests, pending orders and positions.

The account runs in hedging mode: every fill opens its own position, and
positions are closed by id (fully or partly). No partial fills.

Trailing orders (CC-3, D43): the trader sets the distance and step of each
trailing level in whole points of the symbol. A trailing entry (trailing buy
or trailing sell) is a pending order whose level follows the market; a
trailing stop follows an open position. The engine moves the levels
(`propfirm.trading.engine`).
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from propfirm._checks import require_int, require_text


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
    TRAILING = "trailing"  # trailing entry: a stop order whose level follows the market


@dataclass(frozen=True, slots=True)
class Trail:
    """A trailing level's settings, in whole points of the symbol.

    Attributes:
        distance: How far the level stays behind the best price since it was set.
        step: The least improvement that moves the level; 1 moves it on every
            better price.
    """

    distance: int
    step: int

    def __post_init__(self) -> None:
        require_int("distance", self.distance, minimum=1)
        require_int("step", self.step, minimum=1)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A request to open a position now (market) or when a level is reached.

    Attributes:
        order_id: Unique across the engine; also the id of the position it opens.
        volume: Hundredths of a lot (0.01 lot = 1).
        level: Trigger price in points, for limit and stop orders only.
        stop_loss, take_profit: Optional protective levels in points,
            carried to the position.
        trail: The trailing entry's settings, for trailing orders only.
        trailing_stop: Optional trailing stop, set on the position when it opens.
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
    trail: Trail | None = None
    trailing_stop: Trail | None = None

    def __post_init__(self) -> None:
        require_text("order_id", self.order_id)
        require_text("account_id", self.account_id)
        require_text("symbol", self.symbol)
        if not isinstance(self.side, Side) or not isinstance(self.order_type, OrderType):
            raise TypeError("side and order_type must be enum members")
        require_int("volume", self.volume)
        has_level = self.order_type in (OrderType.LIMIT, OrderType.STOP)
        if has_level == (self.level is None):
            raise ValueError("level is required for limit and stop orders only")
        if (self.order_type is OrderType.TRAILING) != (self.trail is not None):
            raise ValueError("trail is required for trailing orders only")
        for name in ("trail", "trailing_stop"):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), Trail):
                raise TypeError(f"{name} must be a Trail")
        for name in ("level", "stop_loss", "take_profit"):
            value = getattr(self, name)
            if value is not None:
                require_int(name, value, minimum=1)


@dataclass(frozen=True, slots=True)
class PendingOrder:
    request: OrderRequest
    placed_at: int
    level: int  # trigger price in points; moves for trailing orders


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
    trailing_stop: Trail | None = None
    trailing_level: int | None = None  # current trailing stop price, None without one
