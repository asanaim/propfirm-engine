"""Order engine: fills, triggers, stops, P&L, margin, caps and rejections."""

import pytest

from propfirm.ledger import Ledger
from propfirm.market.guards import FreezeReason
from propfirm.market.spread import NewsWindow
from propfirm.market.symbols import SYMBOLS
from propfirm.market.types import PriceUpdate
from propfirm.trading.engine import FIRM_TRADING_PNL, TradingEngine
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    DuplicateUpdate,
    FeedGap,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
    StopsModified,
    SymbolFrozen,
    SymbolResumed,
)
from propfirm.trading.instruments import default_instrument
from propfirm.trading.orders import OrderType, Side
from tests.engine_rig import T0, USD_100K, VERSION, Rig, instruments, one, rejected

# --- market fills --------------------------------------------------------


def test_market_buy_fills_at_ask_and_records_the_update() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000, at=T0, lag=150)
    opened = one(rig.order("p1", Side.BUY, at=T0 + 200), PositionOpened)
    assert opened.price == 110_005  # ask = 110_000 + 5
    assert (opened.price_seq, opened.price_source_ts, opened.price_receive_ts) == (
        1,
        T0,
        T0 + 150,
    )
    assert opened.ts == T0 + 200  # fill time
    assert opened.margin_cents == 110_005  # $110,005 notional / 100
    assert opened.config_version == VERSION


def test_market_sell_fills_at_bid() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    assert one(rig.order("p1", Side.SELL), PositionOpened).price == 109_995


def test_open_account_posts_funding_to_the_ledger() -> None:
    ledger = Ledger()
    engine = TradingEngine(instruments(), ledger, config_version=VERSION)
    events = engine.open_account("Z", 2_500_000, T0)
    assert one(events, AccountOpened).initial_balance_cents == 2_500_000
    assert engine.balance("Z") == engine.equity("Z") == 2_500_000
    ledger.verify()
    with pytest.raises(ValueError):
        engine.open_account("Z", 1, T0)


# --- P&L and conversion ----------------------------------------------------


def test_usd_quoted_pnl_posts_to_ledger() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)  # 1 lot at 1.10005
    rig.price("EUR/USD", 110_200)  # bid 1.10195
    assert rig.engine.unrealized_pnl("A") == 19_000  # 190 points * $1 = $190
    closed = one(rig.engine.close_position("A", "p1", T0 + 1), PositionClosed)
    assert (closed.close_price, closed.pnl_cents, closed.reason) == (110_195, 19_000, "client")
    assert rig.engine.balance("A") == USD_100K + 19_000
    assert rig.ledger.balance(FIRM_TRADING_PNL) == -19_000
    rig.ledger.verify()


def test_loss_posts_the_other_way() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    rig.price("EUR/USD", 109_900)  # bid 1.09895
    closed = one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    assert closed.pnl_cents == -11_000
    assert rig.engine.balance("A") == USD_100K - 11_000
    rig.ledger.verify()


def test_jpy_pnl_converts_at_usd_jpy_mid_half_even() -> None:
    rig = Rig()
    rig.price("USD/JPY", 150_000)  # 150.000
    rig.order("p1", Side.BUY, symbol="USD/JPY")  # ask 150.005
    rig.price("USD/JPY", 150_500)  # bid 150.495, mid 150.500
    closed = one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    # 0.490 JPY * 100,000 = 49,000 JPY / 150.5 = $325.5813... -> 32,558 cents
    assert closed.pnl_cents == 32_558


def test_cross_pnl_converts_through_gbp_usd() -> None:
    rig = Rig()
    rig.price("GBP/USD", 130_000)
    rig.price("EUR/GBP", 85_000)
    rig.order("p1", Side.BUY, symbol="EUR/GBP")  # ask 0.85005
    rig.price("EUR/GBP", 85_105)  # bid 0.85100
    closed = one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    assert closed.pnl_cents == 12_350  # 95 GBP * 1.30 = $123.50


def test_jpy_margin_rounds_up_to_the_cent() -> None:
    rig = Rig()
    rig.price("USD/JPY", 150_000)
    opened = one(rig.order("p1", Side.BUY, symbol="USD/JPY"), PositionOpened)
    # 15,000,500 JPY / 150.000 = $100,003.333... / 100 = $1,000.0333 -> 100,004 cents
    assert opened.margin_cents == 100_004


