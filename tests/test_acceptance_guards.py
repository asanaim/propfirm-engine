"""Acceptance test 5, end to end through the engine: a symbol freezes when an
update is older than the threshold or overdue; while frozen it takes no new
orders and its stops and limits are held; it resumes on the first fresh
update, and anything that update crosses fills at its price."""

from propfirm.market.guards import FreezeReason
from propfirm.trading.events import (
    OrderCancelled,
    PositionClosed,
    PositionOpened,
    RejectReason,
    StaleUpdate,
    SymbolFrozen,
    SymbolResumed,
)
from propfirm.trading.orders import OrderType, Side
from tests.engine_rig import SYMS, T0, Rig, one, rejected


def test_delayed_price_freezes_holds_stops_and_rejects_new_orders() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000, at=T0)
    rig.order("p1", Side.BUY, sl=109_900)  # long, stop-loss below
    rig.order("o1", Side.BUY, order_type=OrderType.LIMIT, level=109_500)  # held limit
    rig.order("o2", Side.BUY, order_type=OrderType.LIMIT, level=109_000)  # to cancel

    # Stale price: produced at T0+1s, received 3s later (> 2s). It crosses
    # both the stop-loss and the limit, but must not be applied.
    events = rig.price("EUR/USD", 108_000, at=T0 + 1_000, lag=3_000)
    frozen = one(events, SymbolFrozen)
    assert (frozen.reason, frozen.ts) == (FreezeReason.DELAYED, T0 + 4_000)
    assert one(events, StaleUpdate).lag_ms == 3_000
    assert not [e for e in events if isinstance(e, PositionClosed | PositionOpened)]
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None and quote.mid == 110_000  # last good price kept

    t = T0 + 4_100
    assert rejected(rig.order("p2", Side.BUY, at=t)) is RejectReason.SYMBOL_FROZEN
    assert (
        rejected(rig.order("o3", Side.SELL, order_type=OrderType.LIMIT, level=111_000, at=t))
        is RejectReason.SYMBOL_FROZEN
    )
    assert rejected(rig.engine.close_position("A", "p1", t)) is RejectReason.SYMBOL_FROZEN
    assert rejected(rig.engine.modify_stops("A", "p1", None, None, t)) is (
        RejectReason.SYMBOL_FROZEN
    )
    assert one(rig.engine.cancel_order("A", "o2", t), OrderCancelled).reason is None

    # Fresh price: resumes, then the held stop-loss fills at the fresh bid,
    # not at its level (gap). The limit at 109,500 is not crossed (ask 109,855).
    events = rig.price("EUR/USD", 109_850, at=T0 + 5_000, lag=100)
    kinds = [type(e) for e in events]
    assert kinds.index(SymbolResumed) < kinds.index(PositionClosed)
    closed = one(events, PositionClosed)
    assert (closed.close_price, closed.reason) == (109_845, "stop_loss")
    assert closed.price_seq == 3  # the fresh update, not the stale one
    assert [o.request.order_id for o in rig.engine.pending_orders("A")] == ["o1"]
    rig.ledger.verify()


def test_overdue_symbol_freezes_on_time_and_resumes_on_fresh_price() -> None:
    rig = Rig()  # gap threshold 60 s
    rig.price("EUR/USD", 110_000, at=T0)
    rig.price("XAU/USD", 330_000, at=T0)
    assert rig.engine.advance(T0 + 60_000) == []
    events = rig.order("p1", Side.BUY, at=T0 + 60_001)  # time passing alone freezes
    frozen = [e for e in events if isinstance(e, SymbolFrozen)]
    assert {(e.symbol, e.reason) for e in frozen} == {
        ("EUR/USD", FreezeReason.OVERDUE),
        ("XAU/USD", FreezeReason.OVERDUE),
    }
    assert rejected(events) is RejectReason.SYMBOL_FROZEN
    assert one(rig.price("EUR/USD", 110_010, at=T0 + 70_000), SymbolResumed).symbol == "EUR/USD"
    one(rig.order("p2", Side.BUY, at=T0 + 70_000), PositionOpened)
    assert rig.engine.is_frozen("XAU/USD")


def test_outage_freezes_every_symbol_and_keeps_last_prices() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    rig.price("EUR/USD", 110_100)
    equity = rig.engine.equity("A")
    events = rig.engine.on_outage(T0 + 10)
    assert {e.symbol for e in events if isinstance(e, SymbolFrozen)} == set(SYMS)
    assert rig.engine.equity("A") == equity  # positions keep their last price
    assert rejected(rig.engine.close_position("A", "p1", T0 + 20)) is RejectReason.SYMBOL_FROZEN
    one(rig.price("EUR/USD", 110_100, at=T0 + 30), SymbolResumed)
    one(rig.engine.close_position("A", "p1", T0 + 40), PositionClosed)


def test_no_symbol_trades_before_its_first_price() -> None:
    rig = Rig()
    assert rig.engine.is_frozen("EUR/USD")
    assert rejected(rig.order("p1", Side.BUY)) is RejectReason.SYMBOL_FROZEN


def test_delay_at_threshold_is_still_fresh() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = rig.price("EUR/USD", 110_020, at=T0 + 1_000, lag=2_000)
    assert not [e for e in events if isinstance(e, SymbolFrozen)]
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None and quote.mid == 110_020
