"""Trailing orders (M09, D43): trailing stops on positions, trailing buy and
sell entries, steps, triggers, freezes, news blocks, caps and records.

The rig quotes a fixed 10-point spread: bid = mid - 5, ask = mid + 5.
"""

from dataclasses import replace

from propfirm.journal.machine import CODEC
from propfirm.ledger import Ledger
from propfirm.market.spread import NewsWindow
from propfirm.trading.engine import TradingEngine
from propfirm.trading.events import (
    CloseReason,
    EngineEvent,
    OrderCancelled,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
    TrailingMoved,
    TrailingPlaced,
)
from propfirm.trading.orders import (
    OrderRequest,
    OrderType,
    Side,
    TrailingKind,
    TrailingOrder,
    trailing_move,
)
from tests.engine_rig import T0, VERSION, Rig, instruments, one, rejected


def stop(
    rig: Rig, pid: str, distance: int, step: int, at: int = T0, oid: str = "t1"
) -> list[EngineEvent]:
    return rig.engine.set_trailing_stop(oid, "A", pid, distance, step, at)


def entry(
    rig: Rig,
    side: Side,
    distance: int,
    step: int,
    volume: int = 100,
    at: int = T0,
    oid: str = "e1",
) -> list[EngineEvent]:
    return rig.engine.place_trailing_entry(oid, "A", "EUR/USD", side, volume, distance, step, at)


def working(rig: Rig) -> dict[str, TrailingOrder]:
    return {o.order_id: o for o, _ in rig.engine.trailing_orders("A")}


# --- trailing stops -------------------------------------------------------------------


def test_a_buy_positions_stop_follows_the_highest_bid_by_whole_steps() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # bid 109_995
    rig.order("p1", Side.BUY)
    placed = one(stop(rig, "p1", distance=100, step=20), TrailingPlaced)
    assert (placed.kind, placed.side, placed.level, placed.price_seq) == (
        TrailingKind.STOP,
        Side.BUY,
        109_895,
        1,
    )
    assert placed.config_version == VERSION
    assert rig.price("EUR/USD", 110_019) == []  # bid +19: below one step
    moved = one(rig.price("EUR/USD", 110_020, at=T0 + 5), TrailingMoved)  # bid 110_015
    assert (moved.order_id, moved.level, moved.price_seq, moved.ts) == ("t1", 109_915, 3, T0 + 5)
    assert rig.price("EUR/USD", 110_010) == []  # falls back: the level never moves against
    order = working(rig)["t1"]
    assert (order.level, order.moved_at, order.moved_seq) == (109_915, T0 + 5, 3)
    closed = one(rig.price("EUR/USD", 109_920), PositionClosed)  # bid 109_915: reached
    assert (closed.reason, closed.close_price, closed.remaining_volume) == (
        CloseReason.TRAILING_STOP,
        109_915,
        0,
    )
    assert working(rig) == {}
    rig.ledger.verify()


def test_a_sell_positions_stop_follows_the_lowest_ask() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # ask 110_005
    rig.order("p1", Side.SELL)
    assert one(stop(rig, "p1", distance=50, step=1), TrailingPlaced).level == 110_055
    assert one(rig.price("EUR/USD", 109_900), TrailingMoved).level == 109_955  # ask 109_905
    closed = one(rig.price("EUR/USD", 109_960), PositionClosed)  # ask 109_965 >= 109_955
    assert (closed.reason, closed.close_price) == (CloseReason.TRAILING_STOP, 109_965)


def test_a_gap_past_the_stop_closes_at_the_actual_price() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", distance=100, step=1)  # level 109_895
    closed = one(rig.price("EUR/USD", 109_500), PositionClosed)
    assert closed.close_price == 109_495  # not the level


def test_the_level_moves_first_and_a_move_never_triggers_on_its_own_update() -> None:
    order = TrailingOrder(
        "t", "A", "EUR/USD", Side.BUY, TrailingKind.STOP, None, "p", 10, 5, 100, T0, 1
    )
    assert trailing_move(order, 114) is None  # 104: beats 100 by 4 < step 5
    assert trailing_move(order, 115) == 105
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", distance=1, step=1)  # level bid - 1 = 109_994
    events = rig.price("EUR/USD", 110_100)  # moves to 110_094; bid 110_095 > level
    assert [type(e) for e in events] == [TrailingMoved]


