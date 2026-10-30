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

News trade blocks (Confirmed Configuration CC-3): inside a block on an
affected symbol no trade is opened or closed: market orders, new pending
orders and manual closes are rejected, and pending orders do not trigger
(they are held until the block ends) [DEFAULT]. Stop-losses and take-profits
still execute [DEFAULT]. Changing stops is allowed.

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
- Commission (only symbols priced with `SpreadAndCommission`): a share of
  the notional at the fill price, charged once when a position opens and
  posted to the ledger at once, so it counts in balance, equity, the loss
  limits and the day's P&L. Rounded half-even to the cent. The margin
  check requires margin plus commission.
- Not implemented: margin call and stop-out (not in the project record; the
  loss limits of the rules engine, step 5, act far earlier).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction

from propfirm._checks import is_int, require_int, require_text
from propfirm.ledger import Ledger, transfer
from propfirm.market.guards import FeedGuards, GuardChange, GuardConfig, GuardState
from propfirm.market.sequence import SeqStatus, SequenceTracker
from propfirm.market.spread import NewsWindow, Quote, SpreadModel, SpreadState, in_news
from propfirm.market.symbols import SymbolSpec
from propfirm.market.types import PriceUpdate
from propfirm.money import cents_ceil, cents_half_even
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
    RejectReason,
    StaleUpdate,
    StopsModified,
    SymbolFrozen,
    SymbolResumed,
    TradingConfigChanged,
)
from propfirm.trading.instruments import InstrumentSpec, conversion_route, scaled_cap
from propfirm.trading.orders import OrderRequest, OrderType, PendingOrder, Position, Side

FIRM_CAPITAL = "firm:capital"
FIRM_TRADING_PNL = "firm:trading_pnl"
FIRM_COMMISSION = "firm:commission"
FIRM_PAYOUTS = "firm:payouts"  # paid out of trading accounts, owed to traders until sent


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
    orders: tuple[PendingOrder, ...]  # placement order
    positions: tuple[Position, ...]  # opening order
    guards: GuardState
    sequences: tuple[tuple[str, int], ...]
    spreads: tuple[tuple[str, SpreadState], ...]  # configured order
    calendar: tuple[tuple[str, NewsWindow], ...]  # scheduled, not yet ended
    calendar_ids: tuple[str, ...]  # sorted: every event id ever scheduled
    config_version: str  # the trading settings in force (they can change by input)
    instruments: tuple[InstrumentSpec, ...]  # configured order
    guard: GuardConfig


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
        self._quotes: dict[str, Quote] = {}
        self._accounts: dict[str, _Account] = {}
        self._orders: dict[str, PendingOrder] = {}  # pending, in placement order
        self._positions: dict[str, Position] = {}  # open, in opening order
        self._used_ids: set[str] = set()
        self._locked: set[str] = set()
        self._calendar: dict[str, NewsWindow] = {}  # event id -> window, both uses
        self._calendar_ids: set[str] = set()
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
            self._spread_news(symbol),
            self._now,
        )
        self._quotes[symbol] = quote
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
        if self.is_blocked(position.symbol):
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

    def has_entry(self, entry_id: str) -> bool:
        return self._ledger.has(entry_id)

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
        self._orders = {o.request.order_id: o for o in state.orders}
        self._positions = {p.position_id: p for p in state.positions}
        self._calendar = dict(state.calendar)
        self._calendar_ids = set(state.calendar_ids)
        if not set(self._calendar) <= self._calendar_ids:
            raise ValueError("scheduled event without an id record")

    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    def is_frozen(self, symbol: str) -> bool:
        return self._guards.is_frozen(symbol)

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
        for event_id, window in list(self._calendar.items()):
            if window.end_ts <= self._now:  # time only moves forward: never covers again
                del self._calendar[event_id]
        return [_guard_event(c) for c in self._guards.advance(self._now)]

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
        if self.is_blocked(request.symbol):
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
        assert request.level is not None
        if not _valid_level(request.side, request.order_type, request.level, quote):
            return RejectReason.INVALID_LEVEL
        if not _valid_stops(request.side, request.level, request.stop_loss, request.take_profit):
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
        events: list[EngineEvent] = []
        if self.is_blocked(quote.symbol):
            return events  # held: no trade opens inside a news block
        for order_id, pending in list(self._orders.items()):
            request = pending.request
            if request.symbol != quote.symbol or not _triggered(request, quote):
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
            if position.side is Side.BUY:
                price = quote.bid
                sl_hit = position.stop_loss is not None and price <= position.stop_loss
                tp_hit = position.take_profit is not None and price >= position.take_profit
            else:
                price = quote.ask
                sl_hit = position.stop_loss is not None and price >= position.stop_loss
                tp_hit = position.take_profit is not None and price <= position.take_profit
            if sl_hit:
                events.append(self._close(position, position.volume, quote, CloseReason.STOP_LOSS))
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