def test_crypto_margin_uses_1_to_3_33() -> None:
    rig = Rig()
    rig.price("BTC/USD", 10_000_000)  # $100,000.00
    opened = one(rig.order("p1", Side.BUY, symbol="BTC/USD"), PositionOpened)
    # $100,000.05 / 3.33 = $30,030.045045... -> 3,003,005 cents
    assert opened.margin_cents == 3_003_005


def test_gold_contract_is_100_ounces() -> None:
    rig = Rig()
    rig.price("XAU/USD", 330_000)  # $3,300.00
    rig.order("p1", Side.SELL, symbol="XAU/USD")  # bid 3,299.95
    rig.price("XAU/USD", 329_000)  # ask 3,290.05
    closed = one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    assert closed.pnl_cents == 99_000  # $9.90 * 100 oz


def test_partial_close_releases_margin_pro_rata() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    assert rig.engine.used_margin("A") == 110_005
    rig.price("EUR/USD", 110_100)
    first = one(rig.engine.close_position("A", "p1", T0, volume=40), PositionClosed)
    assert (first.close_id, first.volume, first.remaining_volume) == ("p1/c1", 40, 60)
    assert first.pnl_cents == 3_600  # 90 points * 0.4 lot
    assert rig.engine.used_margin("A") == 66_003  # $1,100.05 * 0.6 = $660.03
    second = one(rig.engine.close_position("A", "p1", T0), PositionClosed)
    assert (second.close_id, second.volume, second.remaining_volume) == ("p1/c2", 60, 0)
    assert rig.engine.positions("A") == () and rig.engine.used_margin("A") == 0
    rig.ledger.verify()


@pytest.mark.parametrize("volume", [0, 101, -1])
def test_partial_close_volume_must_be_within_position(volume: int) -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    events = rig.engine.close_position("A", "p1", T0, volume=volume)
    assert rejected(events) is RejectReason.INVALID_VOLUME


def test_equity_is_balance_plus_unrealized() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.price("USD/JPY", 150_000)
    rig.order("p1", Side.BUY)
    rig.order("p2", Side.SELL, symbol="USD/JPY")
    rig.price("EUR/USD", 110_050)
    rig.price("USD/JPY", 149_000)
    e = rig.engine
    assert e.equity("A") == e.balance("A") + e.unrealized_pnl("A")
    assert e.free_margin("A") == e.equity("A") - e.used_margin("A")


# --- pending orders ----------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "order_type", "level", "quiet", "crossing", "fill"),
    [
        (Side.BUY, OrderType.LIMIT, 109_900, 109_950, 109_800, 109_805),  # ask <= level
        (Side.BUY, OrderType.STOP, 110_100, 110_090, 110_300, 110_305),  # ask >= level
        (Side.SELL, OrderType.LIMIT, 110_100, 110_100, 110_300, 110_295),  # bid >= level
        (Side.SELL, OrderType.STOP, 109_900, 109_910, 109_700, 109_695),  # bid <= level
    ],
)
def test_pending_order_triggers_on_its_side_and_fills_at_the_crossing_price(
    side: Side, order_type: OrderType, level: int, quiet: int, crossing: int, fill: int
) -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    placed = one(rig.order("o1", side, order_type=order_type, level=level), OrderPlaced)
    assert placed.level == level
    assert not [e for e in rig.price("EUR/USD", quiet) if isinstance(e, PositionOpened)]
    opened = one(rig.price("EUR/USD", crossing, at=T0 + 5), PositionOpened)
    assert (opened.price, opened.order_type, opened.ts) == (fill, order_type, T0 + 5)
    assert rig.engine.pending_orders("A") == ()


@pytest.mark.parametrize(
    ("side", "order_type", "bad", "good"),
    [
        (Side.BUY, OrderType.LIMIT, 110_005, 110_004),  # must be below ask
        (Side.BUY, OrderType.STOP, 110_005, 110_006),  # must be above ask
        (Side.SELL, OrderType.LIMIT, 109_995, 109_996),  # must be above bid
        (Side.SELL, OrderType.STOP, 109_995, 109_994),  # must be below bid
    ],
)
def test_pending_level_must_not_trigger_immediately(
    side: Side, order_type: OrderType, bad: int, good: int
) -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    assert rejected(rig.order("o1", side, order_type=order_type, level=bad)) is (
        RejectReason.INVALID_LEVEL
    )
    one(rig.order("o2", side, order_type=order_type, level=good), OrderPlaced)


