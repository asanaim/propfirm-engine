"""The order engine: prices, orders, positions, fills, margin and P&L.

Every input carries its own time; the engine never reads a clock, so the same
inputs always give the same events and ledger (deterministic replay). Engine
time is the latest input time seen.

Fill rules:
- [CONFIRMED] A market order fills at the bid (sell) or ask (buy) of the
  latest applied price update when the engine receives it.
- [CONFIRMED] A pending order triggers when the bid (sells) or ask (buys)
  crosses its level: buy limit ask <= level, buy stop ask >= level, sell
  limit bid >= level, sell stop bid <= level. A trailing entry triggers like
  a stop order at its current level.
- [DEFAULT] A triggered order, stop-loss or take-profit fills at the bid or
  ask of the update that triggered it. When a price gaps past a level, that
  is the actual price, not the level; no other slippage model in v1.
- [DEFAULT] No partial fills. Positions can be closed fully or partly.
- Every fill records the feed update it used: sequence number, source time
  and receive time.

Trailing orders (CC-3, D43). The trader sets distance d and step s in
points [CONFIRMED]. Each level follows the price its order would trade at:
the bid for sells, the ask for buys [DEFAULT].
- Trailing stop on a buy position: highest bid since it was set minus d;
  closes the position when the bid falls to it. On a sell position: lowest
  ask plus d; closes when the ask rises to it.
- Trailing buy: lowest ask since placement plus d; fills when the ask rises
  to it. Trailing sell: highest bid minus d; fills when the bid falls to it.
- A level moves only towards the market, and only when the new value beats
  it by at least s; every move is a `TrailingLevelMoved` event naming the
  update that caused it. Each applied update first moves levels, then
  checks triggers.
- A fixed stop-loss can sit beside a trailing stop; the first reached
  closes the position (both on one update: the stop-loss is named).
- [DEFAULT] A trailing entry's fixed stop-loss and take-profit are checked
  again when it triggers, against the exit price at the fill; if they are
  then on the wrong side the order is cancelled (INVALID_STOPS), because
  its fill price was unknown when it was placed.
- A level that would start below 1 point is rejected (INVALID_LEVEL for an
  entry, INVALID_STOPS for a stop).

Guards (acceptance test 5): while a symbol is frozen it takes no new orders,
closes or stop changes; its pending orders, stop-losses, take-profits and
trailing levels are held, because frozen prices are never applied. Cancelling a pending order is
always allowed. On the first fresh update the symbol resumes and anything that
update crosses fills at its price.

News trade blocks (Confirmed Configuration CC-3): inside a block on an
affected symbol no trade is opened or closed: market orders, new pending
orders and manual closes are rejected, and pending orders do not trigger
(they are held until the block ends) [DEFAULT]. Stop-losses, take-profits
and trailing stops still execute [DEFAULT]; the rules supervisor records
those exits as news violations (D15). Trailing levels keep moving, and
changing stops is allowed.

Rules hooks (step 5): `close_all` closes every position of an account at the
current prices, ignoring freezes and news blocks (breach close-out), and
`lock_account` cancels its pending orders and rejects all further trading.

Money:
- Account currency USD; balances are ledger balances in integer cents.
- P&L and margin are exact rationals until rounded: P&L half-even to the
  cent, margin up to the cent (`propfirm.money`).
- Non-USD amounts convert through the feed price (midpoint) of the
  CUR/USD or USD/CUR symbol, at the time of the calculation.
- [DEFAULT] Margin = notional / leverage at the fill price, fixed at open
  and released pro rata on partial closes. Checked when a position opens;
  pending orders reserve none. Hedged positions are margined gross.
- Equity = balance + unrealized P&L (longs at bid, shorts at ask).
- Not implemented: margin call and stop-out (not in the project record; the
  loss limits of the rules engine, step 5, act far earlier).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction

from propfirm._checks import is_int, require_int, require_text
from propfirm.ledger import Ledger, transfer
from propfirm.market.guards import FeedGuards, GuardChange, GuardConfig
from propfirm.market.sequence import SeqStatus, SequenceTracker
from propfirm.market.spread import NewsWindow, Quote, SpreadModel, in_news
from propfirm.market.types import PriceUpdate
from propfirm.money import cents_ceil, cents_half_even
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    DuplicateUpdate,
    EngineEvent,
    FeedGap,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
    StaleUpdate,
    StopsModified,
    SymbolFrozen,
    SymbolResumed,
    TrailingLevelMoved,
    TrailingStopSet,
)
from propfirm.trading.instruments import InstrumentSpec, conversion_route, scaled_cap
from propfirm.trading.orders import OrderRequest, OrderType, PendingOrder, Position, Side, Trail

FIRM_CAPITAL = "firm:capital"
FIRM_TRADING_PNL = "firm:trading_pnl"


def balance_account(account_id: str) -> str:
    """Ledger account holding a trading account's balance (debit-positive)."""
    return f"acc:{account_id}:balance"


