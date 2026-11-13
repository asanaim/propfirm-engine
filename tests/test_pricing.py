"""Prices and discounts from the policy file, and the age check."""

from dataclasses import replace
from datetime import date
from fractions import Fraction

import pytest

from propfirm.access.gating import age_on
from propfirm.customers.pricing import DiscountKind, PricingError, discount_floor, price
from propfirm.policy import load_policy

POLICY = load_policy()
LITE = POLICY.product("lite")
INSTANT = POLICY.product("instant")
# the mechanism, on products that grant repeat discounts (the shipped policy
# grants none since owner decision K4 of 12 Nov 2026)
REPEATING = replace(LITE, repeat_discounts=(Fraction(10, 100), Fraction(15, 100)))
REPEATING_INSTANT = replace(INSTANT, repeat_discounts=(Fraction(10, 100), Fraction(10, 100)))
TEN = Fraction(1, 10)


def test_first_account_pays_list_price() -> None:
    quote = price(LITE, 1_000_000, prior_accounts=0)
    assert (quote.list_price_cents, quote.discount_kind, quote.price_cents) == (
        5_500,
        DiscountKind.NONE,
        5_500,
    )


def test_code_on_a_first_account() -> None:
    quote = price(LITE, 1_000_000, 0, TEN)
    assert (quote.discount_kind, quote.discount_cents, quote.price_cents) == (
        DiscountKind.CODE,
        550,
        4_950,
    )


@pytest.mark.parametrize(
    ("product", "prior", "code"),
    [
        (LITE, 0, Fraction(11, 100)),  # above the 10% code limit
        (LITE, 1, TEN),  # codes are for first accounts only
        (INSTANT, 0, Fraction(1, 100)),  # no codes on Instant
        (LITE, 0, Fraction(0)),
    ],
)
def test_codes_that_do_not_apply(product: object, prior: int, code: Fraction) -> None:
    with pytest.raises(PricingError):
        price(product, 1_000_000, prior, code)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("product", "prior", "share"),
    [
        (REPEATING, 1, Fraction(10, 100)),  # 2nd account
        (REPEATING, 2, Fraction(15, 100)),  # 3rd
        (REPEATING, 3, Fraction(0)),  # 4th: the record defines no discount
        (REPEATING_INSTANT, 1, Fraction(10, 100)),
        (REPEATING_INSTANT, 2, Fraction(10, 100)),
        (LITE, 1, Fraction(0)),  # K4: no repeat discount on sale
        (INSTANT, 2, Fraction(0)),
    ],
)
def test_repeat_account_discounts(product: object, prior: int, share: Fraction) -> None:
    quote = price(product, 1_000_000, prior)  # type: ignore[arg-type]
    assert quote.discount == share
    assert quote.discount_kind is (DiscountKind.REPEAT if share else DiscountKind.NONE)
    assert quote.price_cents == quote.list_price_cents - discount_floor(
        quote.list_price_cents, share
    )


def test_discount_rounds_down_to_the_cent() -> None:
    assert discount_floor(999, Fraction(15, 100)) == 149  # 149.85
    quote = price(REPEATING, 20_000_000, 2)  # 15% of $779.00 = $116.85 exactly
    assert (quote.discount_cents, quote.price_cents) == (11_685, 66_215)


def test_unknown_size_is_refused() -> None:
    with pytest.raises(PricingError):
        price(POLICY.product("classic"), 500_000, 0)  # no Classic 5K


@pytest.mark.parametrize(
    ("born", "today", "age"),
    [
        (date(2008, 10, 29), date(2026, 10, 29), 18),  # 18th birthday today
        (date(2008, 10, 30), date(2026, 10, 29), 17),  # tomorrow
        (date(2008, 2, 29), date(2026, 2, 28), 17),  # leap-day birthday, non-leap year
        (date(2008, 2, 29), date(2026, 3, 1), 18),
        (date(2008, 2, 29), date(2028, 2, 29), 20),
    ],
)
def test_age(born: date, today: date, age: int) -> None:
    assert age_on(born, today) == age


def test_no_product_on_sale_grants_a_repeat_discount() -> None:
    """Owner decision K4 (12 Nov 2026, review D7): a second account for a
    hedged pair is not sold cheaper."""
    assert all(p.repeat_discounts == () for p in POLICY.products)
