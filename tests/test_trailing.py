"""Trailing orders (CC-3, D43): trailing stops on positions and trailing entries.

The test instruments have a fixed 10-point spread: at feed price p the bid is
p - 5 and the ask p + 5.
"""

import pytest

from propfirm.market.spread import NewsWindow
from propfirm.trading.events import (
    CloseReason,
    EngineEvent,
    OrderCancelled,
    OrderPlaced,
    PositionClosed,
    PositionOpened,
    RejectReason,
    SymbolFrozen,
    SymbolResumed,
    TrailingLevelMoved,
    TrailingStopSet,
)
from propfirm.trading.orders import OrderRequest, OrderType, Side, Trail
from tests.engine_rig import T0, Rig, one, rejected


def place(
    rig: Rig,
    order_id: str,
    side: Side,
    order_type: OrderType = OrderType.MARKET,
    *,
    trail: Trail | None = None,
    trailing_stop: Trail | None = None,
    level: int | None = None,
    sl: int | None = None,
    tp: int | None = None,
    volume: int = 100,
    at: int = T0,
) -> list[EngineEvent]:
    request = OrderRequest(
        order_id,
        "A",
        "EUR/USD",
        side,
        order_type,
        volume,
        level,
        sl,
        tp,
        trail=trail,
        trailing_stop=trailing_stop,
    )
    return rig.engine.place_order(request, at)


def moves(events: list[EngineEvent]) -> list[int]:
    return [e.level for e in events if isinstance(e, TrailingLevelMoved)]


def level_of(rig: Rig, position_id: str) -> int | None:
    [position] = [p for p in rig.engine.positions("A") if p.position_id == position_id]
    return position.trailing_level


# --- settings and validation --------------------------------------------------------


@pytest.mark.parametrize(("distance", "step"), [(0, 1), (1, 0), (-5, 1), (True, 1), (1, 2.0)])
def test_trail_needs_whole_points_of_at_least_one(distance: object, step: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Trail(distance, step)  # type: ignore[arg-type]


def test_trailing_order_needs_a_trail_and_no_level() -> None:
    trail = Trail(50, 1)
    with pytest.raises(ValueError):
        OrderRequest("o", "A", "EUR/USD", Side.BUY, OrderType.TRAILING, 100)
    with pytest.raises(ValueError):
        OrderRequest("o", "A", "EUR/USD", Side.BUY, OrderType.TRAILING, 100, 110_000, trail=trail)
    with pytest.raises(ValueError):
        OrderRequest("o", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100, trail=trail)
    with pytest.raises(TypeError):
        OrderRequest("o", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100, trailing_stop=50)  # type: ignore[arg-type]
    OrderRequest("o", "A", "EUR/USD", Side.BUY, OrderType.TRAILING, 100, trail=trail)


# --- trailing stop on a position ------------------------------------------------------


def test_buy_trailing_stop_follows_the_bid_up_and_closes_at_the_bid() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # bid 109_995
    events = place(rig, "p1", Side.BUY, trailing_stop=Trail(100, 1))
    one(events, PositionOpened)
    stop = one(events, TrailingStopSet)
    assert (stop.trail, stop.level, stop.price_seq) == (Trail(100, 1), 109_895, 1)

    moved = one(rig.price("EUR/USD", 110_200), TrailingLevelMoved)  # bid 110_195
    assert (moved.ref, moved.level, moved.price_seq) == ("p1", 110_095, 2)
    assert moves(rig.price("EUR/USD", 110_150)) == []  # never moves back
    assert level_of(rig, "p1") == 110_095

    closed = one(rig.price("EUR/USD", 110_100, at=T0 + 1), PositionClosed)  # bid 110_095
    assert (closed.reason, closed.close_price, closed.ts) == (
        CloseReason.TRAILING_STOP,
        110_095,
        T0 + 1,
    )
    assert closed.pnl_cents == 9_000  # 90 points on 1 lot
    assert rig.engine.positions("A") == ()
    rig.ledger.verify()


def test_sell_trailing_stop_follows_the_ask_down_and_closes_at_the_ask() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # ask 110_005
    stop = one(place(rig, "p1", Side.SELL, trailing_stop=Trail(100, 1)), TrailingStopSet)
    assert stop.level == 110_105
    assert moves(rig.price("EUR/USD", 109_800)) == [109_905]  # ask 109_805
    assert moves(rig.price("EUR/USD", 109_850)) == []
    closed = one(rig.price("EUR/USD", 109_900), PositionClosed)  # ask 109_905
    assert (closed.reason, closed.close_price) == (CloseReason.TRAILING_STOP, 109_905)


def test_the_level_moves_only_by_at_least_the_step() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY, trailing_stop=Trail(100, 20))  # starts at 109_895
    assert moves(rig.price("EUR/USD", 110_019)) == []  # would gain 19
    assert moves(rig.price("EUR/USD", 110_020)) == [109_915]  # gains exactly 20
    assert moves(rig.price("EUR/USD", 110_039)) == []  # 19 more
    assert moves(rig.price("EUR/USD", 110_100)) == [109_995]  # jumps to the new value


