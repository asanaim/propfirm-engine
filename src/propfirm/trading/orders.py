"""Order requests, pending orders and positions.

The account runs in hedging mode: every fill opens its own position, and
positions are closed by id (fully or partly). No partial fills.
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


@dataclass(frozen=True, slots=True)
class PendingOrder:
    request: OrderRequest
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
