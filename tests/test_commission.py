"""Commission charged when a position opens (crypto in the shipped policy,
trading-v3): the amount, the ledger, the margin check, the day's P&L, the
journal record and the switch of an existing journal."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.journal.inputs import (
    ClosePosition,
    InputRejected,
    OpenAccount,
    PlaceOrder,
    UpdateTrading,
)
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.spread import MarketSpreadConfig
from propfirm.market.types import PriceUpdate
from propfirm.money import cents_half_even
from propfirm.policy import load_policy
from propfirm.service.setup import policy_changes, setup_from_policy
from propfirm.trading.engine import FIRM_COMMISSION, balance_account
from propfirm.trading.events import (
    CommissionCharged,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
)
from propfirm.trading.instruments import SpreadAndCommission
from propfirm.trading.orders import OrderRequest, OrderType, Side

T0 = 1_790_000_000_000
SHIPPED = load_policy()
RATE = Fraction(525, 1_000_000)  # 0.0525 %
HALF = 545  # BTC: 1,089 points rounded up to 1,090; half each side


def run(policy: Any, inputs: list[Any]) -> tuple[Machine, list[list[object]]]:
    machine = Machine(CODEC.encode(setup_from_policy(policy)), {1: bytes(32)})
    machine.use_price_key(1)
    out = []
    for n, item in enumerate(inputs, start=1):
        events = CODEC.decode(machine.apply(f"{n}-0", item).record.events)
        assert isinstance(events, tuple)
        out.append(list(events))
    return machine, out


def btc(seq: int, price: int, ts: int) -> PriceUpdate:
    return PriceUpdate("BTC/USD", seq, ts, ts, price)


def order(
    order_id: str,
    volume: int,
    ts: int,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.MARKET,
    level: int | None = None,
) -> PlaceOrder:
    request = OrderRequest(order_id, "A", "BTC/USD", side, order_type, volume, level)
    return PlaceOrder(request, ts)


def commission_cents(price: int, volume: int) -> int:
    """Exact notional of `volume` hundredths of 1 BTC at `price` points (2 digits)."""
    return cents_half_even(Fraction(price * volume, 100 * 100) * RATE)


def test_shipped_crypto_has_a_raw_spread_and_a_commission() -> None:
    specs = {s.name: s for s in SHIPPED.trading.instruments}
    assert specs["BTC/USD"].commission == RATE
    assert specs["EUR/USD"].commission == 0 and specs["XAU/USD"].commission == 0
    model = specs["BTC/USD"].spread_model
    assert isinstance(model, MarketSpreadConfig) and model.min_points == 1_089


def test_a_market_buy_pays_the_raw_spread_and_the_commission() -> None:
    inputs = [OpenAccount("A", "lite-s1-v2", 10_000_000, T0), btc(1, 8_090_000, T0 + 1)]
    machine, out = run(SHIPPED, [*inputs, order("o1", 100, T0 + 2)])
    opened, charged = out[-1]
    ask = 8_090_000 + HALF
    assert isinstance(opened, PositionOpened) and opened.price == ask  # $10.90 spread
    expected = commission_cents(ask, 100)
    assert expected == 4_248  # 0.0525 % of $80,905.45 = $42.475... -> $42.48
    assert charged == CommissionCharged(
        T0 + 2, "o1", "A", "BTC/USD", 8_090_545, expected, "trading-v4"
    )
    engine = machine.supervisor.engine
    assert engine.balance("A") == 10_000_000 - expected
    entry = machine.ledger.entries()[-1]
    assert (entry.entry_id, entry.kind, entry.reference) == ("commission:o1", "commission", "o1")
    balances = machine.ledger.balances()
    assert balances[FIRM_COMMISSION] == expected
    assert balances[balance_account("A")] == 10_000_000 - expected


def test_closing_charges_no_commission() -> None:
    inputs = [
        OpenAccount("A", "lite-s1-v2", 10_000_000, T0),
        btc(1, 8_090_000, T0 + 1),
        order("o1", 100, T0 + 2),
        btc(2, 8_100_000, T0 + 3),
        ClosePosition("A", "o1", T0 + 4),
    ]
    machine, out = run(SHIPPED, inputs)
    (closed,) = out[-1]
    assert isinstance(closed, PositionClosed)
    pnl = (8_100_000 - HALF) - (8_090_000 + HALF)  # bid at close - ask at open, in cents
    assert closed.pnl_cents == pnl
    assert machine.supervisor.engine.balance("A") == 10_000_000 - 4_248 + pnl


def test_a_pending_order_pays_the_commission_when_it_fills() -> None:
    inputs = [
        OpenAccount("A", "lite-s1-v2", 10_000_000, T0),
        btc(1, 8_090_000, T0 + 1),
        order("o1", 50, T0 + 2, order_type=OrderType.LIMIT, level=8_080_000),
        btc(2, 8_079_000, T0 + 3),  # ask 8,079,545 <= level
    ]
    _, out = run(SHIPPED, inputs)
    assert [type(e) for e in out[2]] == [OrderPlaced]
    opened, charged = out[-1]
    assert isinstance(opened, PositionOpened) and isinstance(charged, CommissionCharged)
    assert charged.commission_cents == commission_cents(8_079_000 + HALF, 50)


def test_the_margin_check_includes_the_commission() -> None:
    price, volume, balance = 8_115_000, 41, 1_000_000  # 0.41 BTC on a $10,000 account
    ask = price + HALF
    notional = Fraction(ask * volume, 100 * 100)
    margin = notional / Fraction(333, 100)
    assert margin * 100 <= balance < margin * 100 + notional * RATE * 100  # fits without it
    inputs = [OpenAccount("A", "lite-s1-v2", balance, T0), btc(1, price, T0 + 1)]
    machine, out = run(SHIPPED, [*inputs, order("o1", volume, T0 + 2)])
    (rejected,) = out[-1]
    assert isinstance(rejected, OrderRejected)
    assert rejected.reason is RejectReason.INSUFFICIENT_MARGIN
    assert machine.supervisor.engine.balance("A") == balance  # nothing charged
    without = replace(
        SHIPPED,
        trading=replace(
            SHIPPED.trading,
            instruments=tuple(
                replace(s, spread=s.spread_model) for s in SHIPPED.trading.instruments
            ),
        ),
    )
    _, out = run(without, [*inputs, order("o1", volume, T0 + 2)])
    assert isinstance(out[-1][0], PositionOpened)  # the same order fits with no commission


def test_the_days_pnl_is_net_of_commission() -> None:
    inputs = [
        OpenAccount("A", "lite-s1-v2", 10_000_000, T0),
        btc(1, 8_090_000, T0 + 1),
        order("o1", 100, T0 + 2),
        ClosePosition("A", "o1", T0 + 3),  # same price: pays the spread
    ]
    machine, _ = run(SHIPPED, inputs)
    (account,) = machine.supervisor.state().accounts
    ((_, day_pnl),) = account.day_pnl
    assert day_pnl == -2 * HALF - 4_248


def test_commission_is_recorded_and_replays_exactly() -> None:
    inputs = [
        OpenAccount("A", "lite-s1-v2", 10_000_000, T0),
        btc(1, 8_090_000, T0 + 1),
        order("o1", 100, T0 + 2),
    ]
    first, _ = run(SHIPPED, inputs)
    again, _ = run(SHIPPED, inputs)
    assert again.head == first.head
    state = first.state()
    assert CODEC.decode(CODEC.encode(state)) == state  # the config travels in snapshots


def test_an_existing_journal_switches_to_commission_at_start() -> None:
    def all_in(spread: Any) -> MarketSpreadConfig:  # trading-v2: the whole cost in the spread
        assert isinstance(spread, MarketSpreadConfig)
        return replace(spread, min_points=5_313, max_points=53_130)

    v2 = replace(
        SHIPPED,
        trading=replace(
            SHIPPED.trading,
            config_version="trading-v2",
            instruments=tuple(
                replace(s, spread=all_in(s.spread_model)) if s.commission else s
                for s in SHIPPED.trading.instruments
            ),
        ),
    )
    machine, _ = run(v2, [OpenAccount("A", "lite-s1-v2", 10_000_000, T0)])
    (update,) = policy_changes(SHIPPED, machine, T0 + 1)
    assert isinstance(update, UpdateTrading) and update.config_version == "trading-v4"
    assert sorted(s.name for s in update.instruments) == [
        "BTC/USD",
        "ETH/USD",
        "LTC/USD",
        "SOL/USD",
        "XRP/USD",
    ]
    events = CODEC.decode(machine.apply("2-0", update).record.events)
    assert isinstance(events, tuple) and not any(isinstance(e, InputRejected) for e in events)
    assert isinstance(machine.supervisor.engine.instruments["BTC/USD"].spread, SpreadAndCommission)
    machine.apply("3-0", btc(1, 8_090_000, T0 + 2))
    out = CODEC.decode(machine.apply("4-0", order("o1", 100, T0 + 3)).record.events)
    assert isinstance(out, tuple) and isinstance(out[-1], CommissionCharged)


@pytest.mark.parametrize(
    "bad",
    [
        {"commission": Fraction(0)},
        {"commission": Fraction(1, 100)},  # 1 %: not below the bound
        {"commission": Fraction(-1, 10_000)},
    ],
)
def test_commission_settings_are_validated(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SpreadAndCommission(MarketSpreadConfig(min_points=5, max_points=50), **bad)


def test_the_commission_needs_a_market_spread() -> None:
    with pytest.raises(TypeError):
        SpreadAndCommission(object(), Fraction(1, 10_000))  # type: ignore[arg-type]


def test_commission_and_notional_round_half_even_to_the_cent() -> None:
    inputs = [OpenAccount("A", "lite-s1-v2", 10_000_000, T0), btc(1, 8_090_000, T0 + 1)]
    _, out = run(SHIPPED, [*inputs, order("o1", 1, T0 + 2)])  # 0.01 BTC
    _, charged = out[-1]
    assert isinstance(charged, CommissionCharged)
    # notional $809.0545 = 80,905.45 cents -> 80,905; commission 42.475 cents -> 42 (up: 43)
    assert (charged.notional_cents, charged.commission_cents) == (80_905, 42)
