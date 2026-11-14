"""The order engine: prices, orders, positions, fills, margin and P&L.

Every input carries its own time; the engine never reads a clock, so the same
inputs always give the same events and ledger (deterministic replay). Engine
time is the latest input time seen.

Fill rules:
- [CONFIRMED] A market order fills at the bid (sell) or ask (buy) of the
  latest applied price update when the engine receives it.
- [CONFIRMED] A pending order triggers when the bid (sells) or ask (buys)
  crosses its level: buy limit ask <= level, buy stop ask >= level, sell
  limit bid >= level, sell stop bid <= level.
- [DEFAULT] A triggered order, stop-loss or take-profit fills at the bid or
  ask of the update that triggered it. When a price gaps past a level, that
  is the actual price, not the level; no other slippage model in v1.
- [DEFAULT] No partial fills. Positions can be closed fully or partly.
- Every fill records the feed update it used: sequence number, source time
  and receive time.

Guards (acceptance test 5): while a symbol is frozen it takes no new orders,
closes or stop changes; its pending orders, stop-losses and take-profits are
held, because frozen prices are never applied. Cancelling a pending order is
always allowed. On the first fresh update the symbol resumes and anything that
update crosses fills at its price.

News trade blocks (Confirmed Configuration CC-3): they bind only accounts
whose rule set is news-restricted (funded, as FTMO; the supervisor tells the
engine per account). Inside a block on an affected symbol no trade is
opened or closed on such an account: market orders, new pending
orders and manual closes are rejected, and pending orders do not trigger
(they are held until the block ends) [DEFAULT]. Stop-losses and take-profits
still execute [DEFAULT]. Changing stops is allowed.

Trailing orders (M09, D43; formulas in `propfirm.trading.orders`):
- [CONFIRMED] Per order the trader sets distance and step in whole points,
  each at least 1; buy-side orders follow the ask and sell-side orders the
  bid, the prices they fill at.
- [DEFAULT] On each applied price the level moves first, then the trigger is
  checked, so a move never triggers on its own update. A triggered entry
  fills like a stop order at that update's price (a gap past the level fills
  at the actual price); lot caps and margin are checked again at the
  trigger, and a rejection cancels the order with its reason.
- [DEFAULT] Frozen prices are never applied, so while a guard freezes the
  symbol a level neither moves nor triggers; the next valid price moves it,
  then checks the trigger.
- [DEFAULT] News blocks: placing, replacing and cancelling trailing orders
  is allowed, and levels keep moving; an entry is held (it triggers on the
  first price after the block); a trailing stop executes like a stop-loss.
- [DEFAULT] One trailing stop per position; setting another replaces it. It
  sits beside any fixed stop-loss: whichever is reached first closes the
  position (on one update both close at the same price; the fixed stop is
  checked first). When the position closes any other way, the trailing stop
  is cancelled (reason `position_closed`). An entry carries no stop-loss or
  take-profit; they are set on the position it opens.

Rules hooks (step 5): `close_all` closes every position of an account at the
current prices, ignoring freezes and news blocks (breach close-out), and
`lock_account` cancels its pending orders and rejects all further trading.

Trading hours (build plan step 1; `propfirm.trading.hours`): a symbol with
hours is closed outside them. Closed is handled like frozen: new orders,
closes and stop changes are rejected (`market_closed`), pending orders,
stop-losses and take-profits are held, a pending order cannot be cancelled
either (as MetaTrader), and a price
that arrives is not applied (`UpdateOutsideHours`). A breach close-out
still closes at the last price.

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
- Commission (only symbols priced with `SpreadAndCommission`): a share of
  the notional at the fill price, charged once when a position opens and
  posted to the ledger at once, so it counts in balance, equity, the loss
  limits and the day's P&L. Rounded half-even to the cent. The margin
  check requires margin plus commission.
- Swaps (K6, D-13.19; formulas in `propfirm.trading.swaps`): at each
  17:00 New York reset the rules supervisor calls `rollover`; every open
  position on a symbol with swap terms pays value x annual rate x nights /
  360, the value at the symbol's feed price (midpoint) in US dollars,
  rounded half-even to the cent, posted to the ledger at once like a
  commission. Never credited. A position whose value cannot be converted to
  US dollars at that moment (no conversion price yet) is not charged.
- Not implemented: margin call and stop-out (not in the project record; the
  loss limits of the rules engine, step 5, act far earlier).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction

from propfirm._checks import is_int, require_int, require_text
from propfirm.journal.codec import added_field
from propfirm.ledger import Ledger, transfer
from propfirm.market.guards import FeedGuards, GuardChange, GuardConfig, GuardState
from propfirm.market.sequence import SeqStatus, SequenceTracker
from propfirm.market.spread import NewsWindow, Quote, SpreadModel, SpreadState, in_news
from propfirm.market.symbols import SymbolSpec
from propfirm.market.types import PriceUpdate
from propfirm.money import cents_ceil, cents_half_even
from propfirm.trading.book import OrderBook, PositionBook
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    CommissionCharged,
    DuplicateUpdate,
    EngineEvent,
    FeedGap,
    NewsCancelled,
    NewsScheduled,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    PriceHeld,
    PriceReleased,
    RejectReason,
    StaleUpdate,
    StopsModified,
    SwapCharged,
    SymbolFrozen,
    SymbolResumed,
    TradingConfigChanged,
    TrailingMoved,
    TrailingPlaced,
    UpdateOutsideHours,
)
from propfirm.trading.instruments import InstrumentSpec, conversion_route, scaled_cap
from propfirm.trading.orders import (
    OrderRequest,
    OrderType,
    PendingOrder,
    Position,
    Side,
    TrailingKind,
    TrailingOrder,
    trailing_level,
    trailing_move,
    trailing_reached,
)
from propfirm.trading.swaps import YEAR_DAYS

FIRM_CAPITAL = "firm:capital"
FIRM_TRADING_PNL = "firm:trading_pnl"
FIRM_COMMISSION = "firm:commission"
FIRM_SWAP = "firm:swap"
_BREAK_RECHECK_MS = 24 * 3_600_000  # no long break ahead: look again a day later
FIRM_CORRECTIONS = "firm:corrections"  # bad-price corrections (D-14.4)
FIRM_SCALING = "firm:scaling"  # allocation added by scaling steps (D-14.8)
FIRM_PAYOUTS = "firm:payouts"  # paid out of trading accounts, owed to traders until sent


def scaling_entry(scaling: str) -> str:
    return f"scaling:{scaling}"


def correction_entry(correction: str, account_id: str) -> str:
    return f"correction:{correction}:{account_id}"


def payout_entry(payout: str) -> str:
    """The ledger entry id of a payout's debit (one per payout, ever)."""
    return f"payout:{payout}"