def test_pending_order_cancelled_at_trigger_when_margin_is_short() -> None:
    rig = Rig(balance=100_000)  # $1,000: crypto cap 0.1 coin, margin $3,003
    rig.price("BTC/USD", 10_000_000)
    rig.order(
        "o1", Side.BUY, volume=10, symbol="BTC/USD", order_type=OrderType.STOP, level=10_000_100
    )
    cancelled = one(rig.price("BTC/USD", 10_000_200), OrderCancelled)
    assert cancelled.reason is RejectReason.INSUFFICIENT_MARGIN


def test_cancel_pending_order() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("o1", Side.BUY, order_type=OrderType.LIMIT, level=109_000)
    cancelled = one(rig.engine.cancel_order("A", "o1", T0), OrderCancelled)
    assert cancelled.reason is None
    assert rejected(rig.engine.cancel_order("A", "o1", T0)) is RejectReason.UNKNOWN_ORDER


# --- stop-loss and take-profit --------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "sl", "tp", "quiet", "crossing", "close", "reason"),
    [
        (Side.BUY, 109_900, None, 109_950, 109_800, 109_795, CloseReason.STOP_LOSS),
        (Side.BUY, None, 110_100, 110_100, 110_200, 110_195, CloseReason.TAKE_PROFIT),
        (Side.SELL, 110_100, None, 110_090, 110_200, 110_205, CloseReason.STOP_LOSS),
        (Side.SELL, None, 109_900, 109_900, 109_800, 109_805, CloseReason.TAKE_PROFIT),
    ],
)
def test_stops_close_at_the_crossing_exit_price(
    side: Side,
    sl: int | None,
    tp: int | None,
    quiet: int,
    crossing: int,
    close: int,
    reason: CloseReason,
) -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", side, sl=sl, tp=tp)
    assert not [e for e in rig.price("EUR/USD", quiet) if isinstance(e, PositionClosed)]
    closed = one(rig.price("EUR/USD", crossing), PositionClosed)
    assert (closed.close_price, closed.reason) == (close, reason)
    rig.ledger.verify()


@pytest.mark.parametrize(
    ("side", "sl", "tp", "ok"),
    [
        (Side.BUY, 109_995, None, False),  # SL must be below bid
        (Side.BUY, 109_994, None, True),
        (Side.BUY, None, 109_995, False),  # TP must be above bid
        (Side.BUY, None, 109_996, True),
        (Side.SELL, 110_005, None, False),  # SL must be above ask
        (Side.SELL, 110_006, None, True),
        (Side.SELL, None, 110_005, False),  # TP must be below ask
        (Side.SELL, None, 110_004, True),
    ],
)
def test_market_order_stops_validated_against_exit_price(
    side: Side, sl: int | None, tp: int | None, ok: bool
) -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = rig.order("p1", side, sl=sl, tp=tp)
    if ok:
        one(events, PositionOpened)
    else:
        assert rejected(events) is RejectReason.INVALID_STOPS


def test_pending_order_stops_validated_against_level() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    bad = rig.order("o1", Side.BUY, order_type=OrderType.LIMIT, level=109_000, sl=109_000)
    assert rejected(bad) is RejectReason.INVALID_STOPS
    good = rig.order(
        "o2", Side.BUY, order_type=OrderType.LIMIT, level=109_000, sl=108_999, tp=109_001
    )
    one(good, OrderPlaced)


def test_modify_stops() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    rig.order("p1", Side.BUY)
    modified = one(rig.engine.modify_stops("A", "p1", 109_000, 111_000, T0), StopsModified)
    assert (modified.stop_loss, modified.take_profit) == (109_000, 111_000)
    assert rejected(rig.engine.modify_stops("A", "p1", 110_000, None, T0)) is (
        RejectReason.INVALID_STOPS
    )
    one(rig.engine.modify_stops("A", "p1", None, None, T0), StopsModified)
    assert rig.engine.positions("A")[0].stop_loss is None


# --- caps and margin ------------------------------------------------------------------


def test_order_volume_cap_scales_with_account() -> None:
    big = Rig()  # $100,000: forex cap 50 lots per order
    big.price("EUR/USD", 110_000)
    assert rejected(
        big.order("o1", Side.BUY, volume=5_001, order_type=OrderType.LIMIT, level=100_000)
    ) is (RejectReason.ORDER_VOLUME_CAP)
    one(
        big.order("o2", Side.BUY, volume=5_000, order_type=OrderType.LIMIT, level=100_000),
        OrderPlaced,
    )
    small = Rig(balance=1_000_000)  # $10,000: 5 lots per order
    small.price("EUR/USD", 110_000)
    assert rejected(small.order("o1", Side.BUY, volume=501)) is RejectReason.ORDER_VOLUME_CAP