def test_a_fixed_stop_loss_sits_beside_the_trailing_stop() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY, sl=109_950)
    stop(rig, "p1", distance=200, step=1)  # level 109_795, below the fixed stop
    events = rig.price("EUR/USD", 109_900)  # bid 109_895: the fixed stop is reached first
    closed = one(events, PositionClosed)
    assert closed.reason is CloseReason.STOP_LOSS
    cancelled = one(events, OrderCancelled)
    assert (cancelled.order_id, cancelled.reason) == ("t1", RejectReason.POSITION_CLOSED)


def test_on_one_update_reaching_both_stops_the_fixed_stop_is_named() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY, sl=109_900)
    stop(rig, "p1", distance=50, step=1)  # level 109_945
    events = rig.price("EUR/USD", 109_800)  # bid 109_795 reaches both
    assert one(events, PositionClosed).reason is CloseReason.STOP_LOSS
    assert one(events, OrderCancelled).reason is RejectReason.POSITION_CLOSED


def test_closing_the_position_cancels_its_stop_but_a_partial_close_keeps_it() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY, volume=100)
    stop(rig, "p1", distance=100, step=1)
    partial = rig.engine.close_position("A", "p1", T0, volume=40)
    assert [type(e) for e in partial] == [PositionClosed]
    assert "t1" in working(rig)
    full = rig.engine.close_position("A", "p1", T0)
    assert one(full, OrderCancelled).reason is RejectReason.POSITION_CLOSED
    assert working(rig) == {}


def test_the_stop_closes_the_whole_remaining_position() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY, volume=100)
    stop(rig, "p1", distance=100, step=1)
    rig.engine.close_position("A", "p1", T0, volume=30)
    closed = one(rig.price("EUR/USD", 109_800), PositionClosed)
    assert (closed.volume, closed.remaining_volume, closed.reason) == (70, 0, "trailing_stop")


def test_setting_another_stop_replaces_the_first() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", distance=100, step=1, oid="t1")
    events = stop(rig, "p1", distance=30, step=1, oid="t2")
    cancelled = one(events, OrderCancelled)
    assert (cancelled.order_id, cancelled.reason) == ("t1", None)
    assert one(events, TrailingPlaced).level == 109_965
    assert list(working(rig)) == ["t2"]
    assert rejected(stop(rig, "p1", distance=30, step=1, oid="t1")) is RejectReason.DUPLICATE_ID


def test_trailing_stop_rejections_are_recorded() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # bid 109_995
    rig.order("p1", Side.BUY)
    assert rejected(stop(rig, "nope", 10, 1)) is RejectReason.UNKNOWN_POSITION
    assert rejected(stop(rig, "p1", 0, 1)) is RejectReason.INVALID_TRAILING
    assert rejected(stop(rig, "p1", 10, 0)) is RejectReason.INVALID_TRAILING
    assert rejected(stop(rig, "p1", 109_995, 1)) is RejectReason.INVALID_TRAILING  # level 0
    assert one(stop(rig, "p1", 109_994, 1), TrailingPlaced).level == 1
    assert rejected(rig.engine.set_trailing_stop("x", "B", "p1", 10, 1, T0)) is (
        RejectReason.UNKNOWN_POSITION  # another account's position
    )
    rig.engine.lock_account("A", T0)
    assert rejected(stop(rig, "p1", 10, 1, oid="t9")) is RejectReason.ACCOUNT_LOCKED


# --- trailing entries -----------------------------------------------------------------


