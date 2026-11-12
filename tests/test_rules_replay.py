"""Deterministic replay through the rules supervisor.

Random seeded sessions cross the 17:00 New York reset and a US news event,
with large price jumps so accounts breach. The same seed must give the same
events and ledger; at every step the ledger balances, breached accounts are
flat and locked, and an account's status never returns to active.
"""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.ledger import Entry, Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.symbols import SYMBOLS
from propfirm.market.synthetic import SyntheticConfig, generate
from propfirm.rules.events import AccountStatus
from propfirm.rules.news import EventScope, RestrictedEvent, trade_blocks
from propfirm.rules.products import PRODUCTS, RULESETS
from propfirm.rules.supervisor import Event, RuleSupervisor
from propfirm.trading.engine import TradingEngine
from propfirm.trading.instruments import default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side
from tests.rules_rig import ny

T0 = ny(2026, 10, 28, 16, 58)  # two minutes before the reset
SYMS = ("EUR/USD", "USD/JPY", "XAU/USD", "BTC/USD")
ACCOUNTS = {
    "A": (PRODUCTS["lite"][0], 500_000),
    "B": (RULESETS["instant-funded-v1"], 500_000),  # retired: its replays stay
    "C": (PRODUCTS["classic"][0], 1_000_000),
}


def run_session(seed: int) -> tuple[list[Event], tuple[Entry, ...]]:
    rng = random.Random(seed)
    specs = {s: spec for s, spec in default_instruments().items() if s in SYMS}
    news = [RestrictedEvent("usd", T0 + 60_000, EventScope.CURRENCY, "USD")]
    ledger = Ledger()
    engine = TradingEngine(
        specs,
        ledger,
        config_version="rsv-replay",
        guard_config=GuardConfig(default_gap_threshold_ms=5_000),
        trade_blocks=trade_blocks(news, {s: SYMBOLS[s] for s in SYMS}),
    )
    sup = RuleSupervisor(engine)
    events: list[Event] = []
    for account, (rules, size) in ACCOUNTS.items():
        events += sup.open_account(account, rules, size, T0)
    feed = SyntheticConfig(seed=f"rules-{seed}", symbols=SYMS, jump_per_mille=30)
    seen: dict[str, AccountStatus] = {}
    n = 0
    for update in generate(feed, T0, T0 + 240_000):
        events += sup.on_price(update)
        if rng.randrange(3) == 0:
            n += 1
            events += command(sup, rng, n, update.receive_ts)
        ledger.verify()
        for account in ACCOUNTS:
            status = sup.status(account)
            previous = seen.get(account, AccountStatus.ACTIVE)
            assert previous is AccountStatus.ACTIVE or status is previous  # final states stay
            seen[account] = status
            if status is AccountStatus.BREACHED:
                assert engine.positions(account) == ()
                assert engine.pending_orders(account) == ()
                assert engine.is_locked(account)
    return events, ledger.entries()


def command(sup: RuleSupervisor, rng: random.Random, n: int, now: int) -> list[Event]:
    engine = sup.engine
    account = rng.choice(list(ACCOUNTS))
    ts = now + rng.randrange(50)
    positions = engine.positions(account)
    if positions and rng.randrange(3) == 0:
        return sup.close_position(account, rng.choice(positions).position_id, ts)
    symbol = rng.choice(SYMS)
    quote = engine.quote(symbol)
    mid = quote.mid if quote else 1_000
    order_type = rng.choice([OrderType.MARKET, OrderType.MARKET, OrderType.LIMIT])
    side = rng.choice([Side.BUY, Side.SELL])
    level = None
    if order_type is OrderType.LIMIT:
        offset = rng.randrange(1, 200)
        level = max(1, mid - offset if side is Side.BUY else mid + offset)
    volume = rng.choice([10, 50, 100, 200])
    request = OrderRequest(f"o{n}", account, symbol, side, order_type, volume, level)
    return sup.place_order(request, ts)


@settings(max_examples=10, deadline=None)
@given(st.integers(min_value=0, max_value=2**32))
def test_same_inputs_give_identical_rule_outcomes(seed: int) -> None:
    assert run_session(seed) == run_session(seed)


def test_sessions_cross_the_reset_breach_and_hit_the_news_block() -> None:
    kinds: set[str] = set()
    reasons: set[str] = set()
    for seed in range(6):
        events, _ = run_session(seed)
        kinds |= {type(e).__name__ for e in events}
        reasons |= {str(getattr(e, "reason", "")) for e in events}
    assert {"DayStarted", "AccountBreached", "PositionOpened", "PositionClosed"} <= kinds
    assert {"news_window", "breach", "account_locked"} <= reasons