def balance_account(account_id: str) -> str:
    """Ledger account holding a trading account's balance (debit-positive)."""
    return f"acc:{account_id}:balance"


@dataclass(frozen=True, slots=True)
class _Account:
    account_id: str
    initial_balance: int


@dataclass(frozen=True, slots=True)
class EngineState:
    """Everything the engine remembers between inputs (its configuration
    comes from the setup). Ordered collections keep the engine's own order,
    which decides the order of events."""

    now: int
    accounts: tuple[tuple[str, int], ...]  # (account id, initial balance), opening order
    locked: tuple[str, ...]  # sorted
    used_ids: tuple[str, ...]  # sorted
    quotes: tuple[Quote, ...]
    orders: tuple[PendingOrder, ...]  # placement order; trailing orders included
    positions: tuple[Position, ...]  # opening order
    guards: GuardState
    sequences: tuple[tuple[str, int], ...]
    spreads: tuple[tuple[str, SpreadState], ...]  # configured order
    calendar: tuple[tuple[str, NewsWindow], ...]  # scheduled, not yet ended
    calendar_ids: tuple[str, ...]  # sorted: every event id ever scheduled
    config_version: str  # the trading settings in force (they can change by input)
    instruments: tuple[InstrumentSpec, ...]  # configured order
    guard: GuardConfig
    # updates held by the sanity filter, one per symbol at most (configured
    # order); added after records existed: none before
    held: tuple[PriceUpdate, ...] = added_field(())
    # (symbol, flat-by time) of the last market break each symbol's bound
    # accounts were made flat for (configured order); added after records
    # existed: none before
    flattened: tuple[tuple[str, int], ...] = added_field(())


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
        self._spreads = {s: SpreadModel(spec.spread_model) for s, spec in self._instruments.items()}
        self._news = tuple(news_windows)
        self._blocks = tuple(trade_blocks)
        self._routes: dict[str, tuple[str, bool]] = {}
        for spec in self._instruments.values():
            currency = spec.quote_currency
            if currency != "USD" and currency not in self._routes:
                self._routes[currency] = conversion_route(currency, self._instruments)
        # symbol -> the symbols whose profit converts to US dollars through it
        self._converted_by: dict[str, tuple[str, ...]] = {}
        for spec in self._instruments.values():
            route = self._routes.get(spec.quote_currency)
            if route is not None:
                via = route[0]
                self._converted_by[via] = (*self._converted_by.get(via, ()), spec.name)
        self._quotes: dict[str, Quote] = {}
        self._accounts: dict[str, _Account] = {}
        self._orders = OrderBook()  # pending, in placement order (indexed)
        self._positions = PositionBook()  # open, in opening order (indexed)
        self._used_ids: set[str] = set()
        self._locked: set[str] = set()
        self._calendar: dict[str, NewsWindow] = {}  # event id -> window, both uses
        self._calendar_ids: set[str] = set()
        self._held: dict[str, PriceUpdate] = {}  # sanity filter: one update per symbol
        # accounts whose rule set is not news-restricted (evaluations): set by
        # the rules supervisor from each account's rule set, so not engine state
        self._news_exempt: set[str] = set()
        self._news_strict: set[str] = set()  # entries cancelled when a window opens
        # accounts the market break rule binds (funded, not Swing): set by the
        # supervisor from each account's rule set, so not engine state
        self._break_bound: set[str] = set()
        # symbol -> ((flat-by, close, open) of its next long break or None,
        # when to look again); derived from the hours and engine time, so
        # not engine state either
        self._breaks: dict[str, tuple[tuple[int, int, int] | None, int]] = {}
        self._flattened: dict[str, int] = {}  # symbol -> the flat-by time already handled
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
        if self._closed(symbol):
            # a stray price outside trading hours is never applied, so it
            # cannot fire a stop; its sequence number is still used
            events.append(UpdateOutsideHours(self._now, symbol, update.seq))
            return events
        apply, changes = self._guards.on_update(update)
        events.extend(_guard_event(c) for c in changes)
        if not apply:
            lag = update.receive_ts - update.source_ts
            events.append(StaleUpdate(self._now, symbol, update.seq, lag))
            return events
        model = self._spreads[symbol]
        held = self._sanity(update, model, events)
        if held:
            return events
        model.observe(update.price)
        quote = model.quote(
            symbol,
            update.price,
            update.seq,
            update.source_ts,
            update.receive_ts,
            self._spread_news(symbol),
            self._now,
            update.bid,
            update.ask,
        )
        self._quotes[symbol] = quote
        events.extend(self._trigger_orders(quote))
        events.extend(self._trigger_stops(quote))
        return events

    def _sanity(self, update: PriceUpdate, model: SpreadModel, events: list[EngineEvent]) -> bool:
        """The sanity filter (review D9); True: `update` is held, not applied.
        Appends what it decided to `events`."""
        guard = self._guards.config
        symbol, last, changes = update.symbol, model.last, model.changes()
        held = self._held.pop(symbol, None)
        if held is not None:
            # the held update is decided by this one: a normal move from it
            # confirms it; anything else drops it as an error
            confirmed = not guard.out_of_line(update.price, held.price, changes)
            events.append(PriceReleased(self._now, symbol, held.seq, confirmed))
            if confirmed:
                return False
        if last is None or not guard.out_of_line(update.price, last, changes):
            return False
        self._held[symbol] = update
        events.append(PriceHeld(self._now, symbol, update.seq, update.price, last))
        return True

    def advance(self, ts: int) -> list[EngineEvent]:
        """Move engine time forward; freezes symbols whose updates are overdue."""
        return self._advance(ts)

    def rollover(self, reset: int, weekday: int) -> list[EngineEvent]:
        """Charge the swaps of a 17:00 New York reset (ms UTC) falling on
        `weekday` (New York, Monday = 0), in position opening order. The
        supervisor calls it once per reset, after advancing to it."""
        require_int("weekday", weekday, minimum=0)
        if weekday > 6:
            raise ValueError("weekday must be 0 (Monday) to 6")
        events = self._advance(reset)
        for position in list(self._positions.values()):
            spec = self._instruments[position.symbol]
            terms = spec.swap
            if terms is None:
                continue
            nights = terms.nights[weekday]
            rate = terms.rate(position.side is Side.BUY)
            quote = self._quotes.get(position.symbol)
            if nights == 0 or rate == 0 or quote is None:
                continue
            try:
                value = self._notional_usd(spec, quote.mid, position.volume)
            except _NoRate:
                continue
            swap_cents = cents_half_even(value * rate * nights / YEAR_DAYS)
            if swap_cents == 0:
                continue
            self._ledger.post(
                transfer(
                    f"swap:{position.position_id}:{self._now}",
                    self._now,
                    "swap",
                    debit=FIRM_SWAP,
                    credit=balance_account(position.account_id),
                    amount=swap_cents,
                    reference=position.position_id,
                )
            )
            events.append(
                SwapCharged(
                    ts=self._now,
                    position_id=position.position_id,
                    account_id=position.account_id,
                    symbol=position.symbol,
                    nights=nights,
                    value_cents=cents_half_even(value),
                    swap_cents=swap_cents,
                    config_version=self._version,
                )
            )
        return events

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
        assert request.level is not None
        self._orders[request.order_id] = PendingOrder(request, self._now)
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
                request.level,
            )
        )
        return events

    def cancel_order(self, account_id: str, order_id: str, ts: int) -> list[EngineEvent]:
        events = self._advance(ts)
        pending = self._orders.get(order_id)
        if pending is None or pending.request.account_id != account_id:
            events.append(self._reject(order_id, account_id, RejectReason.UNKNOWN_ORDER))
            return events
        if self._closed(pending.request.symbol):
            # as MetaTrader at FTMO and FundedNext: a closed market takes no
            # request at all, a deletion included [decided 13 Nov 2026]
            events.append(self._reject(order_id, account_id, RejectReason.MARKET_CLOSED))
            return events
        del self._orders[order_id]
        events.append(OrderCancelled(self._now, order_id, account_id, None))
        return events

    def place_trailing_entry(
        self,
        order_id: str,
        account_id: str,
        symbol: str,
        side: Side,
        volume: int,
        distance: int,
        step: int,
        ts: int,
    ) -> list[EngineEvent]:
        """A trailing buy (follows the ask) or trailing sell (follows the bid)."""
        events = self._advance(ts)
        reason = self._check_trailing_entry(order_id, account_id, symbol, volume)
        if reason is None:
            reason = _trailing_params(distance, step)
        if reason is not None:
            events.append(self._reject(order_id, account_id, reason))
            return events
        quote = self._quotes[symbol]
        follows_ask = side is Side.BUY
        level = trailing_level(follows_ask, quote.ask if follows_ask else quote.bid, distance)
        if level < 1:
            events.append(self._reject(order_id, account_id, RejectReason.INVALID_TRAILING))
            return events
        order = TrailingOrder(
            order_id=order_id,
            account_id=account_id,
            symbol=symbol,
            side=side,
            kind=TrailingKind.ENTRY,
            volume=volume,
            position_id=None,
            distance=distance,
            step=step,
            level=level,
            moved_at=self._now,
            moved_seq=quote.seq,
        )
        self._orders[order_id] = PendingOrder(order, self._now)
        self._used_ids.add(order_id)
        events.append(self._trailing_placed(order))
        return events

    def set_trailing_stop(
        self,
        order_id: str,
        account_id: str,
        position_id: str,
        distance: int,
        step: int,
        ts: int,
    ) -> list[EngineEvent]:
        """Put a trailing stop on a position, replacing any it has."""
        events = self._advance(ts)
        position = self._positions.get(position_id)
        if position is None or position.account_id != account_id:
            events.append(self._reject(order_id, account_id, RejectReason.UNKNOWN_POSITION))
            return events
        if account_id in self._locked:
            events.append(self._reject(order_id, account_id, RejectReason.ACCOUNT_LOCKED))
            return events
        if order_id in self._used_ids or order_id in self._positions:
            events.append(self._reject(order_id, account_id, RejectReason.DUPLICATE_ID))
            return events
        if self._closed(position.symbol):
            events.append(self._reject(order_id, account_id, RejectReason.MARKET_CLOSED))
            return events
        if self._guards.is_frozen(position.symbol):
            events.append(self._reject(order_id, account_id, RejectReason.SYMBOL_FROZEN))
            return events
        quote = self._quotes[position.symbol]
        follows_ask = position.side is Side.SELL
        if _trailing_params(distance, step) is not None or (
            trailing_level(follows_ask, quote.ask if follows_ask else quote.bid, distance) < 1
        ):
            events.append(self._reject(order_id, account_id, RejectReason.INVALID_TRAILING))
            return events
        level = trailing_level(follows_ask, quote.ask if follows_ask else quote.bid, distance)
        replaced = self._trailing_stop_of(position_id)
        if replaced is not None:
            del self._orders[replaced.order_id]
            events.append(OrderCancelled(self._now, replaced.order_id, account_id, None))
        order = TrailingOrder(
            order_id=order_id,
            account_id=account_id,
            symbol=position.symbol,
            side=position.side,
            kind=TrailingKind.STOP,
            volume=None,
            position_id=position_id,
            distance=distance,
            step=step,
            level=level,
            moved_at=self._now,
            moved_seq=quote.seq,
        )
        self._orders[order_id] = PendingOrder(order, self._now)
        self._used_ids.add(order_id)
        events.append(self._trailing_placed(order))
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
        if self._closed(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.MARKET_CLOSED))
            return events
        if self._guards.is_frozen(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.SYMBOL_FROZEN))
            return events
        if self.is_blocked_for(account_id, position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.NEWS_WINDOW))
            return events
        amount = position.volume if volume is None else volume
        if not is_int(amount) or not 1 <= amount <= position.volume:
            events.append(self._reject(position_id, account_id, RejectReason.INVALID_VOLUME))
            return events
        quote = self._quotes[position.symbol]
        if self._stale(quote):
            events.append(self._reject(position_id, account_id, RejectReason.STALE_QUOTE))
            return events
        events.extend(self._close(position, amount, quote, CloseReason.CLIENT))
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
        if self._closed(position.symbol):
            events.append(self._reject(position_id, account_id, RejectReason.MARKET_CLOSED))
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

    def close_all(self, account_id: str, ts: int, reason: CloseReason) -> list[EngineEvent]:
        """Close every position of an account at the current prices. Ignores
        freezes and news blocks: a frozen symbol closes at its last applied
        price (traceable through `price_seq`)."""
        self._account(account_id)
        events = self._advance(ts)
        for position in self._open(account_id):
            events.extend(
                self._close(position, position.volume, self._quotes[position.symbol], reason)
            )
        return events

    def schedule_news(self, event_id: str, window: NewsWindow, ts: int) -> list[EngineEvent]:
        """Add a restricted event: inside its window the affected symbols take
        no trades and their spreads widen [DEFAULT: one window for both]."""
        require_text("event_id", event_id)
        if event_id in self._calendar_ids:
            raise ValueError(f"news event {event_id!r} already scheduled")
        events = self._advance(ts)
        self._calendar[event_id] = window
        self._calendar_ids.add(event_id)
        symbols = tuple(sorted(window.symbols))
        events.append(NewsScheduled(self._now, event_id, symbols, window.start_ts, window.end_ts))
        return events

    def cancel_news(self, event_id: str, ts: int) -> list[EngineEvent]:
        """Remove a scheduled event whose window has not started."""
        events = self._advance(ts)
        window = self._calendar.get(event_id)
        if window is None or window.start_ts <= self._now:
            raise ValueError(f"news event {event_id!r} is unknown, started or over")
        del self._calendar[event_id]
        events.append(NewsCancelled(self._now, event_id))
        return events

    def check_reconfigure(
        self,
        config_version: str,
        instruments: Iterable[InstrumentSpec],
        *,
        allow_same: bool = False,
    ) -> str | None:
        """Why these settings cannot be applied, or None. Only leverage, lot
        caps and spread parameters may change: symbol, digits, contract size
        and quote currency define the instrument and P&L of open positions."""
        if not isinstance(config_version, str) or not config_version.strip():
            return "config version must be non-empty text"
        if config_version != config_version.strip():
            return "config version must be unpadded"
        if config_version == self._version and not allow_same:
            return f"config version {config_version!r} is already in force"
        seen: set[str] = set()
        for spec in instruments:
            if not isinstance(spec, InstrumentSpec):
                return "instruments must be InstrumentSpec values"
            current = self._instruments.get(spec.name)
            if current is None:
                return f"{spec.name} is not a configured symbol"
            if spec.name in seen:
                return f"{spec.name} is listed twice"
            seen.add(spec.name)
            if (spec.symbol, spec.contract_size, spec.quote_currency) != (
                current.symbol,
                current.contract_size,
                current.quote_currency,
            ):
                return f"{spec.name}: symbol, digits, contract size and quote currency are fixed"
        return None

    def reconfigure(
        self,
        config_version: str,
        instruments: Iterable[InstrumentSpec],
        guard: GuardConfig | None,
        ts: int,
    ) -> list[EngineEvent]:
        """Apply new trading settings from now on (checked by `check_reconfigure`)."""
        specs = tuple(instruments)
        refusal = self.check_reconfigure(config_version, specs)
        if refusal is not None:
            raise ValueError(refusal)
        if guard is not None and not isinstance(guard, GuardConfig):
            raise TypeError("guard must be a GuardConfig or None")
        events = self._advance(ts)
        merged = {**self._instruments, **{s.name: s for s in specs}}
        self._apply_config(config_version, tuple(merged.values()), guard or self._guards.config)
        events.append(
            TradingConfigChanged(
                self._now, config_version, tuple(sorted(s.name for s in specs)), guard is not None
            )
        )
        return events

    def _apply_config(
        self, config_version: str, instruments: Iterable[InstrumentSpec], guard: GuardConfig
    ) -> None:
        self._version = config_version
        for spec in instruments:
            if spec != self._instruments[spec.name]:
                self._instruments[spec.name] = spec
            self._spreads[spec.name].reconfigure(spec.spread_model)
        self._guards.reconfigure(guard)
        self._breaks.clear()  # the hours may have changed: look again

    def debit_payout(self, account_id: str, amount_cents: int, payout: str, ts: int) -> None:
        """Take a payout's amount out of the account's balance (ledger entry
        `payout:<payout>`). The rules supervisor checks it first."""
        self._account(account_id)
        self._advance(ts)
        self._ledger.post(
            transfer(
                payout_entry(payout),
                self._now,
                "payout",
                debit=FIRM_PAYOUTS,
                credit=balance_account(account_id),
                amount=amount_cents,
                reference=payout,
            )
        )

    def correct_balance(self, account_id: str, amount_cents: int, correction: str, ts: int) -> None:
        """Add a signed bad-price correction to the balance (ledger entry
        `correction:<correction>:<account>`)."""
        self._account(account_id)
        self._advance(ts)
        # a posting of +x raises the balance (see `debit_payout`, which credits it)
        debit, credit = (
            (balance_account(account_id), FIRM_CORRECTIONS)
            if amount_cents > 0
            else (FIRM_CORRECTIONS, balance_account(account_id))
        )
        self._ledger.post(
            transfer(
                correction_entry(correction, account_id),
                self._now,
                "correction",
                debit=debit,
                credit=credit,
                amount=abs(amount_cents),
                reference=correction,
            )
        )

    def add_allocation(self, account_id: str, amount_cents: int, scaling: str, ts: int) -> None:
        """Add a scaling step's allocation to the balance (ledger entry
        `scaling:<scaling>`)."""
        self._account(account_id)
        self._advance(ts)
        self._ledger.post(
            transfer(
                scaling_entry(scaling),
                self._now,
                "scaling",
                debit=balance_account(account_id),
                credit=FIRM_SCALING,
                amount=amount_cents,
                reference=scaling,
            )
        )

    def unlock_account(self, account_id: str, ts: int) -> list[EngineEvent]:
        """Allow trading again (a reinstated account)."""
        self._account(account_id)
        events = self._advance(ts)
        self._locked.discard(account_id)
        return events

    def has_entry(self, entry_id: str) -> bool:
        return self._ledger.has(entry_id)

    def lock_account(self, account_id: str, ts: int) -> list[EngineEvent]:
        """Cancel the account's pending orders and reject all further trading."""
        self._account(account_id)
        events = self._advance(ts)
        for pending in self._orders.of_account(account_id):  # placement order
            order_id = pending.request.order_id
            if order_id in self._orders:
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
    def config_version(self) -> str:
        return self._version

    @property
    def instruments(self) -> dict[str, InstrumentSpec]:
        """The trading settings in force, per symbol."""
        return dict(self._instruments)

    @property
    def guard_config(self) -> GuardConfig:
        return self._guards.config

    @property
    def symbols(self) -> frozenset[str]:
        """The configured symbols."""
        return frozenset(self._instruments)

    @property
    def symbol_specs(self) -> dict[str, SymbolSpec]:
        return {name: spec.symbol for name, spec in self._instruments.items()}

    def news_event(self, event_id: str) -> NewsWindow | None:
        """A scheduled event's window, until the window ends."""
        return self._calendar.get(event_id)

    def knows_news(self, event_id: str) -> bool:
        """Whether this event id was ever scheduled."""
        return event_id in self._calendar_ids

    def state(self) -> EngineState:
        return EngineState(
            now=self._now,
            accounts=tuple((a.account_id, a.initial_balance) for a in self._accounts.values()),
            locked=tuple(sorted(self._locked)),
            used_ids=tuple(sorted(self._used_ids)),
            quotes=tuple(self._quotes.values()),
            orders=tuple(self._orders.values()),
            positions=tuple(self._positions.values()),
            guards=self._guards.state(),
            sequences=tuple(self._sequences.snapshot().items()),
            spreads=tuple((s, m.state()) for s, m in self._spreads.items()),
            calendar=tuple(self._calendar.items()),
            calendar_ids=tuple(sorted(self._calendar_ids)),
            config_version=self._version,
            instruments=tuple(self._instruments.values()),
            guard=self._guards.config,
            held=tuple(u for s in self._instruments if (u := self._held.get(s)) is not None),
            flattened=tuple(
                (s, at) for s in self._instruments if (at := self._flattened.get(s)) is not None
            ),
        )

    def restore(self, state: EngineState) -> None:
        """Load a state taken by `state()` from an engine with the same
        configuration. Only a fresh engine may be restored."""
        if self._now or self._accounts or self._quotes:
            raise RuntimeError("restore needs a fresh engine")
        known = set(self._instruments)
        if [s for s, _ in state.spreads] != list(self._spreads):
            raise ValueError("spread state is for other symbols")
        if not {q.symbol for q in state.quotes} <= known:
            raise ValueError("quote for an unconfigured symbol")
        accounts = {a: _Account(a, balance) for a, balance in state.accounts}
        if len(accounts) != len(state.accounts):
            raise ValueError("duplicate account in state")
        if [i.name for i in state.instruments] != list(self._instruments):
            raise ValueError("instrument state is for other symbols")
        refusal = self.check_reconfigure(state.config_version, state.instruments, allow_same=True)
        if refusal is not None:
            raise ValueError(refusal)
        self._apply_config(state.config_version, state.instruments, state.guard)
        self._guards.restore(state.guards)
        for symbol, spread in state.spreads:
            self._spreads[symbol].restore(spread)
        self._sequences = SequenceTracker(dict(state.sequences))
        self._now = require_int("now", state.now, minimum=0)
        self._accounts = accounts
        self._locked = set(state.locked)
        self._used_ids = set(state.used_ids)
        self._quotes = {q.symbol: q for q in state.quotes}
        self._orders = OrderBook(state.orders)
        self._positions = PositionBook(state.positions)
        self._calendar = dict(state.calendar)
        self._calendar_ids = set(state.calendar_ids)
        held = {u.symbol: u for u in state.held}
        if len(held) != len(state.held) or not set(held) <= known:
            raise ValueError("bad held updates")
        self._held = held
        flattened = dict(state.flattened)
        if len(flattened) != len(state.flattened) or not set(flattened) <= known:
            raise ValueError("bad market break state")
        self._flattened = flattened
        if not set(self._calendar) <= self._calendar_ids:
            raise ValueError("scheduled event without an id record")

    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    def is_frozen(self, symbol: str) -> bool:
        return self._guards.is_frozen(symbol)

    def is_open(self, symbol: str) -> bool:
        """Whether the symbol's trading hours are open at engine time."""
        return not self._closed(symbol)

    def _closed(self, symbol: str) -> bool:
        hours = self._instruments[symbol].hours
        return hours is not None and not hours.is_open(self._now)

    def set_break_bound(self, account_id: str, bound: bool) -> None:
        """Whether the market break rule binds this account (from its rule set)."""
        self._account(account_id)
        if bound:
            self._break_bound.add(account_id)
        else:
            self._break_bound.discard(account_id)

    def next_break(self, symbol: str) -> tuple[int, int, int] | None:
        """(flat-by, close, open) of the symbol's next long break, or None."""
        return self._next_break(symbol)

    def set_news_restricted(self, account_id: str, restricted: bool) -> None:
        """Whether news trade blocks bind this account (from its rule set)."""
        self._account(account_id)
        if restricted:
            self._news_exempt.discard(account_id)
        else:
            self._news_exempt.add(account_id)

    def set_news_strict(self, account_id: str, strict: bool) -> None:
        """Whether this account's pending entries on a symbol are cancelled
        when a news window opens on it (RuleSet.news_breaches)."""
        self._account(account_id)
        if strict:
            self._news_strict.add(account_id)
        else:
            self._news_strict.discard(account_id)

    def is_blocked_for(self, account_id: str, symbol: str) -> bool:
        """Whether a news trade block binds this account on `symbol` now."""
        return account_id not in self._news_exempt and self.is_blocked(symbol)

    def news_events_at(self, symbol: str) -> tuple[str, ...]:
        """Ids of the scheduled events whose block covers `symbol` now,
        sorted ("setup" for a block of the engine's own configuration)."""
        ids = sorted(e for e, w in self._calendar.items() if in_news((w,), symbol, self._now))
        if in_news(self._blocks, symbol, self._now):
            ids.append("setup")
        return tuple(ids)

    def is_blocked(self, symbol: str) -> bool:
        """Whether a news trade block covers `symbol` at engine time."""
        return in_news(self._blocks, symbol, self._now) or in_news(
            self._calendar.values(), symbol, self._now
        )

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

    def floating(self, account_id: str) -> tuple[tuple[str, int, int], ...]:
        """(position id, the price it would close at now, its P&L in cents)
        per open position, for display: each rounded on its own, so they may
        differ from `unrealized_pnl` (rounded once) by under a cent each."""
        self._account(account_id)
        out = []
        for p in self._open(account_id):
            price = self._exit_price(p)
            out.append((p.position_id, price, cents_half_even(self._pnl_usd(p, price, p.volume))))
        return tuple(out)

    def calendar(self) -> tuple[tuple[str, NewsWindow], ...]:
        """Restricted events scheduled (event id, window), soonest first."""
        return tuple(sorted(self._calendar.items(), key=lambda item: (item[1].start_ts, item[0])))

    def used_margin(self, account_id: str) -> int:
        self._account(account_id)
        return cents_ceil(sum((p.margin_usd for p in self._open(account_id)), Fraction(0)))

    def free_margin(self, account_id: str) -> int:
        return self.equity(account_id) - self.used_margin(account_id)

    def accounts_priced_by(self, symbol: str) -> set[str]:
        """Accounts whose equity a new price of `symbol` can change: those
        holding it, and those holding a symbol whose profit converts to US
        dollars through it (EUR/JPY through USD/JPY)."""
        out: set[str] = set()
        for held in (symbol, *self._converted_by.get(symbol, ())):
            out.update(self._positions.accounts_on(held))
        return out

    def positions(self, account_id: str) -> tuple[Position, ...]:
        self._account(account_id)
        return tuple(self._open(account_id))

    def pending_orders(self, account_id: str) -> tuple[PendingOrder, ...]:
        """Limit and stop orders waiting for their level (trailing orders:
        `trailing_orders`)."""
        self._account(account_id)
        return tuple(
            o for o in self._orders.of_account(account_id) if isinstance(o.request, OrderRequest)
        )

    def trailing_orders(self, account_id: str) -> tuple[tuple[TrailingOrder, int], ...]:
        """Working trailing orders (order, placed at), in placement order."""
        self._account(account_id)
        return tuple(
            (o.request, o.placed_at)
            for o in self._orders.of_account(account_id)
            if isinstance(o.request, TrailingOrder)
        )

    def has_working_orders(self, account_id: str) -> bool:
        """Whether any order of the account (pending or trailing) is working."""
        self._account(account_id)
        return bool(self._orders.of_account(account_id))

    # --- internals ----------------------------------------------------------

    def _advance(self, ts: int) -> list[EngineEvent]:
        require_int("ts", ts, minimum=0)
        target = max(self._now, ts)
        events = self._flatten_before_breaks(target)
        self._now = target
        events.extend(self._cancel_news_entries())
        for event_id, window in list(self._calendar.items()):
            if window.end_ts <= self._now:  # time only moves forward: never covers again
                del self._calendar[event_id]
        events.extend(_guard_event(c) for c in self._guards.advance(self._now))
        return events

    def _cancel_news_entries(self) -> list[EngineEvent]:
        """Cancel the pending and trailing entries of strict accounts on every
        symbol a news window covers now (RejectReason.NEWS_WINDOW), in
        placement order. Stops of open positions stay."""
        if not self._news_strict:
            return []
        events: list[EngineEvent] = []
        for symbol in self._instruments:
            if not self.is_blocked(symbol):
                continue
            for order_id, pending in list(self._orders.on_symbol(symbol)):
                request = pending.request
                if request.account_id not in self._news_strict or (
                    isinstance(request, TrailingOrder) and request.kind is not TrailingKind.ENTRY
                ):
                    continue
                del self._orders[order_id]
                events.append(
                    OrderCancelled(
                        self._now, order_id, request.account_id, RejectReason.NEWS_WINDOW
                    )
                )
        return events

    def _break_after(self, symbol: str, ts: int) -> tuple[tuple[int, int, int] | None, int]:
        """The symbol's first long break that closes after `ts`, as
        (flat-by, close, open), with the time to look again when there is
        none in the schedule's horizon."""
        hours = self._instruments[symbol].hours
        assert hours is not None
        found = hours.next_long_break(ts)
        if found is None:
            return None, ts + _BREAK_RECHECK_MS
        close, reopen = found
        return (close - hours.flat_before_minutes * 60_000, close, reopen), reopen

    def _next_break(self, symbol: str) -> tuple[int, int, int] | None:
        """(flat-by, close, open) of the symbol's next long break not yet
        handled; None when the rule is off on it or none is due soon."""
        hours = self._instruments[symbol].hours
        if hours is None or hours.flat_before_minutes == 0:
            return None
        cached = self._breaks.get(symbol)
        if cached is None or cached[1] <= self._now:
            cached = self._break_after(symbol, self._now)
            self._breaks[symbol] = cached
        return cached[0]

    def _flatten_before_breaks(self, target: int) -> list[EngineEvent]:
        """Make every account the break rule binds flat on each symbol whose
        flat-by time is at or before `target`, at that time and at that
        moment's prices, earliest first (CloseReason.MARKET_BREAK)."""
        if not self._break_bound:
            return []
        events: list[EngineEvent] = []
        while True:
            due = [
                (upcoming[0], symbol)
                for symbol in sorted(self._instruments)
                if (upcoming := self._due_break(symbol, target)) is not None
            ]
            if not due:
                return events
            flat_by, symbol = min(due)
            self._now = max(self._now, flat_by)
            for account_id in sorted(self._break_bound):
                events.extend(self._flatten(account_id, symbol))
            self._flattened[symbol] = flat_by

    def _due_break(self, symbol: str, target: int) -> tuple[int, int, int] | None:
        """The symbol's break whose flat-by time is at or before `target` and
        not yet handled. Moves the cache over breaks already handled, and
        over empty horizons, that end by `target`, so one long advance
        misses no break."""
        upcoming = self._next_break(symbol)
        while True:
            cached = self._breaks.get(symbol)
            if cached is None:  # the rule is off on this symbol
                return None
            if upcoming is None:
                if cached[1] > target:
                    return None
                after = cached[1]  # no break in the last horizon: look on
            elif self._flattened.get(symbol) == upcoming[0]:
                if upcoming[2] > target:
                    return None
                after = upcoming[2]  # handled, and this advance passes its reopening
            else:
                return upcoming if upcoming[0] <= target else None
            self._breaks[symbol] = self._break_after(symbol, after)
            upcoming = self._breaks[symbol][0]

    def _flatten(self, account_id: str, symbol: str) -> list[EngineEvent]:
        """Close the account's positions and cancel its pending orders on
        one symbol, at the current prices (ignores freezes and hours)."""
        events: list[EngineEvent] = []
        for pending in self._orders.of_account(account_id):
            order_id = pending.request.order_id
            if pending.request.symbol == symbol and order_id in self._orders:
                del self._orders[order_id]
                events.append(
                    OrderCancelled(self._now, order_id, account_id, RejectReason.MARKET_BREAK)
                )
        quote = self._quotes.get(symbol)
        if quote is None:
            return events
        for position in self._open(account_id):
            if position.symbol == symbol:
                events.extend(
                    self._close(position, position.volume, quote, CloseReason.MARKET_BREAK)
                )
        return events

    def _in_break_window(self, account_id: str, symbol: str) -> bool:
        """Whether a bound account is between its flat-by time and the close."""
        if account_id not in self._break_bound:
            return False
        upcoming = self._next_break(symbol)
        return upcoming is not None and upcoming[0] <= self._now < upcoming[1]

    def _spread_news(self, symbol: str) -> bool:
        return in_news(self._news, symbol, self._now) or in_news(
            self._calendar.values(), symbol, self._now
        )

    def _account(self, account_id: str) -> _Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise KeyError(f"unknown account {account_id!r}") from None

    def _open(self, account_id: str) -> list[Position]:
        return self._positions.of_account(account_id)

    def _reject(self, ref: str, account_id: str, reason: RejectReason) -> OrderRejected:
        return OrderRejected(self._now, ref, account_id, reason, self._version)

    def _stale(self, quote: Quote) -> bool:
        """Whether `quote` is older than its symbol's quote age limit at
        engine time (review D2); never with no limit set."""
        limit = self._guards.config.quote_age_limit(quote.symbol)
        return limit > 0 and self._now - quote.receive_ts > limit

    def _check_new_order(self, request: OrderRequest) -> RejectReason | None:
        account = self._accounts.get(request.account_id)
        if account is None:
            return RejectReason.UNKNOWN_ACCOUNT
        if request.account_id in self._locked:
            return RejectReason.ACCOUNT_LOCKED
        if request.order_id in self._used_ids or request.order_id in self._positions:
            return RejectReason.DUPLICATE_ID
        if request.order_type is OrderType.TRAILING:
            return RejectReason.INVALID_TRAILING  # trailing entries have their own input
        spec = self._instruments.get(request.symbol)
        if spec is None:
            return RejectReason.UNKNOWN_SYMBOL
        if self._closed(request.symbol):
            return RejectReason.MARKET_CLOSED
        if self._in_break_window(request.account_id, request.symbol):
            return RejectReason.MARKET_BREAK
        if self._guards.is_frozen(request.symbol):
            return RejectReason.SYMBOL_FROZEN
        if self.is_blocked_for(request.account_id, request.symbol):
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
            if self._stale(quote):
                return RejectReason.STALE_QUOTE
            exit_price = quote.bid if request.side is Side.BUY else quote.ask
            if not _valid_stops(request.side, exit_price, request.stop_loss, request.take_profit):
                return RejectReason.INVALID_STOPS
            return None
        assert request.level is not None
        if not _valid_level(request.side, request.order_type, request.level, quote):
            return RejectReason.INVALID_LEVEL
        if not _valid_stops(request.side, request.level, request.stop_loss, request.take_profit):
            return RejectReason.INVALID_STOPS
        return None

    def _check_trailing_entry(
        self, order_id: str, account_id: str, symbol: str, volume: int
    ) -> RejectReason | None:
        """The checks of a new pending order, except news blocks (a trailing
        entry may be placed inside one and is held) and the level."""
        account = self._accounts.get(account_id)
        if account is None:
            return RejectReason.UNKNOWN_ACCOUNT
        if account_id in self._locked:
            return RejectReason.ACCOUNT_LOCKED
        if order_id in self._used_ids or order_id in self._positions:
            return RejectReason.DUPLICATE_ID
        if symbol not in self._instruments:
            return RejectReason.UNKNOWN_SYMBOL
        if self._closed(symbol):
            return RejectReason.MARKET_CLOSED
        if self._in_break_window(account_id, symbol):
            return RejectReason.MARKET_BREAK
        if self._guards.is_frozen(symbol):
            return RejectReason.SYMBOL_FROZEN
        if not is_int(volume) or volume < 1:
            return RejectReason.INVALID_VOLUME
        return self._cap_reason(account, symbol, volume)

    def _cap_reason(self, account: _Account, symbol: str, volume: int) -> RejectReason | None:
        """Lot caps for `volume` more on `symbol` (besides open and working volume)."""
        spec = self._instruments[symbol]
        if volume > scaled_cap(spec.max_order_volume_per_100k, account.initial_balance):
            return RejectReason.ORDER_VOLUME_CAP
        exposure = volume + self._symbol_volume(account.account_id, symbol)
        if exposure > scaled_cap(spec.max_symbol_volume_per_100k, account.initial_balance):
            return RejectReason.SYMBOL_VOLUME_CAP
        return None

    def _symbol_volume(self, account_id: str, symbol: str) -> int:
        """Open volume plus the volume of working orders that open positions
        (limit, stop and trailing entries; a trailing stop adds none)."""
        open_volume = sum(p.volume for p in self._open(account_id) if p.symbol == symbol)
        working_volume = 0
        for o in self._orders.of_account(account_id):
            r = o.request
            if r.symbol == symbol and r.volume is not None:
                working_volume += r.volume
        return open_volume + working_volume

    def _trailing_stop_of(self, position_id: str) -> TrailingOrder | None:
        return self._orders.stop_of(position_id)

    def _trailing_placed(self, order: TrailingOrder) -> TrailingPlaced:
        return TrailingPlaced(
            ts=self._now,
            order_id=order.order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            side=order.side,
            kind=order.kind,
            volume=order.volume,
            position_id=order.position_id,
            distance=order.distance,
            step=order.step,
            level=order.level,
            price_seq=order.moved_seq,
            config_version=self._version,
        )

    def _move_trailing(self, pending: PendingOrder, quote: Quote) -> TrailingOrder:
        """Move a trailing order's level if `quote` beats it by a step;
        return the order as it now stands (moves are appended by callers)."""
        order = pending.request
        assert isinstance(order, TrailingOrder)
        level = trailing_move(order, quote.ask if order.follows_ask else quote.bid)
        if level is None:
            return order
        moved = replace(order, level=level, moved_at=self._now, moved_seq=quote.seq)
        self._orders[order.order_id] = replace(pending, request=moved)
        return moved

    def _open_position(
        self, request: OrderRequest, quote: Quote
    ) -> tuple[PositionOpened] | tuple[PositionOpened, CommissionCharged] | RejectReason:
        spec = self._instruments[request.symbol]
        price = quote.ask if request.side is Side.BUY else quote.bid
        try:
            notional = self._notional_usd(spec, price, request.volume)
            free = self.free_margin(request.account_id)
        except _NoRate:
            return RejectReason.NO_CONVERSION_RATE
        margin = notional / spec.leverage
        margin_cents = cents_ceil(margin)
        commission_cents = cents_half_even(notional * spec.commission)
        if margin_cents + commission_cents > free:
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
        )
        opened = PositionOpened(
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
        if commission_cents == 0:
            return (opened,)
        self._ledger.post(
            transfer(
                f"commission:{request.order_id}",
                self._now,
                "commission",
                debit=FIRM_COMMISSION,
                credit=balance_account(request.account_id),
                amount=commission_cents,
                reference=request.order_id,
            )
        )
        charged = CommissionCharged(
            ts=self._now,
            position_id=request.order_id,
            account_id=request.account_id,
            symbol=request.symbol,
            notional_cents=cents_half_even(notional),
            commission_cents=commission_cents,
            config_version=self._version,
        )
        return (opened, charged)

    def _trigger_orders(self, quote: Quote) -> list[EngineEvent]:
        """Pending and trailing entries on `quote`'s symbol, in placement
        order. Inside a news block nothing opens (held), but trailing levels
        still move."""
        events: list[EngineEvent] = []
        in_window = self.is_blocked(quote.symbol)
        for order_id, pending in self._orders.on_symbol(quote.symbol):
            request = pending.request
            blocked = in_window and request.account_id not in self._news_exempt
            if isinstance(request, TrailingOrder):
                if request.kind is not TrailingKind.ENTRY:
                    continue  # stops: `_trigger_stops`
                order = self._move_trailing(pending, quote)
                if order is not request:
                    events.append(self._trailing_moved(order))
                price = quote.ask if order.follows_ask else quote.bid
                if blocked or not trailing_reached(order, price):
                    continue
                del self._orders[order_id]
                events.extend(self._fill_trailing_entry(order, quote))
                continue
            if blocked or not _triggered(request, quote):
                continue
            del self._orders[order_id]
            opened = self._open_position(request, quote)
            if isinstance(opened, RejectReason):
                events.append(OrderCancelled(self._now, order_id, request.account_id, opened))
            else:
                events.extend(opened)
        return events

    def _fill_trailing_entry(self, order: TrailingOrder, quote: Quote) -> list[EngineEvent]:
        """Open the position of a triggered trailing entry (already removed
        from the working orders): caps, then margin, at the trigger."""
        assert order.volume is not None
        account = self._accounts[order.account_id]
        reason = self._cap_reason(account, order.symbol, order.volume)
        if reason is None:
            request = OrderRequest(
                order_id=order.order_id,
                account_id=order.account_id,
                symbol=order.symbol,
                side=order.side,
                order_type=OrderType.TRAILING,
                volume=order.volume,
                level=order.level,
            )
            opened = self._open_position(request, quote)
            if not isinstance(opened, RejectReason):
                return list(opened)
            reason = opened
        return [OrderCancelled(self._now, order.order_id, order.account_id, reason)]

    def _trailing_moved(self, order: TrailingOrder) -> TrailingMoved:
        return TrailingMoved(
            self._now, order.order_id, order.account_id, order.level, order.moved_seq
        )

    def _trigger_stops(self, quote: Quote) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        for position in self._positions.on_symbol(quote.symbol):
            if position.side is Side.BUY:
                price = quote.bid
                sl_hit = position.stop_loss is not None and price <= position.stop_loss
                tp_hit = position.take_profit is not None and price >= position.take_profit
            else:
                price = quote.ask
                sl_hit = position.stop_loss is not None and price >= position.stop_loss
                tp_hit = position.take_profit is not None and price <= position.take_profit
            trailing_hit = False
            trailing = self._trailing_stop_of(position.position_id)
            if trailing is not None:
                order = self._move_trailing(self._orders[trailing.order_id], quote)
                if order is not trailing:
                    events.append(self._trailing_moved(order))
                trailing_hit = trailing_reached(order, price)
            if sl_hit:
                reason = CloseReason.STOP_LOSS
            elif trailing_hit:
                assert trailing is not None
                del self._orders[trailing.order_id]  # consumed by its own fill
                reason = CloseReason.TRAILING_STOP
            elif tp_hit:
                reason = CloseReason.TAKE_PROFIT
            else:
                continue
            events.extend(self._close(position, position.volume, quote, reason))
        return events

    def _close(
        self, position: Position, volume: int, quote: Quote, reason: CloseReason
    ) -> list[EngineEvent]:
        """Close `volume` of a position: its PositionClosed, then, when none
        is left, the cancellation of its trailing stop (if it has one)."""
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
        cancelled: list[EngineEvent] = []
        if remaining == 0:
            del self._positions[position.position_id]
            trailing = self._trailing_stop_of(position.position_id)
            if trailing is not None:
                del self._orders[trailing.order_id]
                cancelled.append(
                    OrderCancelled(
                        self._now,
                        trailing.order_id,
                        position.account_id,
                        RejectReason.POSITION_CLOSED,
                    )
                )
        else:
            self._positions[position.position_id] = replace(
                position,
                volume=remaining,
                margin_usd=position.margin_usd * remaining / position.volume,
                close_count=count,
            )
        closed = PositionClosed(
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
        return [closed, *cancelled]

    def _exit_price(self, position: Position) -> int:
        quote = self._quotes[position.symbol]
        return quote.bid if position.side is Side.BUY else quote.ask

    def _pnl_usd(self, position: Position, exit_price: int, volume: int) -> Fraction:
        """Exact P&L in USD of closing `volume` of a position at `exit_price`."""
        spec = self._instruments[position.symbol]
        move = (exit_price - position.open_price) * position.side.sign
        quote_ccy = Fraction(move * volume * spec.contract_size, 10**spec.digits * 100)
        return self._to_usd(spec.quote_currency, quote_ccy)

    def _notional_usd(self, spec: InstrumentSpec, price: int, volume: int) -> Fraction:
        notional = Fraction(price * volume * spec.contract_size, 10**spec.digits * 100)
        return self._to_usd(spec.quote_currency, notional)

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


def _trailing_params(distance: int, step: int) -> RejectReason | None:
    """Distance and step must be whole points, each at least 1."""
    if not (is_int(distance) and is_int(step)) or distance < 1 or step < 1:
        return RejectReason.INVALID_TRAILING
    return None


def _triggered(request: OrderRequest, quote: Quote) -> bool:
    level = request.level
    assert level is not None
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