@dataclass(frozen=True, slots=True)
class _Account:
    account_id: str
    initial_balance: int


class _NoRate(Exception):
    """A conversion rate symbol has no applied price yet."""


class TradingEngine:
    def __init__(
        self,
        instruments: Mapping[str, InstrumentSpec],
        ledger: Ledger,
        *,
        config_version: str,
        guard_config: GuardConfig | None = None,
        news_windows: Iterable[NewsWindow] = (),
        trade_blocks: Iterable[NewsWindow] = (),
    ) -> None:
        """`news_windows` widen spreads; `trade_blocks` stop trading on the
        affected symbols (both built from the restricted-events calendar)."""
        if not instruments:
            raise ValueError("instruments must not be empty")
        self._instruments = dict(instruments)
        self._ledger = ledger
        self._version = require_text("config_version", config_version)
        self._guards = FeedGuards(guard_config or GuardConfig(), self._instruments)
        self._sequences = SequenceTracker()
        self._spreads = {s: SpreadModel(spec.spread) for s, spec in self._instruments.items()}
        self._news = tuple(news_windows)
        self._blocks = tuple(trade_blocks)
        self._routes: dict[str, tuple[str, bool]] = {}
        for spec in self._instruments.values():
            currency = spec.quote_currency
            if currency != "USD" and currency not in self._routes:
                self._routes[currency] = conversion_route(currency, self._instruments)
        self._quotes: dict[str, Quote] = {}
        self._accounts: dict[str, _Account] = {}
        self._orders: dict[str, PendingOrder] = {}  # pending, in placement order
        self._positions: dict[str, Position] = {}  # open, in opening order
        self._used_ids: set[str] = set()
        self._locked: set[str] = set()
        self._now = 0

    # --- inputs -------------------------------------------------------------

    def open_account(
        self, account_id: str, initial_balance_cents: int, ts: int
    ) -> list[EngineEvent]:
        """Fund a new trading account through the ledger."""
        require_text("account_id", account_id)
        require_int("initial_balance_cents", initial_balance_cents, minimum=1)
        if account_id in self._accounts:
            raise ValueError(f"account {account_id!r} already exists")
        events = self._advance(ts)
        self._ledger.post(
            transfer(
                f"fund:{account_id}",
                self._now,
                "account_funding",
                debit=balance_account(account_id),
                credit=FIRM_CAPITAL,
                amount=initial_balance_cents,
                reference=account_id,
            )
        )
        self._accounts[account_id] = _Account(account_id, initial_balance_cents)
        events.append(AccountOpened(self._now, account_id, initial_balance_cents, self._version))
        return events

    def on_price(self, update: PriceUpdate) -> list[EngineEvent]:
        """Process one feed update."""
        symbol = update.symbol
        if symbol not in self._instruments:
            raise KeyError(f"feed sent unconfigured symbol {symbol!r}")
        events = self._advance(update.receive_ts)
        seq = self._sequences.observe(symbol, update.seq)
        if seq.status is SeqStatus.DUPLICATE:
            events.append(DuplicateUpdate(self._now, symbol, update.seq))
            return events
        if seq.status is SeqStatus.GAP:
            assert seq.missing_from is not None and seq.missing_to is not None
            events.append(FeedGap(self._now, symbol, seq.missing_from, seq.missing_to))
        apply, changes = self._guards.on_update(update)
        events.extend(_guard_event(c) for c in changes)
        if not apply:
            lag = update.receive_ts - update.source_ts
            events.append(StaleUpdate(self._now, symbol, update.seq, lag))
            return events
        model = self._spreads[symbol]
        model.observe(update.price)
        quote = model.quote(
            symbol,
            update.price,
            update.seq,
            update.source_ts,
            update.receive_ts,
            in_news(self._news, symbol, self._now),
        )
        self._quotes[symbol] = quote
        events.extend(self._trail(quote))
        events.extend(self._trigger_orders(quote))
        events.extend(self._trigger_stops(quote))
        return events

    def advance(self, ts: int) -> list[EngineEvent]:
        """Move engine time forward; freezes symbols whose updates are overdue."""
        return self._advance(ts)

    def on_outage(self, ts: int) -> list[EngineEvent]:
        """The feed connection is down: freeze every symbol."""
        events = self._advance(ts)
        events.extend(_guard_event(c) for c in self._guards.on_outage(self._now))
        return events

    def place_order(self, request: OrderRequest, ts: int) -> list[EngineEvent]:
        events = self._advance(ts)
        reason = self._check_new_order(request)
        if reason is not None:
            events.append(self._reject(request.order_id, request.account_id, reason))
            return events
        quote = self._quotes[request.symbol]
        if request.order_type is OrderType.MARKET:
            opened = self._open_position(request, quote)
            if isinstance(opened, RejectReason):
                events.append(self._reject(request.order_id, request.account_id, opened))
            else:
                self._used_ids.add(request.order_id)
                events.extend(opened)
            return events
        level = _entry_level(request, quote)
        self._orders[request.order_id] = PendingOrder(request, self._now, level)
        self._used_ids.add(request.order_id)
        events.append(
            OrderPlaced(
                self._now,
                request.order_id,
                request.account_id,
                request.symbol,
                request.side,
                request.order_type,
                request.volume,
                level,
                request.trail,
            )
        )
        return events

    def cancel_order(self, account_id: str, order_id: str, ts: int) -> list[EngineEvent]:
        events = self._advance(ts)
        pending = self._orders.get(order_id)
        if pending is None or pending.request.account_id != account_id:
            events.append(self._reject(order_id, account_id, RejectReason.UNKNOWN_ORDER))
            return events
        del self._orders[order_id]
        events.append(OrderCancelled(self._now, order_id, account_id, None))
        return events

    def close_position(
        self, account_id: str, position_id: str, ts: int, volume: int | None = None
    ) -> list[EngineEvent]:
        """Close a position fully (volume None) or partly, at the current price."""
        events = self._advance(ts)
        position = self._positions.get(position_id)
        if position is None or position.account_id != account_id:
            events.append(self._reject(position_id, account_id, RejectReason.UNKNOWN_POSITION))
            return events
        if account_id in self._locked:
            events.append(self._reject(position_id, account_id, RejectReason.ACCOUNT_LOCKED))
            return events
        if self._guards.is_frozen(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.SYMBOL_FROZEN))
            return events
        if in_news(self._blocks, position.symbol, self._now):
            events.append(self._reject(position_id, account_id, RejectReason.NEWS_WINDOW))
            return events
        amount = position.volume if volume is None else volume
        if not is_int(amount) or not 1 <= amount <= position.volume:
            events.append(self._reject(position_id, account_id, RejectReason.INVALID_VOLUME))
            return events
        quote = self._quotes[position.symbol]
        events.append(self._close(position, amount, quote, CloseReason.CLIENT))
        return events

    def modify_stops(
        self,
        account_id: str,
        position_id: str,
        stop_loss: int | None,
        take_profit: int | None,
        ts: int,
    ) -> list[EngineEvent]:
        """Replace a position's stop-loss and take-profit (None removes one)."""
        events = self._advance(ts)
        position = self._positions.get(position_id)
        if position is None or position.account_id != account_id:
            events.append(self._reject(position_id, account_id, RejectReason.UNKNOWN_POSITION))
            return events
        if account_id in self._locked:
            events.append(self._reject(position_id, account_id, RejectReason.ACCOUNT_LOCKED))
            return events
        if self._guards.is_frozen(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.SYMBOL_FROZEN))
            return events
        quote = self._quotes[position.symbol]
        exit_price = quote.bid if position.side is Side.BUY else quote.ask
        if not _valid_stops(position.side, exit_price, stop_loss, take_profit):
            events.append(self._reject(position_id, account_id, RejectReason.INVALID_STOPS))
            return events
        self._positions[position_id] = replace(
            position, stop_loss=stop_loss, take_profit=take_profit
        )
        events.append(StopsModified(self._now, position_id, account_id, stop_loss, take_profit))
        return events

    def set_trailing_stop(
        self, account_id: str, position_id: str, trail: Trail | None, ts: int
    ) -> list[EngineEvent]:
        """Set, replace or (trail None) remove a position's trailing stop. A new
        stop starts from the current exit price."""
        if trail is not None and not isinstance(trail, Trail):
            raise TypeError("trail must be a Trail or None")
        events = self._advance(ts)
        position = self._positions.get(position_id)
        if position is None or position.account_id != account_id:
            events.append(self._reject(position_id, account_id, RejectReason.UNKNOWN_POSITION))
            return events
        if account_id in self._locked:
            events.append(self._reject(position_id, account_id, RejectReason.ACCOUNT_LOCKED))
            return events
        if self._guards.is_frozen(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.SYMBOL_FROZEN))
            return events
        quote = self._quotes[position.symbol]
        level = None
        if trail is not None:
            level = _trail_start(_exit_side(position.side), quote, trail.distance)
            if level < 1:
                events.append(self._reject(position_id, account_id, RejectReason.INVALID_STOPS))
                return events
        self._positions[position_id] = replace(position, trailing_stop=trail, trailing_level=level)
        events.append(TrailingStopSet(self._now, position_id, account_id, trail, level, quote.seq))
        return events

    def close_all(self, account_id: str, ts: int, reason: CloseReason) -> list[EngineEvent]:
        """Close every position of an account at the current prices. Ignores
        freezes and news blocks: a frozen symbol closes at its last applied
        price (traceable through `price_seq`)."""
        self._account(account_id)
        events = self._advance(ts)
        for position in self._open(account_id):
            events.append(
                self._close(position, position.volume, self._quotes[position.symbol], reason)
            )
        return events

    def lock_account(self, account_id: str, ts: int) -> list[EngineEvent]:
        """Cancel the account's pending orders and reject all further trading."""
        self._account(account_id)
        events = self._advance(ts)
        for order_id, pending in list(self._orders.items()):
            if pending.request.account_id == account_id:
                del self._orders[order_id]
                events.append(
                    OrderCancelled(self._now, order_id, account_id, RejectReason.ACCOUNT_LOCKED)
                )
        self._locked.add(account_id)
        return events

    # --- queries ------------------------------------------------------------

    @property
    def now(self) -> int:
        return self._now

    @property
    def symbols(self) -> frozenset[str]:
        """The configured symbols."""
        return frozenset(self._instruments)

    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    def is_frozen(self, symbol: str) -> bool:
        return self._guards.is_frozen(symbol)

    def is_blocked(self, symbol: str) -> bool:
        """Whether a news trade block covers `symbol` at engine time."""
        return in_news(self._blocks, symbol, self._now)

    def is_locked(self, account_id: str) -> bool:
        self._account(account_id)
        return account_id in self._locked

    def balance(self, account_id: str) -> int:
        self._account(account_id)
        return self._ledger.balance(balance_account(account_id))

    def unrealized_pnl(self, account_id: str) -> int:
        """Unrealized P&L in cents: exact sum over positions, rounded once."""
        self._account(account_id)
        return cents_half_even(
            sum(
                (self._pnl_usd(p, self._exit_price(p), p.volume) for p in self._open(account_id)),
                Fraction(0),
            )
        )

    def equity(self, account_id: str) -> int:
        return self.balance(account_id) + self.unrealized_pnl(account_id)

    def used_margin(self, account_id: str) -> int:
        self._account(account_id)
        return cents_ceil(sum((p.margin_usd for p in self._open(account_id)), Fraction(0)))

    def free_margin(self, account_id: str) -> int:
        return self.equity(account_id) - self.used_margin(account_id)

    def positions(self, account_id: str) -> tuple[Position, ...]:
        self._account(account_id)
        return tuple(self._open(account_id))

    def pending_orders(self, account_id: str) -> tuple[PendingOrder, ...]:
        self._account(account_id)
        return tuple(o for o in self._orders.values() if o.request.account_id == account_id)

    # --- internals ----------------------------------------------------------

    def _advance(self, ts: int) -> list[EngineEvent]:
        require_int("ts", ts, minimum=0)
        self._now = max(self._now, ts)
        return [_guard_event(c) for c in self._guards.advance(self._now)]

    def _account(self, account_id: str) -> _Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise KeyError(f"unknown account {account_id!r}") from None

    def _open(self, account_id: str) -> list[Position]:
        return [p for p in self._positions.values() if p.account_id == account_id]

    def _reject(self, ref: str, account_id: str, reason: RejectReason) -> OrderRejected:
        return OrderRejected(self._now, ref, account_id, reason, self._version)

    def _check_new_order(self, request: OrderRequest) -> RejectReason | None:
        account = self._accounts.get(request.account_id)
        if account is None:
            return RejectReason.UNKNOWN_ACCOUNT
        if request.account_id in self._locked:
            return RejectReason.ACCOUNT_LOCKED
        if request.order_id in self._used_ids or request.order_id in self._positions:
            return RejectReason.DUPLICATE_ID
        spec = self._instruments.get(request.symbol)
        if spec is None:
            return RejectReason.UNKNOWN_SYMBOL
        if self._guards.is_frozen(request.symbol):
            return RejectReason.SYMBOL_FROZEN
        if in_news(self._blocks, request.symbol, self._now):
            return RejectReason.NEWS_WINDOW
        if request.volume < 1:
            return RejectReason.INVALID_VOLUME
        if request.volume > scaled_cap(spec.max_order_volume_per_100k, account.initial_balance):
            return RejectReason.ORDER_VOLUME_CAP
        exposure = request.volume + self._symbol_volume(request.account_id, request.symbol)
        if exposure > scaled_cap(spec.max_symbol_volume_per_100k, account.initial_balance):
            return RejectReason.SYMBOL_VOLUME_CAP
        quote = self._quotes[request.symbol]
        if request.order_type is OrderType.MARKET:
            exit_price = quote.bid if request.side is Side.BUY else quote.ask
            if not _valid_stops(request.side, exit_price, request.stop_loss, request.take_profit):
                return RejectReason.INVALID_STOPS
            return None
        level = _entry_level(request, quote)
        if request.order_type is OrderType.TRAILING:
            if level < 1:
                return RejectReason.INVALID_LEVEL
        elif not _valid_level(request.side, request.order_type, level, quote):
            return RejectReason.INVALID_LEVEL
        if not _valid_stops(request.side, level, request.stop_loss, request.take_profit):
            return RejectReason.INVALID_STOPS
        return None

    def _symbol_volume(self, account_id: str, symbol: str) -> int:
        open_volume = sum(
            p.volume
            for p in self._positions.values()
            if p.account_id == account_id and p.symbol == symbol
        )
        pending_volume = sum(
            o.request.volume
            for o in self._orders.values()
            if o.request.account_id == account_id and o.request.symbol == symbol
        )
        return open_volume + pending_volume

    def _open_position(
        self, request: OrderRequest, quote: Quote
    ) -> list[EngineEvent] | RejectReason:
        """Open the position `request` asks for at `quote`: the PositionOpened
        event, then TrailingStopSet when the request carries a trailing stop."""
        spec = self._instruments[request.symbol]
        price = quote.ask if request.side is Side.BUY else quote.bid
        exit_price = quote.bid if request.side is Side.BUY else quote.ask
        if request.order_type is OrderType.TRAILING and not _valid_stops(
            request.side, exit_price, request.stop_loss, request.take_profit
        ):
            return RejectReason.INVALID_STOPS
        trail = request.trailing_stop
        trailing_level = None
        if trail is not None:
            trailing_level = _trail_start(_exit_side(request.side), quote, trail.distance)
            if trailing_level < 1:
                return RejectReason.INVALID_STOPS
        try:
            margin = self._margin_usd(spec, price, request.volume)
            free = self.free_margin(request.account_id)
        except _NoRate:
            return RejectReason.NO_CONVERSION_RATE
        margin_cents = cents_ceil(margin)
        if margin_cents > free:
            return RejectReason.INSUFFICIENT_MARGIN
        self._positions[request.order_id] = Position(
            position_id=request.order_id,
            account_id=request.account_id,
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            open_price=price,
            opened_at=self._now,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            margin_usd=margin,
            trailing_stop=trail,
            trailing_level=trailing_level,
        )
        events: list[EngineEvent] = [
            PositionOpened(
                ts=self._now,
                position_id=request.order_id,
                account_id=request.account_id,
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                order_type=request.order_type,
                price=price,
                price_seq=quote.seq,
                price_source_ts=quote.source_ts,
                price_receive_ts=quote.receive_ts,
                margin_cents=margin_cents,
                config_version=self._version,
            )
        ]
        if trail is not None:
            events.append(
                TrailingStopSet(
                    self._now,
                    request.order_id,
                    request.account_id,
                    trail,
                    trailing_level,
                    quote.seq,
                )
            )
        return events

    def _trail(self, quote: Quote) -> list[EngineEvent]:
        """Move the trailing levels on `quote`'s symbol towards the market."""
        events: list[EngineEvent] = []
        for order_id, pending in list(self._orders.items()):
            request = pending.request
            if request.symbol != quote.symbol or request.trail is None:
                continue
            level = _trail_next(request.side, quote, request.trail, pending.level)
            if level is not None:
                self._orders[order_id] = replace(pending, level=level)
                events.append(
                    TrailingLevelMoved(self._now, order_id, request.account_id, level, quote.seq)
                )
        for position in list(self._positions.values()):
            trail = position.trailing_stop
            if position.symbol != quote.symbol or trail is None:
                continue
            assert position.trailing_level is not None
            level = _trail_next(_exit_side(position.side), quote, trail, position.trailing_level)
            if level is not None:
                self._positions[position.position_id] = replace(position, trailing_level=level)
                events.append(
                    TrailingLevelMoved(
                        self._now, position.position_id, position.account_id, level, quote.seq
                    )
                )
        return events

    def _trigger_orders(self, quote: Quote) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        if in_news(self._blocks, quote.symbol, self._now):
            return events  # held: no trade opens inside a news block
        for order_id, pending in list(self._orders.items()):
            request = pending.request
            if request.symbol != quote.symbol or not _triggered(pending, quote):
                continue
            del self._orders[order_id]
            opened = self._open_position(request, quote)
            if isinstance(opened, RejectReason):
                events.append(OrderCancelled(self._now, order_id, request.account_id, opened))
            else:
                events.extend(opened)
        return events

    def _trigger_stops(self, quote: Quote) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        for position in list(self._positions.values()):
            if position.symbol != quote.symbol:
                continue
            trailing = position.trailing_level
            if position.side is Side.BUY:
                price = quote.bid
                sl_hit = position.stop_loss is not None and price <= position.stop_loss
                trail_hit = trailing is not None and price <= trailing
                tp_hit = position.take_profit is not None and price >= position.take_profit
            else:
                price = quote.ask
                sl_hit = position.stop_loss is not None and price >= position.stop_loss
                trail_hit = trailing is not None and price >= trailing
                tp_hit = position.take_profit is not None and price <= position.take_profit
            if sl_hit:
                events.append(self._close(position, position.volume, quote, CloseReason.STOP_LOSS))
            elif trail_hit:
                events.append(
                    self._close(position, position.volume, quote, CloseReason.TRAILING_STOP)
                )
            elif tp_hit:
                events.append(
                    self._close(position, position.volume, quote, CloseReason.TAKE_PROFIT)
                )
        return events

    def _close(
        self, position: Position, volume: int, quote: Quote, reason: CloseReason
    ) -> PositionClosed:
        price = quote.bid if position.side is Side.BUY else quote.ask
        pnl_cents = cents_half_even(self._pnl_usd(position, price, volume))
        count = position.close_count + 1
        close_id = f"{position.position_id}/c{count}"
        account = balance_account(position.account_id)
        if pnl_cents > 0:
            self._ledger.post(
                transfer(
                    f"pnl:{close_id}",
                    self._now,
                    "trade_pnl",
                    debit=account,
                    credit=FIRM_TRADING_PNL,
                    amount=pnl_cents,
                    reference=close_id,
                )
            )
        elif pnl_cents < 0:
            self._ledger.post(
                transfer(
                    f"pnl:{close_id}",
                    self._now,
                    "trade_pnl",
                    debit=FIRM_TRADING_PNL,
                    credit=account,
                    amount=-pnl_cents,
                    reference=close_id,
                )
            )
        remaining = position.volume - volume
        if remaining == 0:
            del self._positions[position.position_id]
        else:
            self._positions[position.position_id] = replace(
                position,
                volume=remaining,
                margin_usd=position.margin_usd * remaining / position.volume,
                close_count=count,
            )
        return PositionClosed(
            ts=self._now,
            position_id=position.position_id,
            close_id=close_id,
            account_id=position.account_id,
            symbol=position.symbol,
            side=position.side,
            volume=volume,
            open_price=position.open_price,
            close_price=price,
            price_seq=quote.seq,
            price_source_ts=quote.source_ts,
            price_receive_ts=quote.receive_ts,
            pnl_cents=pnl_cents,
            remaining_volume=remaining,
            reason=reason,
            config_version=self._version,
        )

    def _exit_price(self, position: Position) -> int:
        quote = self._quotes[position.symbol]
        return quote.bid if position.side is Side.BUY else quote.ask

    def _pnl_usd(self, position: Position, exit_price: int, volume: int) -> Fraction:
        """Exact P&L in USD of closing `volume` of a position at `exit_price`."""
        spec = self._instruments[position.symbol]
        move = (exit_price - position.open_price) * position.side.sign
        quote_ccy = Fraction(move * volume * spec.contract_size, 10**spec.digits * 100)
        return self._to_usd(spec.quote_currency, quote_ccy)

    def _margin_usd(self, spec: InstrumentSpec, price: int, volume: int) -> Fraction:
        notional = Fraction(price * volume * spec.contract_size, 10**spec.digits * 100)
        return self._to_usd(spec.quote_currency, notional) / spec.leverage

    def _to_usd(self, currency: str, amount: Fraction) -> Fraction:
        if currency == "USD":
            return amount
        symbol, multiply = self._routes[currency]
        quote = self._quotes.get(symbol)
        if quote is None:
            raise _NoRate(symbol)
        rate = Fraction(quote.mid, 10 ** self._instruments[symbol].digits)
        return amount * rate if multiply else amount / rate


