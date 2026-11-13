"""Overnight swaps (build plan step 1, line 3; K6, D-13.19).

charge = position value x annual rate x nights / 360, at each 17:00 New
York reset, rounded half-even to the cent, never credited; Wednesday's reset
carries 3 nights for forex, metals and oil, weekends none; crypto every
night.
"""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.journal.machine import CODEC
from propfirm.ledger import Ledger
from propfirm.policy import PolicyError, load_policy, parse_policy
from propfirm.rules.clock import NEW_YORK_17
from propfirm.rules.products import PRODUCTS
from propfirm.trading.engine import TradingEngine, balance_account
from propfirm.trading.events import SwapCharged
from propfirm.trading.orders import Side
from propfirm.trading.swaps import (
    EVERY_NIGHT,
    WEEKDAYS_TRIPLE_WEDNESDAY,
    SwapSettings,
    SwapTerms,
    swap_terms,
)
from tests.engine_rig import instruments
from tests.rules_rig import RulesRig, ny
from tests.test_policy import edited

SIZE = 10_000_000  # $100,000
LITE = replace(PRODUCTS["lite"][0], rsv_id="lite-swaps-test")
EUR_LONG = SwapTerms(Fraction(25, 1000), Fraction(0), WEEKDAYS_TRIPLE_WEDNESDAY)  # 2.5 % long
MONDAY = ny(2026, 11, 16, 10)


def _p(text: str) -> Fraction:
    return Fraction(text) / 100


# --- the formula --------------------------------------------------------------------


def test_shipped_rates_give_the_product_lines_swaps() -> None:
    swaps = {i.name: i.swap for i in load_policy().trading.instruments}
    assert all(s is not None for s in swaps.values())
    eur, jpy, aud = swaps["EUR/USD"], swaps["USD/JPY"], swaps["AUD/USD"]
    assert eur is not None and (eur.long_rate, eur.short_rate) == (
        _p("2.5"),
        Fraction(0),
    )  # 4 - 2.5 + 1
    assert jpy is not None and (jpy.long_rate, jpy.short_rate) == (
        Fraction(0),
        _p("3.75"),
    )  # 4 - 1.25 + 1
    assert aud is not None and (aud.long_rate, aud.short_rate) == (_p("0.15"), _p("1.85"))
    gold, oil, btc = swaps["XAU/USD"], swaps["WTI"], swaps["BTC/USD"]
    assert gold is not None and (gold.long_rate, gold.short_rate) == (_p("5"), Fraction(0))
    assert oil is not None and (oil.long_rate, oil.short_rate) == (_p("4"), Fraction(0))
    assert btc is not None and (btc.long_rate, btc.short_rate, btc.nights) == (
        _p("15"),
        _p("15"),
        EVERY_NIGHT,
    )
    assert eur.nights == gold.nights == oil.nights == (1, 1, 3, 1, 1, 0, 0)


