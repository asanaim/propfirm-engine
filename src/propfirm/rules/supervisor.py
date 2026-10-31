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
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import NewsWindow
from propfirm.market.types import PriceUpdate
from propfirm.rules.clock import NEW_YORK_17, TradingClock
from propfirm.rules.events import (
    AccountBreached,
    AccountStatus,
    AccountVoided,
    BreachReason,
    DayStarted,
    NewsViolation,
    PayoutDebited,
    RuleEvent,
    StagePassed,
)
from propfirm.rules.products import MaxLossKind, RuleSet, TargetBasis, TieRule
from propfirm.trading.engine import TradingEngine, payout_entry
from propfirm.trading.events import (
    CloseReason,
    CommissionCharged,
    EngineEvent,
    PositionClosed,
    PositionOpened,
)
from propfirm.trading.instruments import InstrumentSpec
from propfirm.trading.orders import OrderRequest, Side

type Event = EngineEvent | RuleEvent

_WINDOW_FILLS = frozenset(
    {CloseReason.STOP_LOSS, CloseReason.TAKE_PROFIT, CloseReason.TRAILING_STOP}
)


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


@dataclass(frozen=True, slots=True)
class AccountState:
    account_id: str
    rules: RuleSet
    initial_balance: int
    status: AccountStatus
    day_base: int
    high_water: int
    trading_days: tuple[int, ...]  # sorted day ids
    day_pnl: tuple[tuple[int, int], ...]  # (day id, realized cents), sorted


