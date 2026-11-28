"""Automatic risk limits in the engine (D-14.34): class percents and
symbol-side cuts on new exposure, close only, replay through the input and
the state snapshot."""

import pytest

from propfirm.customers.exposure import Cut
from propfirm.customers.risk_sync import build
from propfirm.journal.inputs import SetRiskLimits
from propfirm.journal.machine import CODEC
from propfirm.journal.stream import decode_input, encode_input
from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.policy import load_policy
from propfirm.trading.engine import EngineState, TradingEngine
from propfirm.trading.events import (
    OrderPlaced,
    PositionClosed,
    PositionOpened,
    RejectReason,
    RiskLimitsSet,
)
from propfirm.trading.orders import OrderType, Side
from propfirm.trading.risk import NO_LIMITS, RiskCut, RiskLimits, limited_cap
from tests.engine_rig import T0, VERSION, Rig, instruments, one, rejected

# $100,000: forex 50 lots per order
HALVE_EUR_BUY = RiskCut("EUR/USD", Side.BUY, 50, ("B", "C"))
QUARTER_EUR_BUY = RiskCut("EUR/USD", Side.BUY, 25, ("A", "B", "C"))


def limits(
    classes: tuple[tuple[str, str], ...] = (),
    cuts: tuple[RiskCut, ...] = (),
    percent: tuple[tuple[str, int], ...] = (("C", 50), ("D", 0)),
) -> RiskLimits:
    return RiskLimits(7, classes, percent, cuts)


def test_percent_is_the_smallest_that_applies() -> None:
    r = limits((("ACC_A", "A"), ("ACC_C", "C"), ("ACC_D", "D")), (HALVE_EUR_BUY,))
    assert r.percent("ACC_B", "EUR/USD", Side.BUY) == 50  # B, by the cut
    assert r.percent("ACC_B", "EUR/USD", Side.SELL) == 100
    assert r.percent("ACC_A", "EUR/USD", Side.BUY) == 100  # the cut skips A
    assert r.percent("ACC_C", "EUR/USD", Side.BUY) == 50  # class and cut: not 25
    assert r.percent("ACC_C", "GBP/USD", Side.SELL) == 50
    assert r.percent("ACC_D", "EUR/USD", Side.SELL) == 0
    assert NO_LIMITS.percent("X", "EUR/USD", Side.BUY) == 100
    assert limited_cap(5_000, 50) == 2_500 and limited_cap(501, 50) == 250  # rounds down


def test_limits_are_validated() -> None:
    with pytest.raises(ValueError):
        RiskLimits(1, (("B1", "B"),), (), ())  # B is the default: not listed
    with pytest.raises(ValueError):
        RiskLimits(1, (("Z", "C"), ("A", "C")), (), ())  # not sorted
    with pytest.raises(ValueError):
        RiskCut("EUR/USD", Side.BUY, 100, ("B",))  # not a cut
    with pytest.raises(ValueError):
        RiskLimits(1, (), (), (HALVE_EUR_BUY, HALVE_EUR_BUY))


def test_a_cut_binds_new_orders_on_its_side_only() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    one(rig.engine.set_risk_limits(limits(cuts=(HALVE_EUR_BUY,)), T0), RiskLimitsSet)
    assert rejected(rig.order("o1", Side.BUY, volume=2_501)) is RejectReason.RISK_LIMIT
    one(rig.order("o2", Side.BUY, volume=2_500), PositionOpened)
    # the symbol cap is cut too: 2,500 open + 1 more is over 50 % of it
    # (EUR/USD symbol cap 100 lots: 50 % is 50 lots); fill up to it
    one(rig.order("o3", Side.BUY, volume=2_500), PositionOpened)
    assert rejected(rig.order("o4", Side.BUY, volume=1)) is RejectReason.RISK_LIMIT
    # the other side is not cut, and closing is never limited
    one(rig.order("o5", Side.SELL, volume=100), PositionOpened)
    one(rig.engine.close_position("A", "o2", T0), PositionClosed)


def test_a_cut_skips_classes_it_does_not_list() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.engine.set_risk_limits(limits((("A", "A"),), (HALVE_EUR_BUY,)), T0)
    one(rig.order("o1", Side.BUY, volume=5_000), PositionOpened)  # class A: full caps
    rig.engine.set_risk_limits(limits((("A", "A"),), (QUARTER_EUR_BUY,)), T0)
    assert rejected(rig.order("o2", Side.BUY, volume=1)) is RejectReason.RISK_LIMIT


def test_class_d_is_close_only() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    one(rig.order("p1", Side.BUY, volume=100), PositionOpened)
    rig.engine.set_risk_limits(limits((("A", "D"),)), T0)
    assert rejected(rig.order("o1", Side.SELL, volume=1)) is RejectReason.CLOSE_ONLY
    pending = rig.order("o2", Side.BUY, volume=1, order_type=OrderType.LIMIT, level=100_000)
    assert rejected(pending) is RejectReason.CLOSE_ONLY
    one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    # lifted: the next set leaves the account out
    rig.engine.set_risk_limits(NO_LIMITS, T0)
    one(rig.order("o3", Side.SELL, volume=1), PositionOpened)


def test_a_working_order_placed_before_a_cut_fills_as_placed() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    one(
        rig.order("w1", Side.BUY, volume=5_000, order_type=OrderType.LIMIT, level=109_000),
        OrderPlaced,
    )
    rig.engine.set_risk_limits(limits((("A", "D"),)), T0)
    filled = rig.price("EUR/USD", 108_900, at=T0 + 1)
    one(filled, PositionOpened)


def test_the_limits_survive_a_snapshot() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.engine.set_risk_limits(limits((("A", "C"),), (HALVE_EUR_BUY,)), T0)
    state = rig.engine.state()
    assert state.risk.cuts == (HALVE_EUR_BUY,)
    again = TradingEngine(
        instruments(),
        Ledger(),
        config_version=VERSION,
        guard_config=GuardConfig(delay_threshold_ms=2_000, default_gap_threshold_ms=60_000),
    )
    decoded = CODEC.decode(CODEC.encode(state))
    assert isinstance(decoded, EngineState)
    again.restore(decoded)
    assert again.risk_limits == rig.engine.risk_limits
    # a state without limits encodes as before they existed
    assert "risk" not in CODEC.encode(Rig().engine.state())


def test_the_input_travels_the_stream() -> None:
    item = SetRiskLimits(limits((("ACC1", "C"),), (HALVE_EUR_BUY,)), T0)
    assert decode_input(encode_input(item)) == item


def test_the_platform_builds_the_set_from_classes_and_cuts() -> None:
    policy = load_policy()
    cuts = {
        ("EUR/USD", "buy"): Cut("halve", 1),
        ("XAU/USD", "sell"): Cut("quarter", 2, 3),
    }
    classes = {"ACC3": "C", "ACC1": "A", "ACC2": "B", "ACC4": "D"}
    built = build(9, classes, cuts, policy.classes, policy.exposure)
    assert built == RiskLimits(
        9,
        (("ACC1", "A"), ("ACC3", "C"), ("ACC4", "D")),
        (("C", 50), ("D", 0)),
        (
            RiskCut("EUR/USD", Side.BUY, 50, ("B", "C")),
            RiskCut("XAU/USD", Side.SELL, 25, ("A", "B", "C")),
        ),
    )
    assert build(0, {}, {}, policy.classes, policy.exposure) == RiskLimits(
        0, (), (("C", 50), ("D", 0)), ()
    )