def _guard_event(change: GuardChange) -> SymbolFrozen | SymbolResumed:
    if change.reason is None:
        return SymbolResumed(change.ts, change.symbol)
    return SymbolFrozen(change.ts, change.symbol, change.reason)


def _exit_side(side: Side) -> Side:
    """The side of the trade that closes a position opened on `side`."""
    return Side.SELL if side is Side.BUY else Side.BUY


def _trail_start(trade_side: Side, quote: Quote, distance: int) -> int:
    """Where a trailing level for a trade on `trade_side` stands at `quote`:
    `distance` below the bid for sells, above the ask for buys."""
    return quote.bid - distance if trade_side is Side.SELL else quote.ask + distance


def _trail_next(trade_side: Side, quote: Quote, trail: Trail, level: int) -> int | None:
    """The trailing level after `quote`, or None when it does not move: it
    moves only towards the market, by at least `trail.step`."""
    candidate = _trail_start(trade_side, quote, trail.distance)
    gain = candidate - level if trade_side is Side.SELL else level - candidate
    return candidate if gain >= trail.step else None


def _entry_level(request: OrderRequest, quote: Quote) -> int:
    """A pending order's starting level: its own, or a trailing order's start."""
    if request.order_type is OrderType.TRAILING:
        assert request.trail is not None
        return _trail_start(request.side, quote, request.trail.distance)
    assert request.level is not None
    return request.level


