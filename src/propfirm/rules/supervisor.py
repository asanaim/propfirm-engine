"""Enforces each account's rule set on top of the order engine.

Every input goes through `_run`:
1. Roll trading days: for each 17:00 New York boundary passed since the last
   input, advance the engine to the boundary, set each active account's daily
   base to the higher of balance and equity *at that instant* (prices are
   those last applied before it), and check for a breach there. A position
   carried across the reset with a floating loss larger than the daily limit
   therefore breaches at the reset itself.
2. Pass the input to the engine.
3. Record trading days and realized P&L per day from the engine's fills.
4. Check every active account: update the equity high-water mark (trailing
   max loss), then breach and pass in the order the tie rule sets (D11:
   breach wins).

News exits (D15): a stop-loss, take-profit or trailing stop that closes a
position inside a news trade block on its symbol is recorded as a
`NewsExitViolation`. Its profit (never its loss) is kept out of the amount
measured against the profit target when the rule set says so.

Floors (all in integer cents, compared at-or-below):
- daily floor = daily base - daily limit
- static max floor = initial balance - max loss
- trailing max floor = min(high-water mark - max loss, initial balance)

A breach closes every position at the current prices (gaps fill at the
actual price; the extra loss is recorded, not capped) and locks the account.
A pass locks the account read-only for review.

The engine only applies fresh prices, so no breach is ever decided on a
delayed price (D13: platform prices are the only reference).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction

from propfirm._checks import require_text
from propfirm.market.types import PriceUpdate
from propfirm.rules.clock import NEW_YORK_17, TradingClock
from propfirm.rules.events import (
    AccountBreached,
    AccountStatus,
    BreachReason,
    DayStarted,
    NewsExitViolation,
    RuleEvent,
    StagePassed,
)
from propfirm.rules.products import MaxLossKind, RuleSet, TargetBasis, TieRule
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import CloseReason, EngineEvent, PositionClosed, PositionOpened
from propfirm.trading.orders import OrderRequest, Trail

type Event = EngineEvent | RuleEvent


@dataclass(slots=True)
class _State:
    account_id: str
    rules: RuleSet
    initial_balance: int
    status: AccountStatus
    day_base: int
    high_water: int
    trading_days: set[int] = field(default_factory=set)  # day ids with an opening fill
    day_pnl: dict[int, int] = field(default_factory=dict)  # realized cents per day id
    news_excluded: int = 0  # cents of news-exit profit kept out of the target (D15)


class RuleSupervisor:
    def __init__(self, engine: TradingEngine, clock: TradingClock = NEW_YORK_17) -> None:
        self._engine = engine
        self._clock = clock
        self._accounts: dict[str, _State] = {}
        self._day: int | None = None  # current trading day id (its 17:00 start)

    # --- inputs -------------------------------------------------------------

    def open_account(
        self, account_id: str, rules: RuleSet, initial_balance_cents: int, ts: int
    ) -> list[Event]:
        require_text("account_id", account_id)
        if initial_balance_cents not in rules.sizes_cents:
            raise ValueError(f"{initial_balance_cents} cents is not a {rules.product} size")
        events = self._roll(ts)
        events += self._engine.open_account(account_id, initial_balance_cents, ts)
        self._accounts[account_id] = _State(
            account_id=account_id,
            rules=rules,
            initial_balance=initial_balance_cents,
            status=AccountStatus.ACTIVE,
            day_base=initial_balance_cents,
            high_water=initial_balance_cents,
        )
        return events

    def on_price(self, update: PriceUpdate) -> list[Event]:
        return self._run(update.receive_ts, lambda: self._engine.on_price(update))

    def advance(self, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.advance(ts))

    def on_outage(self, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.on_outage(ts))

    def place_order(self, request: OrderRequest, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.place_order(request, ts))

    def cancel_order(self, account_id: str, order_id: str, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.cancel_order(account_id, order_id, ts))

    def close_position(
        self, account_id: str, position_id: str, ts: int, volume: int | None = None
    ) -> list[Event]:
        return self._run(
            ts, lambda: self._engine.close_position(account_id, position_id, ts, volume)
        )

    def modify_stops(
        self,
        account_id: str,
        position_id: str,
        stop_loss: int | None,
        take_profit: int | None,
        ts: int,
    ) -> list[Event]:
        return self._run(
            ts,
            lambda: self._engine.modify_stops(account_id, position_id, stop_loss, take_profit, ts),
        )

    def set_trailing_stop(
        self, account_id: str, position_id: str, trail: Trail | None, ts: int
    ) -> list[Event]:
        return self._run(
            ts, lambda: self._engine.set_trailing_stop(account_id, position_id, trail, ts)
        )

    # --- queries ------------------------------------------------------------

    @property
    def engine(self) -> TradingEngine:
        return self._engine

    def has_account(self, account_id: str) -> bool:
        return account_id in self._accounts

    def status(self, account_id: str) -> AccountStatus:
        return self._state(account_id).status

    def trading_days(self, account_id: str) -> int:
        return len(self._state(account_id).trading_days)

    def floors(self, account_id: str) -> tuple[int, int]:
        """(daily floor, max floor) in cents."""
        state = self._state(account_id)
        return self._daily_floor(state), self._max_floor(state)

    def consistency_ok(self, account_id: str) -> bool:
        """Best day <= the rule set's share of total realized profit (and
        total profit positive). True when the rule set has no such rule."""
        state = self._state(account_id)
        share = state.rules.consistency
        if share is None:
            return True
        return _consistent(state.day_pnl, share)

    def news_excluded(self, account_id: str) -> int:
        """Cents of news-exit profit kept out of the profit target (D15)."""
        return self._state(account_id).news_excluded

    # --- internals ----------------------------------------------------------

    def _state(self, account_id: str) -> _State:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise KeyError(f"unknown account {account_id!r}") from None

    def _active(self) -> list[_State]:
        return [s for s in self._accounts.values() if s.status is AccountStatus.ACTIVE]

    def _run(self, ts: int, action: Callable[[], list[EngineEvent]]) -> list[Event]:
        events = self._roll(ts)
        engine_events = action()
        events += engine_events
        events += self._record(engine_events)
        for state in self._active():
            events += self._check(state)
        return events

    def _roll(self, ts: int) -> list[Event]:
        now = max(ts, self._engine.now)
        if self._day is None:
            self._day = self._clock.day_start(now)
            return []
        events: list[Event] = []
        boundary = self._clock.next_boundary(self._day)
        while boundary <= now:
            events += self._engine.advance(boundary)
            self._day = boundary
            for state in self._active():
                equity = self._engine.equity(state.account_id)
                state.day_base = max(self._engine.balance(state.account_id), equity)
                state.high_water = max(state.high_water, equity)
                events.append(
                    DayStarted(
                        boundary,
                        state.account_id,
                        state.day_base,
                        self._daily_floor(state),
                        self._max_floor(state),
                        state.rules.rsv_id,
                    )
                )
                events += self._breach(state, equity)
            boundary = self._clock.next_boundary(boundary)
        return events

    def _record(self, events: list[EngineEvent]) -> list[Event]:
        """Update trading days and day P&L from the engine's fills; return
        the news-exit violations among them."""
        assert self._day is not None
        violations: list[Event] = []
        for event in events:
            state = self._accounts.get(getattr(event, "account_id", ""))
            if state is None:
                continue
            if isinstance(event, PositionOpened):
                state.trading_days.add(self._day)
            elif isinstance(event, PositionClosed):
                state.day_pnl[self._day] = state.day_pnl.get(self._day, 0) + event.pnl_cents
                if event.reason in _NEWS_EXITS and self._engine.is_blocked(event.symbol):
                    violations.append(self._news_exit(state, event))
        return violations

    def _news_exit(self, state: _State, close: PositionClosed) -> NewsExitViolation:
        excluded = max(close.pnl_cents, 0) if state.rules.exclude_news_exit_profit else 0
        state.news_excluded += excluded
        return NewsExitViolation(
            close.ts,
            state.account_id,
            close.close_id,
            close.symbol,
            close.reason,
            close.pnl_cents,
            excluded,
            state.rules.rsv_id,
        )

    def _check(self, state: _State) -> list[Event]:
        equity = self._engine.equity(state.account_id)
        state.high_water = max(state.high_water, equity)
        if state.rules.tie_rule is TieRule.PASS_WINS:
            passed = self._pass(state, equity)
            return passed or self._breach(state, equity)
        breached = self._breach(state, equity)
        return breached or self._pass(state, equity)

    def _daily_floor(self, state: _State) -> int:
        return state.day_base - state.rules.amount(state.rules.daily_loss, state.initial_balance)

    def _max_floor(self, state: _State) -> int:
        loss = state.rules.amount(state.rules.max_loss, state.initial_balance)
        if state.rules.max_loss_kind is MaxLossKind.STATIC:
            return state.initial_balance - loss
        return min(state.high_water - loss, state.initial_balance)

    def _breach(self, state: _State, equity: int) -> list[Event]:
        daily, maximum = self._daily_floor(state), self._max_floor(state)
        reasons: list[BreachReason] = []
        if equity <= daily:
            reasons.append(BreachReason.DAILY_LOSS)
        if equity <= maximum:
            reasons.append(BreachReason.MAX_LOSS)
        if not reasons:
            return []
        now = self._engine.now
        events: list[Event] = [
            AccountBreached(
                now,
                state.account_id,
                tuple(reasons),
                equity,
                daily,
                maximum,
                state.rules.rsv_id,
            )
        ]
        events += self._engine.close_all(state.account_id, now, CloseReason.BREACH)
        events += self._engine.lock_account(state.account_id, now)
        state.status = AccountStatus.BREACHED
        return events

    def _pass(self, state: _State, equity: int) -> list[Event]:
        rules = state.rules
        if rules.profit_target is None:
            return []
        account_id = state.account_id
        balance = self._engine.balance(account_id)
        measured = balance if rules.target_basis is TargetBasis.CLOSED_BALANCE else equity
        measured -= state.news_excluded
        target = state.initial_balance + rules.amount(rules.profit_target, state.initial_balance)
        if measured < target or len(state.trading_days) < rules.min_trading_days:
            return []
        if rules.flat_to_pass and (
            self._engine.positions(account_id) or self._engine.pending_orders(account_id)
        ):
            return []
        if (
            rules.consistency_at_pass
            and rules.consistency is not None
            and not _consistent(state.day_pnl, rules.consistency)
        ):
            return []
        now = self._engine.now
        events: list[Event] = [
            StagePassed(now, account_id, balance, len(state.trading_days), rules.rsv_id)
        ]
        events += self._engine.lock_account(account_id, now)
        state.status = AccountStatus.PASSED
        return events


_NEWS_EXITS = frozenset({CloseReason.STOP_LOSS, CloseReason.TAKE_PROFIT, CloseReason.TRAILING_STOP})


def _consistent(day_pnl: dict[int, int], share: Fraction) -> bool:
    total = sum(day_pnl.values())
    if total <= 0:
        return False
    return max(day_pnl.values()) <= share * total
