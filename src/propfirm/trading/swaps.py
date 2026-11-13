"""Overnight swaps (build plan step 1; product line version 1, section 8;
decisions K6 and D-13.19).

A position open at a 17:00 New York reset pays a swap for the nights that
reset covers:

    charge = position value x annual rate x nights / 360

The position value is its notional at the symbol's current feed price
(midpoint), in US dollars; the charge is rounded half-even to the cent and
taken from the balance like a commission. Rates are annual shares of the
value, charged only: a swap is never credited.

Annual rates, from eight central bank rates (owner settings) and markups:

| Asset | Long | Short |
| Forex | quote - base + markup, floor 0 | base - quote + markup, floor 0 |
| Gold, silver | USD rate + metal markup | 0 |
| Oil | oil rate | 0 |
| Crypto | crypto rate | crypto rate |

A forex side whose rate differential exceeds the markup would earn carry at
a broker; here it pays nothing and earns nothing.

Nights per reset, by the New York weekday of the reset (Monday first):
forex, metals and oil 1, 1, 3, 1, 1, 0, 0 (Wednesday's reset carries the
weekend, as brokers' triple swap); crypto 1 every night, weekends included.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from propfirm.market.symbols import AssetClass, SymbolSpec

YEAR_DAYS = 360  # the money-market day count brokers use for swaps

# nights charged at a reset, by New York weekday, Monday first
WEEKDAYS_TRIPLE_WEDNESDAY: tuple[int, ...] = (1, 1, 3, 1, 1, 0, 0)
EVERY_NIGHT: tuple[int, ...] = (1, 1, 1, 1, 1, 1, 1)

CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")


@dataclass(frozen=True, slots=True)
class SwapTerms:
    """One symbol's swap: annual charge rates per side and the nights each
    reset carries (by New York weekday, Monday first)."""

    long_rate: Fraction
    short_rate: Fraction
    nights: tuple[int, ...]

    def __post_init__(self) -> None:
        for name in ("long_rate", "short_rate"):
            value = getattr(self, name)
            if not isinstance(value, Fraction) or not 0 <= value < 1:
                raise ValueError(f"{name} must be a Fraction in [0, 1)")
        if not isinstance(self.nights, tuple) or len(self.nights) != 7:
            raise ValueError("nights must be 7 whole numbers, Monday first")
        for n in self.nights:
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 7:
                raise ValueError("each weekday carries 0 to 7 nights")

    def rate(self, long: bool) -> Fraction:
        return self.long_rate if long else self.short_rate


@dataclass(frozen=True, slots=True)
class SwapSettings:
    """The owner settings swaps are worked out from."""

    rates: Mapping[str, Fraction]  # central bank policy rate per currency
    markup: Fraction  # forex, added to the costly side
    metal_markup: Fraction  # added to the USD rate for long metals
    oil_long: Fraction  # long oil; short pays nothing
    crypto: Fraction  # both sides, every night

    def __post_init__(self) -> None:
        missing = set(CURRENCIES) - set(self.rates)
        if missing:
            raise ValueError(f"rates missing for {sorted(missing)}")
        for code, value in self.rates.items():
            if not isinstance(value, Fraction) or not 0 <= value < 1:
                raise ValueError(f"rate for {code} must be a Fraction in [0, 1)")
        for name in ("markup", "metal_markup", "oil_long", "crypto"):
            value = getattr(self, name)
            if not isinstance(value, Fraction) or not 0 <= value < 1:
                raise ValueError(f"{name} must be a Fraction in [0, 1)")


def swap_terms(symbol: SymbolSpec, settings: SwapSettings) -> SwapTerms:
    """The swap of one launch symbol under the settings."""
    match symbol.asset_class:
        case AssetClass.FOREX:
            base, quote = symbol.name.split("/")
            difference = settings.rates[base] - settings.rates[quote]
            return SwapTerms(
                long_rate=max(Fraction(0), settings.markup - difference),
                short_rate=max(Fraction(0), settings.markup + difference),
                nights=WEEKDAYS_TRIPLE_WEDNESDAY,
            )
        case AssetClass.METAL:
            return SwapTerms(
                settings.rates["USD"] + settings.metal_markup,
                Fraction(0),
                WEEKDAYS_TRIPLE_WEDNESDAY,
            )
        case AssetClass.OIL:
            return SwapTerms(settings.oil_long, Fraction(0), WEEKDAYS_TRIPLE_WEDNESDAY)
        case AssetClass.CRYPTO:
            return SwapTerms(settings.crypto, settings.crypto, EVERY_NIGHT)
