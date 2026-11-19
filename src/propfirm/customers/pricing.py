"""What a customer pays, from the policy file (CC-2).

- A discount code applies to the customer's first account of a product
  only, and never above the product's `code_discount_max_percent` (0 on
  Instant, so no codes there).
- Repeat-account discounts apply to the 2nd, 3rd ... account of the same
  product, one listed percentage per account; past the list, none
  [DEFAULT: the record defines the 2nd and 3rd only].
- No code is valid on the product's `code_free_sizes_cents` (D-13.17:
  Lite 10K).
- Retake (D-13.6): after a breach, one new account of the same product and
  size at `retake_discount_percent` off; checkout decides eligibility and
  passes `retake=True`. It replaces a repeat discount, never adds to one.
- None of these combine: codes are for first accounts, repeat and retake
  discounts for later ones, and a retake takes the place of a repeat.
- Rounding: the discount is rounded down to the cent (`discount_floor`),
  so a customer never gets more than the stated percentage.
- Add-ons (D-13.18): each adds its share of the list price, rounded down
  to the cent; a discount is taken off the list price only, never off an
  add-on (D-14.12).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from propfirm.policy import AddonOffer, ProductPolicy
from propfirm.rules.addons import ordered


class PricingError(ValueError):
    """The requested purchase cannot be priced (wrong size, code not allowed)."""


class DiscountKind(StrEnum):
    NONE = "none"
    CODE = "code"
    REPEAT = "repeat"
    RETAKE = "retake"
    TRIAL = "trial"  # the free trial: the list price, all of it off (D-13.17)


@dataclass(frozen=True, slots=True)
class PriceQuote:
    list_price_cents: int
    discount_kind: DiscountKind
    discount: Fraction  # share of the list price
    discount_cents: int
    price_cents: int  # list price less the discount, plus the add-ons
    addons: tuple[str, ...] = ()  # in id order
    addon_cents: int = 0


def discount_floor(list_price_cents: int, share: Fraction) -> int:
    """`share` of the price in whole cents, rounded down."""
    return math.floor(list_price_cents * share)


def price(
    product: ProductPolicy,
    size_cents: int,
    prior_accounts: int,
    code_discount: Fraction | None = None,
    retake: bool = False,
    addons: Sequence[AddonOffer] = (),
) -> PriceQuote:
    """Price one account of `size_cents`. `prior_accounts` counts the
    customer's earlier paid accounts of this product; `code_discount` is the
    share a valid code offers, if a code was given; `retake`: the customer
    holds an unused breach of this product and size; `addons`: the add-ons
    chosen, each offered with this product."""
    prices = dict(product.prices_cents)
    if size_cents not in prices:
        raise PricingError(f"{product.name} is not sold in size {size_cents} cents")
    if prior_accounts < 0:
        raise ValueError("prior_accounts must be >= 0")
    list_price = prices[size_cents]
    kind, share = DiscountKind.NONE, Fraction(0)
    if code_discount is not None:
        if prior_accounts > 0:
            raise PricingError("discount codes apply to a first account only")
        if not 0 < code_discount <= product.code_discount_max:
            raise PricingError(f"this code cannot be used on {product.name}")
        if size_cents in product.code_free_sizes:
            raise PricingError("discount codes cannot be used on this account size")
        kind, share = DiscountKind.CODE, code_discount
    elif retake and product.retake_discount > 0:
        if prior_accounts < 1:
            raise ValueError("a retake follows an earlier account")
        kind, share = DiscountKind.RETAKE, product.retake_discount
    elif 1 <= prior_accounts <= len(product.repeat_discounts):
        kind, share = DiscountKind.REPEAT, product.repeat_discounts[prior_accounts - 1]
    off = discount_floor(list_price, share)
    for offer in addons:
        if product.id not in offer.products:
            raise PricingError(f"{offer.id} is not sold with {product.name}")
    chosen = ordered([o.id for o in addons])
    extra = sum(discount_floor(list_price, o.price) for o in addons)
    return PriceQuote(list_price, kind, share, off, list_price - off + extra, chosen, extra)