def test_symbol_cap_counts_open_and_pending_volume() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    for i in range(2):
        one(
            rig.order(f"o{i}", Side.BUY, volume=5_000, order_type=OrderType.LIMIT, level=100_000),
            OrderPlaced,
        )
    events = rig.order("o9", Side.SELL, volume=1)
    assert rejected(events) is RejectReason.SYMBOL_VOLUME_CAP


def test_insufficient_margin() -> None:
    rig = Rig(balance=100_000)  # $1,000
    rig.price("BTC/USD", 10_000_000)
    events = rig.order("p1", Side.BUY, volume=10, symbol="BTC/USD")  # needs $3,003
    assert rejected(events) is RejectReason.INSUFFICIENT_MARGIN
    assert rig.engine.positions("A") == ()


# --- rejections ------------------------------------------------------------------------


def test_rejections_name_the_reason_and_version() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    r = one(rig.order("p1", Side.BUY, account="nobody"), OrderRejected)
    assert (r.reason, r.config_version, r.ref) == (RejectReason.UNKNOWN_ACCOUNT, VERSION, "p1")
    assert rejected(rig.order("p2", Side.BUY, symbol="WTI")) is RejectReason.UNKNOWN_SYMBOL
    assert rejected(rig.order("p3", Side.BUY, volume=0)) is RejectReason.INVALID_VOLUME
    one(rig.order("p4", Side.BUY), PositionOpened)
    assert rejected(rig.order("p4", Side.BUY)) is RejectReason.DUPLICATE_ID
    assert rejected(rig.engine.close_position("A", "nope", T0)) is RejectReason.UNKNOWN_POSITION
    rig.engine.open_account("B", USD_100K, T0)
    assert rejected(rig.engine.close_position("B", "p4", T0)) is RejectReason.UNKNOWN_POSITION


def test_no_conversion_rate_until_the_rate_symbol_is_priced() -> None:
    rig = Rig()
    rig.price("EUR/GBP", 85_000)  # GBP/USD never priced
    assert rejected(rig.order("p1", Side.BUY, symbol="EUR/GBP")) is RejectReason.NO_CONVERSION_RATE


def test_rejected_market_order_id_can_be_reused() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    assert rejected(rig.order("p1", Side.BUY, volume=0)) is RejectReason.INVALID_VOLUME
    one(rig.order("p1", Side.BUY), PositionOpened)


def test_engine_needs_a_conversion_route_for_every_quote_currency() -> None:
    only_cross = {"EUR/JPY": default_instrument(SYMBOLS["EUR/JPY"])}
    with pytest.raises(ValueError):
        TradingEngine(only_cross, Ledger(), config_version=VERSION)


# --- feed anomalies and spread widening ---------------------------------------------


def test_duplicate_update_is_ignored_and_logged() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = rig.engine.on_price(PriceUpdate("EUR/USD", 1, T0, T0, 120_000))
    assert one(events, DuplicateUpdate).seq == 1
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None and quote.mid == 110_000


def test_lost_updates_are_logged_and_the_next_price_applied() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    events = rig.engine.on_price(PriceUpdate("EUR/USD", 5, T0 + 1, T0 + 1, 110_050))
    gap = one(events, FeedGap)
    assert (gap.missing_from, gap.missing_to) == (2, 4)
    quote = rig.engine.quote("EUR/USD")
    assert quote is not None and quote.mid == 110_050


def test_spread_widens_inside_a_news_window() -> None:
    window = NewsWindow(frozenset({"EUR/USD"}), T0 + 1_000, T0 + 2_000)
    rig = Rig(news_windows=[window])
    rig.price("EUR/USD", 110_000, at=T0)
    q = rig.engine.quote("EUR/USD")
    assert q is not None and q.spread == 10
    rig.price("EUR/USD", 110_000, at=T0 + 1_500)
    q = rig.engine.quote("EUR/USD")
    assert q is not None and q.spread == 30  # min 10 * news multiplier 3


def test_unconfigured_feed_symbol_is_an_error() -> None:
    rig = Rig()
    with pytest.raises(KeyError):
        rig.price("WTI", 6_500)


def test_frozen_reason_is_carried_on_events() -> None:
    rig = Rig()
    rig.price("EUR/USD", 110_000)
    frozen = one(rig.price("EUR/USD", 110_000, at=T0 + 100, lag=2_001), SymbolFrozen)
    assert frozen.reason is FreezeReason.DELAYED
    assert one(rig.price("EUR/USD", 110_000, at=T0 + 3_000), SymbolResumed).symbol == "EUR/USD"
