"""Named, exact rounding from rational US dollar amounts to integer cents.

Intermediate values are `fractions.Fraction` (exact rationals), never float.
Every conversion to cents goes through one of these functions, so each
rounding decision is visible and tested.
"""

import math
from fractions import Fraction


def cents_half_even(usd: Fraction) -> int:
    """Round a dollar amount to the nearest cent, ties to the even cent.

    Used for realized and unrealized P&L: unbiased, so rounding does not
    favour trader or firm over many trades. [DEFAULT policy]
    """
    return round(usd * 100)


def cents_ceil(usd: Fraction) -> int:
    """Round a dollar amount up to the next cent (toward +infinity).

    Used for margin requirements, where rounding must never understate the
    amount required. [DEFAULT policy]
    """
    return math.ceil(usd * 100)


def points_to_price(points: int, digits: int) -> Fraction:
    """Exact price from integer points: points / 10**digits."""
    return Fraction(points, 10**digits)