def _triggered(pending: PendingOrder, quote: Quote) -> bool:
    request = pending.request
    level = pending.level
    if request.side is Side.BUY:
        if request.order_type is OrderType.LIMIT:
            return quote.ask <= level
        return quote.ask >= level
    if request.order_type is OrderType.LIMIT:
        return quote.bid >= level
    return quote.bid <= level


def _valid_level(side: Side, order_type: OrderType, level: int, quote: Quote) -> bool:
    """A pending order must not trigger on the price it is placed at."""
    if side is Side.BUY:
        return level < quote.ask if order_type is OrderType.LIMIT else level > quote.ask
    return level > quote.bid if order_type is OrderType.LIMIT else level < quote.bid


def _valid_stops(
    side: Side, reference: int, stop_loss: int | None, take_profit: int | None
) -> bool:
    """Stop-loss on the losing side and take-profit on the winning side of
    `reference` (the current exit price, or a pending order's level)."""
    for level in (stop_loss, take_profit):
        if level is not None and (not is_int(level) or level < 1):
            return False
    if side is Side.BUY:
        return (stop_loss is None or stop_loss < reference) and (
            take_profit is None or take_profit > reference
        )
    return (stop_loss is None or stop_loss > reference) and (
        take_profit is None or take_profit < reference
    )
