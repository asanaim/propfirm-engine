from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from propfirm.market.spread import (
    NewsWindow,
    SpreadConfig,
    SpreadModel,
    in_news,
    round_up_to_even,
)
from propfirm.money import cents_ceil, cents_half_even, points_to_price
from propfirm.trading.instruments import (
    LEVERAGE,
    conversion_route,
    default_instruments,
    quote_currency,
    scaled_cap,
)


@pytest.mark.parametrize(
    ("usd", "cents"),
    [
        (Fraction(1, 200), 0),  # 0.5 cent -> 0 (even)
        (Fraction(3, 200), 2),  # 1.5 cents -> 2 (even)
        (Fraction(-1, 200), 0),
        (Fraction(-3, 200), -2),
        (Fraction(1001, 100_000), 1),  # 1.001 cents
    ],
)
def test_half_even(usd: Fraction, cents: int) -> None:
    assert cents_half_even(usd) == cents


def test_ceil_never_understates() -> None:
    assert cents_ceil(Fraction(1, 1000)) == 1
    assert cents_ceil(Fraction(1, 100)) == 1
    assert cents_ceil(Fraction(0)) == 0


def test_points_to_price_is_exact() -> None:
    assert points_to_price(110_123, 5) == Fraction(110_123, 100_000)


def test_spread_starts_at_minimum_and_widens_with_volatility() -> None:
    model = SpreadModel(SpreadConfig(min_points=6, window=4))
    model.observe(1_000)
    assert model.spread_points(news=False) == 6
    for price in (1_020, 1_000, 1_020, 1_000):  # mean |change| = 20
        model.observe(price)
    assert model.spread_points(news=False) == 20
    assert model.spread_points(news=True) == 60


def test_volatility_window_forgets_old_moves() -> None:
    model = SpreadModel(SpreadConfig(min_points=6, window=2))
    for price in (1_000, 1_100, 1_101, 1_102):
        model.observe(price)
    assert model.spread_points(news=False) == 6  # only the last two 1-point moves


def test_odd_spread_rounds_up_to_even_so_mid_is_the_feed_price() -> None:
    model = SpreadModel(SpreadConfig(min_points=5))
    q = model.quote("EUR/USD", 110_000, seq=1, source_ts=0, receive_ts=0, news=False)
    assert (q.bid, q.ask, q.mid, q.spread) == (109_997, 110_003, 110_000, 6)
    assert (q.bid + q.ask) == 2 * q.mid


def test_news_widening_stays_even() -> None:
    model = SpreadModel(SpreadConfig(min_points=5, news_multiplier=3))
    assert model.spread_points(news=True) == 16  # 5 * 3 = 15 -> 16


def test_bid_never_below_one_point() -> None:
    model = SpreadModel(SpreadConfig(min_points=10))
    q = model.quote("XRP/USD", 2, seq=1, source_ts=0, receive_ts=0, news=False)
    assert q.bid == 1 and q.spread == 10


def test_round_up_to_even() -> None:
    assert [round_up_to_even(n) for n in (0, 1, 2, 3, 10, 11)] == [0, 2, 2, 4, 10, 12]


def test_news_window_covers_half_open_interval() -> None:
    w = NewsWindow(frozenset({"EUR/USD"}), 100, 200)
    assert not w.covers("EUR/USD", 99)
    assert w.covers("EUR/USD", 100) and w.covers("EUR/USD", 199)
    assert not w.covers("EUR/USD", 200) and not w.covers("XAU/USD", 150)
    assert in_news([w], "EUR/USD", 150) and not in_news([], "EUR/USD", 150)
    with pytest.raises(ValueError):
        NewsWindow(frozenset({"EUR/USD"}), 200, 200)


@given(st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=50), st.booleans())
def test_quote_invariants(prices: list[int], news: bool) -> None:
    model = SpreadModel(SpreadConfig(min_points=3, window=7, vol_multiplier=Fraction(3, 2)))
    for p in prices:
        model.observe(p)
    q = model.quote("S", prices[-1], seq=1, source_ts=0, receive_ts=0, news=news)
    assert 1 <= q.bid < q.ask
    assert q.spread >= 3 and q.spread % 2 == 0
    assert q.bid + q.ask == 2 * q.mid or q.bid == 1


def test_spread_config_validation() -> None:
    with pytest.raises(ValueError):
        SpreadConfig(min_points=0)
    with pytest.raises(ValueError):
        SpreadConfig(min_points=1, vol_multiplier=Fraction(-1))


def test_instrument_defaults_cover_all_launch_symbols() -> None:
    instruments = default_instruments()
    assert len(instruments) == 30
    assert instruments["EUR/USD"].contract_size == 100_000
    assert instruments["XAU/USD"].contract_size == 100
    assert instruments["XAG/USD"].contract_size == 5_000
    assert instruments["WTI"].contract_size == 1_000
    assert instruments["BTC/USD"].contract_size == 1
    assert instruments["WTI"].leverage == 20
    assert instruments["BTC/USD"].leverage == Fraction(333, 100)
    assert LEVERAGE  # every asset class has a leverage
    assert quote_currency("WTI") == "USD"
    assert quote_currency("EUR/JPY") == "JPY"


def test_every_quote_currency_has_a_usd_route() -> None:
    instruments = default_instruments()
    routes = {spec.quote_currency for spec in instruments.values()} - {"USD"}
    assert routes == {"JPY", "CHF", "CAD", "GBP", "AUD", "NZD"}
    assert conversion_route("JPY", instruments) == ("USD/JPY", False)
    assert conversion_route("GBP", instruments) == ("GBP/USD", True)
    with pytest.raises(ValueError):
        conversion_route("SEK", instruments)


def test_caps_scale_with_account_size_and_round_down() -> None:
    assert scaled_cap(5_000, 10_000_000) == 5_000  # $100,000 account
    assert scaled_cap(5_000, 1_000_000) == 500  # $10,000
    assert scaled_cap(5_000, 500_000) == 250  # $5,000
    assert scaled_cap(5_000, 20_000_000) == 10_000  # $200,000
    assert scaled_cap(3, 1_000_000) == 0  # 0.3 rounds down to 0