def test_a_trailing_buy_follows_the_lowest_ask_and_fills_like_a_stop() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # ask 110_005
    placed = one(entry(rig, Side.BUY, distance=100, step=10), TrailingPlaced)
    assert (placed.kind, placed.volume, placed.level, placed.position_id) == (
        TrailingKind.ENTRY,
        100,
        110_105,
        None,
    )
    assert one(rig.price("EUR/USD", 109_800), TrailingMoved).level == 109_905  # ask 109_805
    assert rig.engine.positions("A") == ()
    opened = one(rig.price("EUR/USD", 109_910), PositionOpened)  # ask 109_915 >= 109_905
    assert (opened.order_type, opened.price, opened.position_id, opened.side) == (
        OrderType.TRAILING,
        109_915,
        "e1",
        Side.BUY,
    )
    assert working(rig) == {}


def test_a_trailing_sell_follows_the_highest_bid() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)  # bid 109_995
    assert one(entry(rig, Side.SELL, distance=100, step=1), TrailingPlaced).level == 109_895
    assert one(rig.price("EUR/USD", 110_300), TrailingMoved).level == 110_195  # bid 110_295
    opened = one(rig.price("EUR/USD", 110_000), PositionOpened)  # bid 109_995: gap past
    assert (opened.price, opened.side) == (109_995, Side.SELL)


def test_trailing_entry_rejections_are_recorded() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    assert rejected(entry(rig, Side.BUY, 0, 1)) is RejectReason.INVALID_TRAILING
    assert rejected(entry(rig, Side.BUY, 1, 0)) is RejectReason.INVALID_TRAILING
    assert rejected(entry(rig, Side.SELL, 109_995, 1)) is RejectReason.INVALID_TRAILING
    assert rejected(entry(rig, Side.BUY, 10, 1, volume=0)) is RejectReason.INVALID_VOLUME
    assert rejected(entry(rig, Side.BUY, 10, 1, volume=1_000_000)) is (
        RejectReason.ORDER_VOLUME_CAP
    )
    rejected_unknown = rig.engine.place_trailing_entry("e9", "A", "NOPE", Side.BUY, 1, 1, 1, T0)
    assert rejected(rejected_unknown) is RejectReason.UNKNOWN_SYMBOL
    assert rejected(
        rig.engine.place_trailing_entry("e9", "Z", "EUR/USD", Side.BUY, 1, 1, 1, T0)
    ) is (RejectReason.UNKNOWN_ACCOUNT)
    entry(rig, Side.BUY, 10, 1)
    assert rejected(entry(rig, Side.BUY, 10, 1)) is RejectReason.DUPLICATE_ID
    assert rejected(rig.order("e1", Side.BUY)) is RejectReason.DUPLICATE_ID


def test_a_place_order_request_cannot_claim_the_trailing_type() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    request = OrderRequest("o1", "A", "EUR/USD", Side.BUY, OrderType.TRAILING, 100, 110_100)
    assert rejected(rig.engine.place_order(request, T0)) is RejectReason.INVALID_TRAILING


def test_entry_volume_counts_against_the_symbol_cap() -> None:
    rig = Rig()  # $100K: the caps apply as stated
    rig.price("EUR/USD", 110_000)
    spec = instruments()["EUR/USD"]
    order_cap, symbol_cap = spec.max_order_volume_per_100k, spec.max_symbol_volume_per_100k
    total = n = 0
    while total + order_cap <= symbol_cap:
        n += 1
        one(entry(rig, Side.BUY, 100, 1, volume=order_cap, oid=f"e{n}"), TrailingPlaced)
        total += order_cap
    over = symbol_cap - total + 1  # at most the order cap
    assert rejected(rig.order("o", Side.BUY, volume=over)) is RejectReason.SYMBOL_VOLUME_CAP
    assert rejected(entry(rig, Side.BUY, 100, 1, volume=over, oid="e0")) is (
        RejectReason.SYMBOL_VOLUME_CAP
    )
    rig.engine.cancel_order("A", "e1", T0)
    assert one(rig.order("o", Side.BUY, volume=over), PositionOpened).volume == over


def test_lot_caps_are_checked_again_at_the_trigger() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    entry(rig, Side.BUY, 100, 1, volume=100)
    lower = replace(
        instruments()["EUR/USD"], max_order_volume_per_100k=50, max_symbol_volume_per_100k=50
    )
    rig.engine.reconfigure("rsv-test-2", [lower], None, T0)
    cancelled = one(rig.price("EUR/USD", 110_200), OrderCancelled)
    assert (cancelled.order_id, cancelled.reason) == ("e1", RejectReason.ORDER_VOLUME_CAP)


