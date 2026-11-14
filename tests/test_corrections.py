"""Bad-price corrections in the engine (build plan step 1, line 7; D-14.4):
a signed balance correction, and reinstating an account a bad price
breached."""

from typing import Any

import pytest

from propfirm.journal.inputs import (
    CorrectBalance,
    InputRejected,
    InputRejectReason,
    ReinstateAccount,
)
from propfirm.journal.machine import CODEC
from propfirm.rules.events import (
    AccountBreached,
    AccountReinstated,
    AccountStatus,
    BalanceCorrected,
)
from propfirm.rules.products import PRODUCTS
from propfirm.trading.events import PositionClosed, PositionOpened, RejectReason
from propfirm.trading.orders import Side
from tests.engine_rig import USD_100K, one, rejected
from tests.rules_rig import RulesRig, ny

T0 = ny(2026, 11, 17, 10, 0)
LITE = PRODUCTS["lite"][0]  # stage 1: 5% daily, 10% overall


def of(kind: type, events: list[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


def breached_by_a_spike() -> tuple[RulesRig, int]:
    """10 lots long EUR/USD; a bad print 60 pips down breaches the daily
    loss limit; the real price never left 1.10000."""
    r = RulesRig(LITE, USD_100K, T0)
    r.price(110_000, T0)
    one(r.order(Side.BUY, 1_000, T0 + 1), PositionOpened)
    events = r.price(109_400, T0 + 2)  # the bad price
    one(events, AccountBreached)
    (closed,) = of(PositionClosed, events)
    return r, closed.pnl_cents


def test_a_correction_moves_the_balance_once_and_not_todays_pnl() -> None:
    r = RulesRig(LITE, USD_100K, T0)
    r.price(110_000, T0)
    before = r.engine.balance("A")
    events = r.sup.correct_balance("A", 25_000, "COR00000001", T0 - 86_400_000, T0 + 1)
    corrected = one(events, BalanceCorrected)
    assert (corrected.amount_cents, corrected.balance_cents) == (25_000, before + 25_000)
    assert r.engine.balance("A") == before + 25_000
    assert r.sup.correction_refusal("A", "COR00000001") == "duplicate_correction"
    with pytest.raises(ValueError, match="duplicate_correction"):
        r.sup.correct_balance("A", 25_000, "COR00000001", T0, T0 + 2)
    # corrections for an earlier day's price: not today's result either way
    r.sup.correct_balance("A", -25_000, "COR00000002", T0 - 86_400_000, T0 + 3)
    assert r.engine.balance("A") == before
    assert r.sup.status("A") is AccountStatus.ACTIVE


def test_a_breach_from_a_bad_price_is_corrected_then_reinstated() -> None:
    r, pnl = breached_by_a_spike()
    assert pnl < 0 and r.sup.status("A") is AccountStatus.BREACHED
    assert rejected(r.order(Side.BUY, 10, T0 + 3)) is RejectReason.ACCOUNT_LOCKED
    assert r.sup.reinstatement_refusal("A") == "still_breached"  # correct first
    r.sup.correct_balance("A", -pnl, "COR00000001", T0 + 2, T0 + 4)  # today's loss reversed
    assert r.sup.reinstatement_refusal("A") is None
    events = r.sup.reinstate("A", "COR00000001", T0 + 2, T0 + 5)
    one(events, AccountReinstated)
    assert r.sup.status("A") is AccountStatus.ACTIVE
    assert r.engine.balance("A") == USD_100K
    one(r.order(Side.BUY, 10, T0 + 6), PositionOpened)  # trading again
    assert r.sup.reinstatement_refusal("A") == "account_not_breached"


def test_the_inputs_validate_and_encode() -> None:
    with pytest.raises(ValueError):
        CorrectBalance("A", 0, "COR00000001", 1, 1)
    with pytest.raises(TypeError):
        CorrectBalance("A", True, "COR00000001", 1, 1)
    for item in (
        CorrectBalance("A", -5, "COR00000001", 1, 1),
        ReinstateAccount("A", "COR00000001", 1, 1),
    ):
        assert CODEC.decode(CODEC.encode(item)) == item


def test_the_journal_refuses_a_reinstatement_before_its_correction() -> None:
    from propfirm.journal.inputs import apply

    r, pnl = breached_by_a_spike()
    out = apply(r.sup, {}, ReinstateAccount("A", "COR00000001", T0 + 2, T0 + 4))
    (refused,) = out
    assert isinstance(refused, InputRejected) and refused.reason is InputRejectReason.STILL_BREACHED
    apply(r.sup, {}, CorrectBalance("A", -pnl, "COR00000001", T0 + 2, T0 + 5))
    again = apply(r.sup, {}, CorrectBalance("A", -pnl, "COR00000001", T0 + 2, T0 + 6))
    assert isinstance(again[0], InputRejected)
    assert again[0].reason is InputRejectReason.DUPLICATE_CORRECTION
    assert any(
        isinstance(e, AccountReinstated)
        for e in apply(r.sup, {}, ReinstateAccount("A", "COR00000001", T0 + 2, T0 + 7))
    )


# --- the plan: what each account gets back ------------------------------------------


def ev(kind: str, seq: int, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "seq": seq, "account_id": fields.pop("account_id", "A"), **fields}


def test_a_close_at_the_bad_price_is_scaled_to_the_good_price() -> None:
    from propfirm.customers.corrections import plan

    events = [
        ev("PositionOpened", 1, position_id="p1", symbol="EUR/USD", price_seq=1),
        # long 10 lots from 1.10005, stopped at 1.09395 by a bad mid of 1.09400
        ev(
            "PositionClosed",
            2,
            position_id="p1",
            close_id="c1",
            symbol="EUR/USD",
            price_seq=2,
            open_price=110_005,
            close_price=109_395,
            pnl_cents=-61_000,
            remaining_volume=0,
        ),
        ev("AccountBreached", 2),
    ]
    (item,) = plan(events, "EUR/USD", 2, 109_400, 110_000)
    # at the good mid the fill is 1.09995: the loss is 1,000 cents, so +60,000 back
    assert (item.account_id, item.amount_cents, item.reinstate, item.fills) == (
        "A",
        60_000,
        True,
        1,
    )
    assert item.manual == ()


def test_an_open_at_the_bad_price_moves_each_later_close() -> None:
    from propfirm.customers.corrections import plan

    events = [
        # a limit filled at a bad low print: bought at 1.09400 instead of 1.10000
        ev("PositionOpened", 2, position_id="p1", symbol="EUR/USD", price_seq=2),
        ev(
            "PositionClosed",
            5,
            position_id="p1",
            close_id="c1",
            symbol="EUR/USD",
            price_seq=9,
            open_price=109_400,
            close_price=110_000,
            pnl_cents=60_000,
            remaining_volume=0,
        ),
    ]
    (item,) = plan(events, "EUR/USD", 2, 109_400, 110_000)
    assert (item.amount_cents, item.reinstate) == (-60_000, False)  # the windfall removed


def test_what_cannot_be_scaled_is_left_for_staff_and_others_are_untouched() -> None:
    from propfirm.customers.corrections import inputs, plan

    events = [
        ev("PositionOpened", 2, position_id="p1", symbol="EUR/USD", price_seq=2),  # still open
        ev(
            "PositionClosed",
            2,
            account_id="B",
            position_id="q1",
            close_id="d1",
            symbol="EUR/USD",
            price_seq=2,
            open_price=109_400,
            close_price=109_400,
            pnl_cents=0,
            remaining_volume=0,
        ),
        ev(
            "PositionClosed",
            3,
            account_id="C",
            position_id="r1",
            close_id="e1",
            symbol="GBP/USD",
            price_seq=2,
            open_price=1,
            close_price=2,
            pnl_cents=5,
            remaining_volume=0,
        ),
    ]
    items = plan(events, "EUR/USD", 2, 109_400, 110_000)
    assert [(i.account_id, i.amount_cents, i.manual) for i in items] == [
        ("A", 0, ("p1: opened at the bad price and still open",)),
        ("B", 0, ("d1: closed at its open price",)),
    ]
    assert inputs("COR00000001", 7, items, 8) == []  # nothing for the engine


def test_rounding_is_half_to_even_to_the_cent() -> None:
    from propfirm.customers.corrections import scaled

    assert (scaled(5, 1, 2), scaled(15, 1, 10), scaled(-5, 1, 2), scaled(7, 1, 2)) == (2, 2, -2, 4)


def test_taking_back_a_windfall_lowers_a_trailing_high_water_mark() -> None:
    one_step = PRODUCTS["one-step"][0]  # 10% trailing at end of day
    assert one_step.max_loss_kind.trails
    r = RulesRig(one_step, USD_100K, T0)
    r.price(110_000, T0)
    state = r.sup._state("A")
    state.high_water = USD_100K + 500_000  # raised by a bad print's profit
    r.sup.correct_balance("A", -400_000, "COR00000001", T0 - 86_400_000, T0 + 1)
    assert state.high_water == USD_100K + 100_000
    r.sup.correct_balance("A", -900_000, "COR00000002", T0 - 86_400_000, T0 + 2)
    assert state.high_water == USD_100K  # never below the start
    r.sup.correct_balance("A", 50_000, "COR00000003", T0 - 86_400_000, T0 + 3)
    assert state.high_water == USD_100K  # a loss given back leaves it
