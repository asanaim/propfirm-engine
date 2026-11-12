"""Open positions and working orders, indexed (review D3, 12 Nov 2026).

The engine kept positions and orders in one dict each and scanned all of
them for every price and every account, so the work per price update grew
with the square of the number of accounts. These are the same dicts, in
the same order (opening and placement order: it decides event order, so
replays stay identical), with indexes kept up to date on every write:

- positions by account and by symbol;
- orders by symbol and by account, and each position's trailing stop.

Each index keeps the main order: an index is a dict of ids, filled in the
order the ids entered the main dict, and an id replaced in place (a moved
stop, a partial close) keeps its place in both. Only item assignment and
`del` change a book; the other dict mutators are refused, so no write can
skip the indexes.
"""

from collections.abc import Iterable
from typing import Any, NoReturn

from propfirm.trading.orders import PendingOrder, Position, TrailingKind, TrailingOrder


def _refuse(*_: Any, **__: Any) -> NoReturn:
    raise TypeError("books change only by item assignment and del")


class _Book[V](dict[str, V]):
    pop = popitem = clear = update = setdefault = _refuse

    def __ior__(self, other: Any) -> NoReturn:  # type: ignore[misc]
        raise TypeError("books change only by item assignment and del")


def _add(index: dict[str, dict[str, None]], key: str, item_id: str) -> None:
    index.setdefault(key, {})[item_id] = None


def _remove(index: dict[str, dict[str, None]], key: str, item_id: str) -> None:
    ids = index[key]
    del ids[item_id]
    if not ids:
        del index[key]


class PositionBook(_Book[Position]):
    """Open positions in opening order, indexed by account and symbol."""

    def __init__(self, positions: Iterable[Position] = ()) -> None:
        super().__init__()
        self._by_account: dict[str, dict[str, None]] = {}
        self._by_symbol: dict[str, dict[str, None]] = {}
        for p in positions:
            self[p.position_id] = p

    def __setitem__(self, position_id: str, position: Position) -> None:
        if position.position_id != position_id:
            raise ValueError("a position is stored under its own id")
        old = self.get(position_id)
        if old is not None:
            if (old.account_id, old.symbol) != (position.account_id, position.symbol):
                raise ValueError("a position keeps its account and symbol")
        else:
            _add(self._by_account, position.account_id, position_id)
            _add(self._by_symbol, position.symbol, position_id)
        super().__setitem__(position_id, position)

    def __delitem__(self, position_id: str) -> None:
        position = self[position_id]
        super().__delitem__(position_id)
        _remove(self._by_account, position.account_id, position_id)
        _remove(self._by_symbol, position.symbol, position_id)

    def of_account(self, account_id: str) -> list[Position]:
        return [self[i] for i in self._by_account.get(account_id, ())]

    def on_symbol(self, symbol: str) -> list[Position]:
        return [self[i] for i in self._by_symbol.get(symbol, ())]

    def accounts_on(self, symbol: str) -> list[str]:
        """Accounts holding `symbol`, each once, in the order of their first
        position on it."""
        return list(dict.fromkeys(self[i].account_id for i in self._by_symbol.get(symbol, ())))


class OrderBook(_Book[PendingOrder]):
    """Working orders in placement order, indexed by symbol and account,
    with each position's trailing stop."""

    def __init__(self, orders: Iterable[PendingOrder] = ()) -> None:
        super().__init__()
        self._by_symbol: dict[str, dict[str, None]] = {}
        self._by_account: dict[str, dict[str, None]] = {}
        self._stops: dict[str, str] = {}  # position id -> trailing stop order id
        for o in orders:
            self[o.request.order_id] = o

    def __setitem__(self, order_id: str, pending: PendingOrder) -> None:
        request = pending.request
        if request.order_id != order_id:
            raise ValueError("an order is stored under its own id")
        old = self.get(order_id)
        if old is not None:
            before = old.request
            if (before.account_id, before.symbol, _stop_of(before)) != (
                request.account_id,
                request.symbol,
                _stop_of(request),
            ):
                raise ValueError("an order keeps its account, symbol and position")
        else:
            position = _stop_of(request)
            if position is not None:
                if position in self._stops:
                    raise ValueError(f"position {position} already has a trailing stop")
                self._stops[position] = order_id
            _add(self._by_symbol, request.symbol, order_id)
            _add(self._by_account, request.account_id, order_id)
        super().__setitem__(order_id, pending)

    def __delitem__(self, order_id: str) -> None:
        request = self[order_id].request
        super().__delitem__(order_id)
        _remove(self._by_symbol, request.symbol, order_id)
        _remove(self._by_account, request.account_id, order_id)
        position = _stop_of(request)
        if position is not None:
            del self._stops[position]

    def on_symbol(self, symbol: str) -> list[tuple[str, PendingOrder]]:
        return [(i, self[i]) for i in self._by_symbol.get(symbol, ())]

    def of_account(self, account_id: str) -> list[PendingOrder]:
        return [self[i] for i in self._by_account.get(account_id, ())]

    def stop_of(self, position_id: str) -> TrailingOrder | None:
        order_id = self._stops.get(position_id)
        if order_id is None:
            return None
        request = self[order_id].request
        assert isinstance(request, TrailingOrder)
        return request


def _stop_of(request: object) -> str | None:
    """The position a trailing stop protects; None for any other order."""
    if isinstance(request, TrailingOrder) and request.kind is TrailingKind.STOP:
        return request.position_id
    return None
