"""The spread that follows the market (trading-v2), the first model kept for
replay, and the switch between them through the journal."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from propfirm.journal.inputs import InputRejected, OpenAccount, PlaceOrder, UpdateTrading
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.spread import (
    MarketSpreadConfig,
    SpreadConfig,
    SpreadModel,
    lower_median,
)
from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.service.setup import policy_changes, setup_from_policy
from propfirm.trading.events import PositionOpened
from propfirm.trading.instruments import default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side

MARKET = MarketSpreadConfig(min_points=5, max_points=50)  # EUR/USD as shipped
T0 = 1_790_000_000_000


def spread_after(config: Any, prices: list[int], news: bool = False) -> int:
    model = SpreadModel(config)
    for price in prices:
        model.observe(price)
    return model.spread_points(news)


def calm(n: int, start: int = 110_000) -> list[int]:
    """n prices moving 1 point at a time, up and down."""
    return [start + (i % 2) for i in range(n)]


# --- the market model -----------------------------------------------------------------


def test_a_calm_market_gets_the_normal_spread() -> None:
    assert spread_after(MARKET, calm(30)) == 6  # 5 points, rounded up to even


def test_a_single_jump_does_not_widen_the_spread() -> None:
    prices = [*calm(10), 110_300, *calm(5, 110_300)]  # +300 points once
    assert spread_after(MARKET, prices) == 6
    # the first model: mean of 15 changes (9 x 1, 299, 0, 4 x 1) = 20.8 -> 21 -> 22
    assert spread_after(SpreadConfig(min_points=5), prices) == 22


def test_a_jump_before_enough_history_does_not_widen_it() -> None:
    assert spread_after(MARKET, [110_000, 110_300]) == 6  # 1 change < min_samples 3
    assert spread_after(MARKET, [110_000, 110_300, 110_600]) == 6  # 2 changes
    assert spread_after(SpreadConfig(min_points=5), [110_000, 110_300]) == 300  # first model


def test_a_fast_market_widens_it_and_calm_brings_it_back() -> None:
    fast = [110_000 + 20 * (i % 2) for i in range(21)]  # 20-point moves, 20 times
    assert spread_after(MARKET, fast) == 20
    assert spread_after(MARKET, [*fast, *calm(11, 110_000)]) == 6  # most changes calm again


def test_the_spread_never_passes_the_cap_outside_news() -> None:
    wild = [110_000 + 1_000 * (i % 2) for i in range(21)]  # 1,000-point moves
    assert spread_after(MARKET, wild) == 50
    assert spread_after(MARKET, wild, news=True) == 150  # news multiplies the cap
    odd_cap = MarketSpreadConfig(min_points=5, max_points=51)
    assert spread_after(odd_cap, wild) == 52  # always even, so the mid stays the feed price


def test_news_multiplies_the_normal_spread() -> None:
    assert spread_after(MARKET, calm(30), news=True) == 16  # 5 x 3 = 15 -> 16


def test_the_multiplier_scales_the_market_move() -> None:
    fast = [110_000 + 20 * (i % 2) for i in range(21)]
    half = replace(MARKET, vol_multiplier=Fraction(1, 2))
    assert spread_after(half, fast) == 10


def test_lower_median() -> None:
    assert lower_median([3]) == 3
    assert lower_median([300, 1, 2]) == 2
    assert lower_median([1, 300]) == 1  # even count: the lower middle
    assert lower_median([4, 1, 300, 2]) == 2
    with pytest.raises(ValueError):
        lower_median([])


@pytest.mark.parametrize(
    "bad",
    [
        {"min_points": 0, "max_points": 5},
        {"min_points": 10, "max_points": 5},  # cap below the normal spread
        {"min_points": 5, "max_points": 50, "window": 2, "min_samples": 3},
        {"min_points": 5, "max_points": 50, "min_samples": 0},
        {"min_points": 5, "max_points": 50, "vol_multiplier": Fraction(-1)},
        {"min_points": 5, "max_points": 50, "news_multiplier": 0},
    ],
)
def test_market_spread_config_validation(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MarketSpreadConfig(**bad)


@given(
    st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=60),
    st.booleans(),
    st.integers(min_value=1, max_value=40),
    st.integers(min_value=1, max_value=20),
)
def test_market_quote_invariants(prices: list[int], news: bool, low: int, extra: int) -> None:
    config = MarketSpreadConfig(min_points=low, max_points=low + extra, window=7, min_samples=2)
    model = SpreadModel(config)
    for p in prices:
        model.observe(p)
    q = model.quote("S", prices[-1], seq=1, source_ts=0, receive_ts=0, news=news)
    widest = (low + extra) * (3 if news else 1)
    assert low <= q.spread <= widest + 1 and q.spread % 2 == 0
    assert 1 <= q.bid < q.ask
    assert q.bid + q.ask == 2 * q.mid or q.bid == 1


def test_switching_models_keeps_the_recent_changes() -> None:
    model = SpreadModel(SpreadConfig(min_points=5))
    for price in [*calm(10), 110_300]:
        model.observe(price)
    assert model.spread_points(False) == 32  # mean of nine 1s and 299 = 30.8 -> 31 -> 32
    model.reconfigure(MARKET)
    assert model.spread_points(False) == 6  # same history, market model


# --- through the engine and the journal --------------------------------------------------


def eurusd_buy(order_id: str, ts: int) -> PlaceOrder:
    request = OrderRequest(order_id, "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100)
    return PlaceOrder(request, ts)


def run(setup_policy: Any, inputs: list[Any]) -> tuple[Machine, list[list[object]]]:
    machine = Machine(CODEC.encode(setup_from_policy(setup_policy)), {1: bytes(32)})
    machine.use_price_key(1)
    out = []
    for n, item in enumerate(inputs, start=1):
        events = CODEC.decode(machine.apply(f"{n}-0", item).record.events)
        assert isinstance(events, tuple)
        out.append(list(events))
    return machine, out


def jump_then_buy(order_id: str, start: int) -> list[Any]:
    prices = [*calm(10), 110_300]
    return [
        *(PriceUpdate("EUR/USD", start + i, T0 + i, T0 + i, p) for i, p in enumerate(prices)),
        eurusd_buy(order_id, T0 + 50),
    ]


def opened(events: list[object]) -> PositionOpened:
    return next(e for e in events if isinstance(e, PositionOpened))


def test_a_market_order_right_after_a_jump_pays_the_normal_spread() -> None:
    shipped = load_policy()
    inputs = [OpenAccount("A", "lite-s1-v1", 1_000_000, T0), *jump_then_buy("o1", 1)]
    _, out = run(shipped, inputs)
    assert opened(out[-1]).price == 110_303  # ask = 110,300 + 6 / 2


def test_the_switch_is_journaled_and_replays_exactly() -> None:
    shipped = load_policy()
    first = replace(
        shipped,
        trading=replace(
            shipped.trading,
            config_version="trading-v1",
            instruments=tuple(default_instruments().values()),  # the first model
        ),
    )
    to_market = UpdateTrading("trading-v2", shipped.trading.instruments, None, T0 + 100)
    inputs = [
        OpenAccount("A", "lite-s1-v1", 1_000_000, T0),
        *jump_then_buy("o1", 1),  # under the first model
        to_market,
        *(
            PriceUpdate("EUR/USD", 12 + i, T0 + 200 + i, T0 + 200 + i, 110_300 + (i % 2))
            for i in range(3)
        ),
        eurusd_buy("o2", T0 + 300),  # under the market model
    ]
    machine, out = run(first, inputs)
    assert opened(out[12]).price > 110_303  # first model: the jump widened the spread
    assert opened(out[-1]).price <= 110_304  # market model: normal spread again
    assert opened(out[-1]).config_version == "trading-v2"
    again, _ = run(first, inputs)  # same inputs, same records
    assert again.head == machine.head
    state = CODEC.decode(CODEC.encode(machine.state()))  # a snapshot holds both configs
    assert state == machine.state()


def test_an_existing_journal_switches_at_engine_start() -> None:
    """A journal begun under trading-v1 gets one UpdateTrading for every
    symbol when the engine starts with the shipped policy (trading-v2)."""
    shipped = load_policy()
    legacy = replace(
        shipped,
        trading=replace(
            shipped.trading,
            config_version="trading-v1",
            instruments=tuple(default_instruments().values()),
        ),
    )
    machine, _ = run(legacy, [])
    (update,) = policy_changes(shipped, machine, T0)
    assert isinstance(update, UpdateTrading) and update.config_version == "trading-v2"
    assert len(update.instruments) == 30 and update.guard is None
    events = CODEC.decode(machine.apply("1-0", update).record.events)
    assert isinstance(events, tuple) and not any(isinstance(e, InputRejected) for e in events)
    spec = machine.supervisor.engine.instruments["EUR/USD"]
    assert spec.spread == MarketSpreadConfig(min_points=5, max_points=50)
    assert policy_changes(shipped, machine, T0) == []