def test_margin_is_checked_at_the_trigger_and_a_rejection_cancels_the_entry() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    entry(rig, Side.BUY, 100, 1, volume=100)  # placed with ample free margin
    rig.price("GBP/USD", 130_000)
    rig.order("g", Side.BUY, symbol="GBP/USD")  # 1 lot at 1.30005
    rig.price("GBP/USD", 10_000)  # bid 0.09995: a $120,010 floating loss
    assert rig.engine.free_margin("A") < 0
    cancelled = one(rig.price("EUR/USD", 110_200), OrderCancelled)
    assert (cancelled.order_id, cancelled.reason) == ("e1", RejectReason.INSUFFICIENT_MARGIN)
    assert working(rig) == {}
    assert [p.position_id for p in rig.engine.positions("A")] == ["g"]


def test_cancel_and_lock_remove_trailing_orders() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", 100, 1)
    entry(rig, Side.BUY, 100, 1)
    entry(rig, Side.SELL, 100, 1, oid="e2")
    assert rig.engine.cancel_order("A", "e2", T0) == [OrderCancelled(T0, "e2", "A", None)]
    assert rig.engine.pending_orders("A") == ()  # limit and stop orders only
    assert rig.engine.has_working_orders("A")
    events = rig.engine.lock_account("A", T0)
    assert {(e.order_id, e.reason) for e in events if isinstance(e, OrderCancelled)} == {
        ("t1", RejectReason.ACCOUNT_LOCKED),
        ("e1", RejectReason.ACCOUNT_LOCKED),
    }
    assert not rig.engine.has_working_orders("A")


def test_a_breach_close_out_cancels_the_positions_trailing_stop() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", 100, 1)
    events = rig.engine.close_all("A", T0, CloseReason.BREACH)
    assert one(events, PositionClosed).reason is CloseReason.BREACH
    assert one(events, OrderCancelled).reason is RejectReason.POSITION_CLOSED


# --- freezes and news blocks ----------------------------------------------------------


def test_a_frozen_symbol_neither_moves_nor_triggers_then_the_next_price_moves_first() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", distance=100, step=1)  # 109_895
    entry(rig, Side.BUY, distance=100, step=1)  # 110_105
    held = rig.price("EUR/USD", 109_000, at=T0 + 100, lag=2_001)  # delayed: not applied
    assert not [e for e in held if isinstance(e, TrailingMoved | PositionClosed | PositionOpened)]
    assert rejected(stop(rig, "p1", 50, 1, at=T0 + 200, oid="t2")) is RejectReason.SYMBOL_FROZEN
    assert rejected(entry(rig, Side.SELL, 50, 1, at=T0 + 200, oid="e2")) is (
        RejectReason.SYMBOL_FROZEN
    )
    events = rig.price("EUR/USD", 110_500, at=T0 + 3_000)  # fresh: bid 110_495, ask 110_505
    moved = {e.order_id: e.level for e in events if isinstance(e, TrailingMoved)}
    assert moved == {"t1": 110_395}  # the entry's level only moves down
    opened = one(events, PositionOpened)  # the entry: ask 110_505 >= 110_105, gap fill
    assert (opened.position_id, opened.price) == ("e1", 110_505)