def test_set_change_and_remove_a_trailing_stop() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    one(place(rig, "p1", Side.BUY), PositionOpened)
    rig.price("EUR/USD", 110_300)  # bid 110_295
    stop = one(rig.engine.set_trailing_stop("A", "p1", Trail(50, 5), T0), TrailingStopSet)
    assert (stop.level, stop.price_seq) == (110_245, 2)
    stop = one(rig.engine.set_trailing_stop("A", "p1", Trail(200, 1), T0), TrailingStopSet)
    assert stop.level == 110_095  # a replacement restarts from the current bid
    removed = one(rig.engine.set_trailing_stop("A", "p1", None, T0), TrailingStopSet)
    assert (removed.trail, removed.level) == (None, None)
    assert moves(rig.price("EUR/USD", 111_000)) == []
    assert not [e for e in rig.price("EUR/USD", 100_000) if isinstance(e, PositionClosed)]


def test_set_trailing_stop_rejections() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY)
    trail = Trail(50, 1)
    assert rejected(rig.engine.set_trailing_stop("A", "nope", trail, T0)) is (
        RejectReason.UNKNOWN_POSITION
    )
    assert rejected(rig.engine.set_trailing_stop("A", "p1", Trail(109_995, 1), T0)) is (
        RejectReason.INVALID_STOPS  # would start at 0 points
    )
    with pytest.raises(TypeError):
        rig.engine.set_trailing_stop("A", "p1", 50, T0)  # type: ignore[arg-type]
    one(rig.price("EUR/USD", 110_000, at=T0 + 100, lag=2_001), SymbolFrozen)
    assert rejected(rig.engine.set_trailing_stop("A", "p1", trail, T0 + 100)) is (
        RejectReason.SYMBOL_FROZEN
    )
    rig.engine.lock_account("A", T0 + 100)
    assert rejected(rig.engine.set_trailing_stop("A", "p1", trail, T0 + 100)) is (
        RejectReason.ACCOUNT_LOCKED
    )


def test_market_order_whose_trailing_stop_would_start_below_one_point_is_rejected() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = place(rig, "p1", Side.BUY, trailing_stop=Trail(109_995, 1))
    assert rejected(events) is RejectReason.INVALID_STOPS
    assert rig.engine.positions("A") == ()


def test_fixed_stop_loss_and_trailing_stop_first_reached_closes() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY, sl=109_900, trailing_stop=Trail(200, 1))  # trail at 109_795
    closed = one(rig.price("EUR/USD", 109_900), PositionClosed)  # bid 109_895
    assert closed.reason is CloseReason.STOP_LOSS

    rig.price("EUR/USD", 110_000)
    place(rig, "p2", Side.BUY, sl=109_700, trailing_stop=Trail(200, 1))  # trail 109_795
    rig.price("EUR/USD", 110_300)  # trail moves to 110_095, above the stop-loss
    closed = one(rig.price("EUR/USD", 110_000), PositionClosed)  # bid 109_995
    assert closed.reason is CloseReason.TRAILING_STOP

    rig.price("EUR/USD", 110_000)
    place(rig, "p3", Side.BUY, sl=109_900, trailing_stop=Trail(100, 1))  # trail 109_895
    closed = one(rig.price("EUR/USD", 109_000), PositionClosed)  # both on one update
    assert closed.reason is CloseReason.STOP_LOSS


def test_partial_close_keeps_the_trailing_stop_on_the_rest() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY, trailing_stop=Trail(100, 1))
    rig.price("EUR/USD", 110_200)
    one(rig.engine.close_position("A", "p1", T0, volume=40), PositionClosed)
    assert level_of(rig, "p1") == 110_095
    closed = one(rig.price("EUR/USD", 110_000), PositionClosed)
    assert (closed.reason, closed.volume) == (CloseReason.TRAILING_STOP, 60)


# --- trailing entries -------------------------------------------------------------------


def test_trailing_buy_follows_the_ask_down_and_fills_on_the_rebound() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # ask 110_005
    placed = one(place(rig, "o1", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1)), OrderPlaced)
    assert (placed.order_type, placed.level, placed.trail) == (
        OrderType.TRAILING,
        110_055,
        Trail(50, 1),
    )
    moved = one(rig.price("EUR/USD", 109_800), TrailingLevelMoved)  # ask 109_805
    assert (moved.ref, moved.level) == ("o1", 109_855)
    [pending] = rig.engine.pending_orders("A")
    assert pending.level == 109_855
    assert not [e for e in rig.price("EUR/USD", 109_849) if isinstance(e, PositionOpened)]
    opened = one(rig.price("EUR/USD", 109_860, at=T0 + 7), PositionOpened)  # ask 109_865
    assert (opened.price, opened.order_type, opened.ts) == (109_865, OrderType.TRAILING, T0 + 7)
    assert rig.engine.pending_orders("A") == ()