@dataclass(frozen=True, slots=True)
class SupervisorState:
    accounts: tuple[AccountState, ...]  # opening order: decides event order
    day: int | None


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
        self._engine.set_news_restricted(account_id, rules.news_restricted)
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
    ) -> list[Event]:
        return self._run(
            ts,
            lambda: self._engine.place_trailing_entry(
                order_id, account_id, symbol, side, volume, distance, step, ts
            ),
        )

    def set_trailing_stop(
        self, order_id: str, account_id: str, position_id: str, distance: int, step: int, ts: int
    ) -> list[Event]:
        return self._run(
            ts,
            lambda: self._engine.set_trailing_stop(
                order_id, account_id, position_id, distance, step, ts
            ),
        )

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

    def schedule_news(self, event_id: str, window: NewsWindow, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.schedule_news(event_id, window, ts))

    def cancel_news(self, event_id: str, ts: int) -> list[Event]:
        return self._run(ts, lambda: self._engine.cancel_news(event_id, ts))

    def reconfigure(
        self,
        config_version: str,
        instruments: tuple[InstrumentSpec, ...],
        guard: GuardConfig | None,
        ts: int,
    ) -> list[Event]:
        return self._run(
            ts, lambda: self._engine.reconfigure(config_version, instruments, guard, ts)
        )

    # --- state --------------------------------------------------------------

    def state(self) -> SupervisorState:
        return SupervisorState(
            accounts=tuple(
                AccountState(
                    s.account_id,
                    s.rules,
                    s.initial_balance,
                    s.status,
                    s.day_base,
                    s.high_water,
                    tuple(sorted(s.trading_days)),
                    tuple(sorted(s.day_pnl.items())),
                )
                for s in self._accounts.values()
            ),
            day=self._day,
        )

    def restore(self, state: SupervisorState) -> None:
        """Load a state taken by `state()`; the engine is restored separately."""
        if self._accounts or self._day is not None:
            raise RuntimeError("restore needs a fresh supervisor")
        accounts = {
            a.account_id: _State(
                account_id=a.account_id,
                rules=a.rules,
                initial_balance=a.initial_balance,
                status=a.status,
                day_base=a.day_base,
                high_water=a.high_water,
                trading_days=set(a.trading_days),
                day_pnl=dict(a.day_pnl),
            )
            for a in state.accounts
        }
        if len(accounts) != len(state.accounts):
            raise ValueError("duplicate account in state")
        self._accounts = accounts
        self._day = state.day
        for a in accounts.values():  # derived from the rule sets, not engine state
            self._engine.set_news_restricted(a.account_id, a.rules.news_restricted)

    # --- queries ------------------------------------------------------------

    @property
    def engine(self) -> TradingEngine:
        return self._engine

    def void_account(self, account_id: str, case: str, ts: int) -> list[Event]:
        """An integrity decision: close the account's positions at the current
        prices and lock it (status voided). Only an active account."""
        state = self._state(account_id)
        if state.status is not AccountStatus.ACTIVE:
            raise ValueError(f"{account_id} is {state.status.value}")
        events = self._roll(ts)
        now = self._engine.now
        equity = self._engine.equity(account_id)
        events.append(AccountVoided(now, account_id, case, equity, state.rules.rsv_id))
        closed = self._engine.close_all(account_id, now, CloseReason.VOID)
        self._record(closed)
        events += closed
        events += self._engine.lock_account(account_id, now)
        state.status = AccountStatus.VOIDED
        for other in self._active():
            events += self._check(other)
        return events

    def payout_refusal(self, account_id: str, amount_cents: int, payout: str) -> str | None:
        """Why this payout debit cannot be applied, or None."""
        state = self._state(account_id)
        engine = self._engine
        if engine.has_entry(payout_entry(payout)):
            return "duplicate_payout"  # first: a retried debit is recognised whatever came after
        if state.status is not AccountStatus.ACTIVE:
            return "account_not_active"
        if engine.positions(account_id) or engine.has_working_orders(account_id):
            return "positions_open"
        if amount_cents > engine.balance(account_id) - state.initial_balance:
            return "payout_exceeds_profit"  # the balance never goes below the baseline
        return None

    def debit_payout(self, account_id: str, amount_cents: int, payout: str, ts: int) -> list[Event]:
        """Take a payout out of the account (checked by `payout_refusal`)."""
        refusal = self.payout_refusal(account_id, amount_cents, payout)
        if refusal is not None:
            raise ValueError(refusal)
        state = self._state(account_id)
        events = self._roll(ts)
        self._engine.debit_payout(account_id, amount_cents, payout, ts)
        state.day_base -= amount_cents
        state.high_water -= amount_cents
        events.append(
            PayoutDebited(
                self._engine.now,
                account_id,
                payout,
                amount_cents,
                self._engine.balance(account_id),
                state.rules.rsv_id,
            )
        )
        for other in self._active():
            events += self._check(other)
        return events

    def has_account(self, account_id: str) -> bool:
        return account_id in self._accounts

    def accounts(self) -> tuple[str, ...]:
        """Every account id, in opening order."""
        return tuple(self._accounts)

    def rule_set(self, account_id: str) -> RuleSet:
        return self._state(account_id).rules

    def status(self, account_id: str) -> AccountStatus:
        return self._state(account_id).status

    def trading_days(self, account_id: str) -> int:
        return len(self._state(account_id).trading_days)

    def trading_day_ids(self, account_id: str) -> tuple[int, ...]:
        """The trading days (their 17:00 New York start, ms UTC) with an
        opening fill, oldest first."""
        return tuple(sorted(self._state(account_id).trading_days))

    def day_pnl(self, account_id: str) -> tuple[tuple[int, int], ...]:
        """Realized P&L per trading day, net of commission: (day id, cents),
        oldest first (the figures the consistency rule reads)."""
        return tuple(sorted(self._state(account_id).day_pnl.items()))

    def initial_balance(self, account_id: str) -> int:
        return self._state(account_id).initial_balance

    def limits(self, account_id: str) -> tuple[int, int]:
        """(daily loss limit, maximum loss limit) in cents."""
        state = self._state(account_id)
        rules = state.rules
        initial = state.initial_balance
        return rules.amount(rules.daily_loss, initial), rules.amount(rules.max_loss, initial)

    def next_reset(self, account_id: str) -> tuple[int, int]:
        """(the next 17:00 New York reset, the daily floor from then) in ms
        UTC and cents, if equity stayed as it is now: the new daily base is
        the higher of balance and equity, so a floating loss of the daily
        limit or more breaches at the reset itself (audit, M12)."""
        state = self._state(account_id)
        engine = self._engine
        base = max(engine.balance(account_id), engine.equity(account_id))
        daily = state.rules.amount(state.rules.daily_loss, state.initial_balance)
        return self._clock.next_boundary(engine.now), base - daily

    def floors(self, account_id: str) -> tuple[int, int]:
        """(daily floor, max floor) in cents."""
        state = self._state(account_id)
        return self._daily_floor(state), self._max_floor(state)

    def target(self, account_id: str) -> int | None:
        """The level in cents the target basis must reach to pass; None
        when the stage has no profit target (funded)."""
        return self._target(self._state(account_id))

    def consistency_ok(self, account_id: str) -> bool:
        """Best day <= the rule set's share of total realized profit (and
        total profit positive). True when the rule set has no such rule."""
        state = self._state(account_id)
        share = state.rules.consistency
        if share is None:
            return True
        return _consistent(state.day_pnl, share)

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
        self._record(engine_events)
        events += self._news_violations(engine_events)
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

    def _record(self, events: list[EngineEvent]) -> None:
        assert self._day is not None
        for event in events:
            state = self._accounts.get(getattr(event, "account_id", ""))
            if state is None:
                continue
            if isinstance(event, PositionOpened):
                state.trading_days.add(self._day)
            elif isinstance(event, PositionClosed):
                state.day_pnl[self._day] = state.day_pnl.get(self._day, 0) + event.pnl_cents
            elif isinstance(event, CommissionCharged):  # the day's P&L is net of commission
                state.day_pnl[self._day] = state.day_pnl.get(self._day, 0) - event.commission_cents

    def _news_violations(self, events: list[EngineEvent]) -> list[Event]:
        """Stop-loss, take-profit and trailing-stop fills inside a window that
        binds the account (the only fills the engine lets through there)."""
        out: list[Event] = []
        for e in events:
            if not isinstance(e, PositionClosed) or e.reason not in _WINDOW_FILLS:
                continue
            state = self._accounts.get(e.account_id)
            if state is None or not self._engine.is_blocked_for(e.account_id, e.symbol):
                continue
            out.append(
                NewsViolation(
                    e.ts,
                    e.account_id,
                    e.position_id,
                    e.close_id,
                    e.symbol,
                    e.reason.value,
                    e.pnl_cents,
                    self._engine.news_events_at(e.symbol),
                    state.rules.rsv_id,
                )
            )
        return out

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

    def _target(self, state: _State) -> int | None:
        rules = state.rules
        if rules.profit_target is None:
            return None
        return state.initial_balance + rules.amount(rules.profit_target, state.initial_balance)

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
        target = self._target(state)
        if target is None:
            return []
        account_id = state.account_id
        balance = self._engine.balance(account_id)
        measured = balance if rules.target_basis is TargetBasis.CLOSED_BALANCE else equity
        if measured < target or len(state.trading_days) < rules.min_trading_days:
            return []
        if rules.flat_to_pass and (
            self._engine.positions(account_id) or self._engine.has_working_orders(account_id)
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


def _consistent(day_pnl: dict[int, int], share: Fraction) -> bool:
    total = sum(day_pnl.values())
    if total <= 0:
        return False
    return max(day_pnl.values()) <= share * total