def test_inside_a_news_block_levels_move_entries_are_held_and_stops_execute() -> None:
    block = NewsWindow(frozenset({"EUR/USD"}), T0 + 1_000, T0 + 2_000)
    rig = Rig(trade_blocks=[block])
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", distance=400, step=1)  # 109_595
    rig.price("EUR/USD", 110_000, at=T0 + 1_000)
    placed = entry(rig, Side.BUY, distance=100, step=1, at=T0 + 1_000)  # allowed inside
    assert one(placed, TrailingPlaced).level == 110_105
    moved = one(rig.price("EUR/USD", 109_700, at=T0 + 1_100), TrailingMoved)
    assert (moved.order_id, moved.level) == ("e1", 109_805)
    assert one(rig.price("EUR/USD", 109_590, at=T0 + 1_100), PositionClosed).reason is (
        CloseReason.TRAILING_STOP  # bid 109_585 reached 109_595: executes like a stop-loss
    )
    assert rig.price("EUR/USD", 110_000, at=T0 + 1_500) == []  # ask 110_005 past the level: held
    assert rig.engine.cancel_order("A", "e1", T0 + 1_600)[0] == OrderCancelled(
        T0 + 1_600, "e1", "A", None
    )
    entry(rig, Side.BUY, distance=100, step=1, at=T0 + 1_700, oid="e2")  # 110_105
    rig.price("EUR/USD", 110_200, at=T0 + 1_800)  # ask 110_205: reached, but held
    opened = one(rig.price("EUR/USD", 110_200, at=T0 + 2_001), PositionOpened)
    assert (opened.position_id, opened.price, opened.ts) == ("e2", 110_205, T0 + 2_001)


# --- state and records ----------------------------------------------------------------


def test_trailing_orders_survive_state_and_restore_through_the_codec() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    stop(rig, "p1", 100, 1)
    entry(rig, Side.SELL, 100, 3)
    rig.price("EUR/USD", 110_050, at=T0 + 10)
    state = rig.engine.state()
    decoded = CODEC.decode(CODEC.encode(state))
    assert decoded == state
    engine = TradingEngine(instruments(), Ledger(), config_version=VERSION)
    assert isinstance(decoded, type(state))
    engine.restore(decoded)
    assert engine.trailing_orders("A") == rig.engine.trailing_orders("A")
    assert [o.request.order_id for o in decoded.orders] == ["t1", "e1"]  # placement order


def test_events_and_inputs_round_trip_through_the_codec() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    events = [*stop(rig, "p1", 100, 1), *rig.price("EUR/USD", 110_100)]
    events += rig.price("EUR/USD", 109_000)
    for event in events:
        assert CODEC.decode(CODEC.encode(event)) == event
    assert OrderRejected in {type(e) for e in rig.order("p1", Side.BUY)}


# --- the rules supervisor -------------------------------------------------------------


def test_a_working_trailing_entry_keeps_an_account_from_passing_flat() -> None:
    from propfirm.rules.events import StagePassed
    from propfirm.rules.products import PRODUCTS
    from tests.rules_rig import RulesRig, ny

    week = [ny(2026, 10, d, 10) for d in (19, 20, 21, 22)]
    rig = RulesRig(PRODUCTS["lite"][0], 10_000_000, week[0])
    for day in week[:3]:
        rig.round_trip(1, day)
    rig.price(110_000, week[3])
    rig.sup.place_trailing_entry("e1", "A", "EUR/USD", Side.SELL, 10, 500, 1, week[3])
    events = rig.round_trip(700, week[3])  # target, 4 days: flat but for the entry
    assert not any(isinstance(e, StagePassed) for e in events)
    assert one(rig.sup.cancel_order("A", "e1", week[3] + 2_000), StagePassed).trading_days == 4


def test_a_trailing_fill_is_a_trading_day_and_entries_block_a_payout_debit() -> None:
    from propfirm.rules.products import PRODUCTS
    from tests.rules_rig import RulesRig, ny

    day = ny(2026, 10, 19, 10)
    rig = RulesRig(PRODUCTS["instant"][0], 2_500_000, day)
    rig.price(110_000, day)
    rig.sup.place_trailing_entry("e1", "A", "EUR/USD", Side.SELL, 10, 50, 1, day)
    assert rig.sup.payout_refusal("A", 1, "po-1") == "positions_open"
    assert one(rig.price(109_900, day + 1_000), PositionOpened).order_type is OrderType.TRAILING
    assert rig.sup.trading_days("A") == 1
    rig.sup.set_trailing_stop("t1", "A", "e1", 30, 1, day + 1_000)
    assert one(rig.price(109_990, day + 2_000), PositionClosed).reason is (
        CloseReason.TRAILING_STOP  # ask 109_995 >= 109_935 (ask 109_905 + 30)
    )
    assert rig.sup.payout_refusal("A", 1, "po-1") == "payout_exceeds_profit"