def test_trailing_sell_follows_the_bid_up_and_fills_on_the_drop() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # bid 109_995
    placed = one(place(rig, "o1", Side.SELL, OrderType.TRAILING, trail=Trail(50, 1)), OrderPlaced)
    assert placed.level == 109_945
    assert moves(rig.price("EUR/USD", 110_300)) == [110_245]  # bid 110_295
    opened = one(rig.price("EUR/USD", 110_250), PositionOpened)  # bid 110_245
    assert (opened.side, opened.price) == (Side.SELL, 110_245)


def test_trailing_entry_can_carry_a_trailing_stop_from_its_fill() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "o1", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1), trailing_stop=Trail(80, 1))
    events = rig.price("EUR/USD", 110_100)  # ask 110_105 >= 110_055: fills
    one(events, PositionOpened)
    stop = one(events, TrailingStopSet)
    assert (stop.position_id, stop.level) == ("o1", 110_015)  # bid 110_095 - 80


def test_trailing_sell_that_would_start_below_one_point_is_rejected() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = place(rig, "o1", Side.SELL, OrderType.TRAILING, trail=Trail(109_995, 1))
    assert rejected(events) is RejectReason.INVALID_LEVEL


def test_trailing_entry_stops_are_checked_against_its_start_and_again_at_the_fill() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # trailing buy would start at 110_055
    bad = place(rig, "o1", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1), sl=110_060)
    assert rejected(bad) is RejectReason.INVALID_STOPS
    one(place(rig, "o2", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1), sl=110_000), OrderPlaced)
    rig.price("EUR/USD", 109_500)  # level falls to 109_555, under the stop-loss
    cancelled = one(rig.price("EUR/USD", 109_560), OrderCancelled)  # exit price: bid 109_555
    assert (cancelled.order_id, cancelled.reason) == ("o2", RejectReason.INVALID_STOPS)
    assert rig.engine.positions("A") == ()


def test_locking_cancels_trailing_entries() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "o1", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1))
    cancelled = one(rig.engine.lock_account("A", T0), OrderCancelled)
    assert cancelled.reason is RejectReason.ACCOUNT_LOCKED
    assert moves(rig.price("EUR/USD", 109_000)) == []


# --- freezes and news blocks -----------------------------------------------------------


def test_frozen_symbol_holds_levels_then_the_first_fresh_price_moves_then_triggers() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY, trailing_stop=Trail(100, 1))  # 109_895
    events = rig.price("EUR/USD", 111_000, at=T0 + 100, lag=2_001)  # delayed: not applied
    one(events, SymbolFrozen)
    assert moves(events) == []
    assert level_of(rig, "p1") == 109_895
    events = rig.price("EUR/USD", 112_000, at=T0 + 3_000)  # fresh: moves to 111_895
    one(events, SymbolResumed)
    assert moves(events) == [111_895]

    rig.price("EUR/USD", 111_000, at=T0 + 3_100, lag=2_001)  # frozen again
    events = rig.price("EUR/USD", 110_500, at=T0 + 6_000)  # gaps through 111_895
    closed = one(events, PositionClosed)
    assert (closed.reason, closed.close_price) == (CloseReason.TRAILING_STOP, 110_495)


def test_news_block_holds_trailing_entries_but_levels_move_and_trailing_stops_execute() -> None:
    block = NewsWindow(frozenset({"EUR/USD"}), T0 + 1_000, T0 + 2_000)
    rig = Rig(trade_blocks=[block])
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY, trailing_stop=Trail(100, 1))  # stop 109_895
    place(rig, "o1", Side.BUY, OrderType.TRAILING, trail=Trail(50, 1))  # entry 110_055

    inside = rig.price("EUR/USD", 109_700, at=T0 + 1_500)  # bid 109_695: stop executes
    assert one(inside, PositionClosed).reason is CloseReason.TRAILING_STOP
    assert moves(inside) == [109_755]  # the entry still follows the ask down
    held = rig.price("EUR/USD", 109_800, at=T0 + 1_600)  # ask 109_805 crosses: held
    assert not [e for e in held if isinstance(e, PositionOpened)]
    after = rig.price("EUR/USD", 109_800, at=T0 + 2_001)  # first price after the block
    assert one(after, PositionOpened).price == 109_805


def test_trailing_stop_can_be_set_inside_a_news_block() -> None:
    block = NewsWindow(frozenset({"EUR/USD"}), T0 + 1_000, T0 + 2_000)
    rig = Rig(trade_blocks=[block])
    rig.price("EUR/USD", 110_000)
    place(rig, "p1", Side.BUY)
    rig.price("EUR/USD", 110_000, at=T0 + 1_500)
    assert rig.engine.is_blocked("EUR/USD")
    stop = one(rig.engine.set_trailing_stop("A", "p1", Trail(10, 1), T0 + 1_500), TrailingStopSet)
    assert stop.level == 109_985