def test_a_side_whose_differential_beats_the_markup_pays_nothing_and_earns_nothing() -> None:
    settings = SwapSettings(
        rates={c: Fraction(0) for c in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")}
        | {"AUD": _p("5")},
        markup=_p("1"),
        metal_markup=_p("1"),
        oil_long=_p("4"),
        crypto=_p("15"),
    )
    terms = swap_terms(load_policy().trading.instruments[4].symbol, settings)  # AUD/USD
    assert (terms.long_rate, terms.short_rate) == (Fraction(0), _p("6"))


def test_terms_and_settings_are_checked() -> None:
    with pytest.raises(ValueError):
        SwapTerms(Fraction(-1, 100), Fraction(0), EVERY_NIGHT)
    with pytest.raises(ValueError):
        SwapTerms(Fraction(0), Fraction(0), (1, 1, 1))
    with pytest.raises(ValueError):
        SwapTerms(Fraction(0), Fraction(0), (1, 1, 1, 1, 1, 1, 8))
    with pytest.raises(ValueError, match="missing"):
        SwapSettings({"USD": Fraction(0)}, Fraction(0), Fraction(0), Fraction(0), Fraction(0))


# --- the charge -----------------------------------------------------------------------


def long_eur(swap: SwapTerms = EUR_LONG) -> RulesRig:
    rig = RulesRig(LITE, SIZE, MONDAY, swap=swap)
    rig.price(110_000, MONDAY)  # 1.10000
    rig.order(Side.BUY, 100, MONDAY)  # 1 lot: $110,000
    return rig


def swaps_of(events: list[Any]) -> list[SwapCharged]:
    return [e for e in events if isinstance(e, SwapCharged)]


def test_each_reset_charges_and_wednesday_carries_three_nights() -> None:
    rig = long_eur()
    balance = rig.engine.balance("A")
    events = rig.price(110_000, ny(2026, 11, 18, 17, 0, 1))  # past Mon, Tue and Wed resets
    charged = swaps_of(events)
    # $110,000 x 2.5 % / 360 = $7.6389 a night
    assert [(e.nights, e.swap_cents) for e in charged] == [(1, 764), (1, 764), (3, 2_292)]
    assert [e.ts for e in charged] == [
        ny(2026, 11, 16, 17),
        ny(2026, 11, 17, 17),
        ny(2026, 11, 18, 17),
    ]
    assert charged[0].value_cents == 11_000_000
    assert rig.engine.balance("A") == balance - 3_820
    assert rig.ledger.balance(balance_account("A")) == balance - 3_820
    assert rig.ledger.balance("firm:swap") == 3_820  # the firm keeps it


def test_weekend_resets_charge_forex_nothing_and_crypto_every_night() -> None:
    rig = long_eur()
    weekend = ny(2026, 11, 22, 17, 0, 1)  # past Thu, Fri, Sat and Sun resets too
    nights = [e.nights for e in swaps_of(rig.price(110_000, weekend))]
    assert nights == [1, 1, 3, 1, 1]  # Mon to Fri; Saturday and Sunday carry none
    crypto = long_eur(SwapTerms(_p("15"), _p("15"), EVERY_NIGHT))
    assert [e.nights for e in swaps_of(crypto.price(110_000, weekend))] == [1] * 7


def test_the_free_side_pays_nothing() -> None:
    rig = RulesRig(LITE, SIZE, MONDAY, swap=EUR_LONG)
    rig.price(110_000, MONDAY)
    rig.order(Side.SELL, 100, MONDAY)
    assert swaps_of(rig.price(110_000, ny(2026, 11, 18, 17, 0, 1))) == []


def test_a_swap_counts_in_the_day_that_closes_and_the_new_base_follows_it() -> None:
    rig = long_eur()
    rig.price(110_000, ny(2026, 11, 16, 17, 0, 1))  # Monday's reset only
    (day, pnl), *_ = rig.sup.day_pnl("A")
    assert (day, pnl) == (NEW_YORK_17.day_start(MONDAY), -764)
    daily_floor, _ = rig.sup.floors("A")
    equity = rig.engine.equity("A")
    assert daily_floor == max(rig.engine.balance("A"), equity) - SIZE * 3 // 100


def test_instruments_without_terms_charge_nothing() -> None:
    rig = RulesRig(LITE, SIZE, MONDAY)  # the older setups: no swap terms
    rig.price(110_000, MONDAY)
    rig.order(Side.BUY, 100, MONDAY)
    assert swaps_of(rig.price(110_000, ny(2026, 11, 18, 17, 0, 1))) == []


def test_the_engine_refuses_a_bad_weekday() -> None:
    engine = TradingEngine(instruments(), Ledger(), config_version="t")
    with pytest.raises(ValueError):
        engine.rollover(MONDAY, 7)


# --- records ---------------------------------------------------------------------------


def test_terms_round_trip_and_older_instruments_keep_their_encoding() -> None:
    spec = instruments()["EUR/USD"]
    assert "swap" not in CODEC.encode(spec)
    with_swap = replace(spec, swap=EUR_LONG)
    assert CODEC.decode(CODEC.encode(with_swap)) == with_swap
    event = SwapCharged(1, "p", "A", "EUR/USD", 3, 11_000_000, 2_292, "v")
    assert CODEC.decode(CODEC.encode(event)) == event


# --- policy file -------------------------------------------------------------------------


def test_a_policy_without_swaps_charges_none() -> None:
    policy = parse_policy(edited(lambda d: d["trading"].pop("swaps")))
    assert all(i.swap is None for i in policy.trading.instruments)


def test_a_missing_rate_is_refused() -> None:
    with pytest.raises(PolicyError, match="NZD"):
        parse_policy(edited(lambda d: d["trading"]["swaps"]["rates_percent"].pop("NZD")))


def test_a_rate_is_written_as_percentage_text() -> None:
    def change(d: dict[str, Any]) -> None:
        d["trading"]["swaps"]["crypto_percent"] = 15

    with pytest.raises(PolicyError, match="crypto_percent"):
        parse_policy(edited(change))
