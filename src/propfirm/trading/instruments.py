"""Tradable instrument specifications.

Sources of each value (Confirmed Configuration):
- Leverage per asset class: [CONFIRMED] forex 1:100, metals 1:50, oil 1:20,
  crypto 1:3.33.
- Contract sizes: [DEFAULT] forex 100,000 units; XAU 100 oz; XAG 5,000 oz;
  WTI and Brent 1,000 barrels; crypto 1 coin.
- Lot caps per order and per symbol: [DEFAULT] development placeholders,
  stated per $100,000 of initial balance and scaled to the account by
  `scaled_cap` (Plan v4: caps scale with account size; values set in P3).
- Minimum spreads: [DEFAULT] development placeholders, calibrated in P3.
- Commission: none by default. A symbol priced with `SpreadAndCommission`
  pays a commission when a position opens (the shipped policy sets one on
  crypto, as prop firms charge it; see docs/spread-benchmarks.md).

Volumes are in hundredths of a lot (0.01 lot = 1).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from propfirm._checks import require_int, require_text
from propfirm.market.spread import AnySpreadConfig, MarketSpreadConfig, SpreadConfig
from propfirm.market.symbols import LAUNCH_SYMBOLS, AssetClass, SymbolSpec

LEVERAGE: Mapping[AssetClass, Fraction] = {
    AssetClass.FOREX: Fraction(100),
    AssetClass.METAL: Fraction(50),
    AssetClass.OIL: Fraction(20),
    AssetClass.CRYPTO: Fraction(333, 100),
}

_CONTRACT_SIZE: Mapping[str, int] = {"XAU/USD": 100, "XAG/USD": 5_000, "WTI": 1_000, "Brent": 1_000}
_CONTRACT_BY_CLASS: Mapping[AssetClass, int] = {AssetClass.FOREX: 100_000, AssetClass.CRYPTO: 1}

# Per $100,000 of initial balance: (max volume per order, max open + pending
# volume per symbol), hundredths of a lot. Development placeholders.
_LOT_CAPS: Mapping[AssetClass, tuple[int, int]] = {
    AssetClass.FOREX: (5_000, 10_000),
    AssetClass.METAL: (2_000, 4_000),
    AssetClass.OIL: (2_000, 4_000),
    AssetClass.CRYPTO: (1_000, 2_000),
}

_MIN_SPREAD: Mapping[str, int] = {
    "XAU/USD": 20,
    "XAG/USD": 20,
    "WTI": 3,
    "Brent": 3,
    "BTC/USD": 1_000,
    "ETH/USD": 100,
    "SOL/USD": 5,
    "XRP/USD": 5,
    "LTC/USD": 5,
}
_MAJORS = frozenset({"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"})


_ONE_PERCENT = Fraction(1, 100)


@dataclass(frozen=True, slots=True)
class SpreadAndCommission:
    """A market spread plus a commission charged once, when a position
    opens: `commission` of the position's notional at the fill price (in
    USD, rounded half-even to the cent). This is how prop firms price
    crypto: a raw spread, and a commission as a share of the trade."""

    spread: MarketSpreadConfig
    commission: Fraction  # share of the notional, e.g. 0.0525 % = Fraction(21, 40_000)

    def __post_init__(self) -> None:
        if not isinstance(self.spread, MarketSpreadConfig):
            raise TypeError("spread must be a MarketSpreadConfig")
        if not isinstance(self.commission, Fraction) or not 0 < self.commission < _ONE_PERCENT:
            raise ValueError("commission must be a Fraction above 0 and below 1 %")


type Pricing = AnySpreadConfig | SpreadAndCommission


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: SymbolSpec
    contract_size: int
    leverage: Fraction
    quote_currency: str
    max_order_volume_per_100k: int
    max_symbol_volume_per_100k: int
    spread: Pricing  # the spread model, with any commission

    def __post_init__(self) -> None:
        if not isinstance(self.spread, (SpreadConfig, MarketSpreadConfig, SpreadAndCommission)):
            raise TypeError(
                "spread must be a SpreadConfig, MarketSpreadConfig or SpreadAndCommission"
            )
        require_int("contract_size", self.contract_size, minimum=1)
        require_text("quote_currency", self.quote_currency)
        require_int("max_order_volume_per_100k", self.max_order_volume_per_100k, minimum=1)
        require_int("max_symbol_volume_per_100k", self.max_symbol_volume_per_100k, minimum=1)
        if not isinstance(self.leverage, Fraction) or self.leverage <= 0:
            raise ValueError("leverage must be a positive Fraction")
        if self.max_order_volume_per_100k > self.max_symbol_volume_per_100k:
            raise ValueError("order cap must be <= symbol cap")

    @property
    def name(self) -> str:
        return self.symbol.name

    @property
    def digits(self) -> int:
        return self.symbol.digits

    @property
    def spread_model(self) -> AnySpreadConfig:
        """The spread settings alone."""
        return self.spread.spread if isinstance(self.spread, SpreadAndCommission) else self.spread

    @property
    def commission(self) -> Fraction:
        """Commission as a share of the notional, charged when a position opens (0: none)."""
        if isinstance(self.spread, SpreadAndCommission):
            return self.spread.commission
        return Fraction(0)


CAP_BASIS_CENTS = 10_000_000  # $100,000


def scaled_cap(cap_per_100k: int, initial_balance_cents: int) -> int:
    """A cap stated per $100,000 of initial balance, scaled to an account and
    rounded down to whole hundredths of a lot (never above the stated ratio)."""
    require_int("cap_per_100k", cap_per_100k, minimum=0)
    require_int("initial_balance_cents", initial_balance_cents, minimum=0)
    return cap_per_100k * initial_balance_cents // CAP_BASIS_CENTS


def quote_currency(name: str) -> str:
    """Quote currency of a symbol: the part after '/', or USD for oil."""
    return name.split("/")[1] if "/" in name else "USD"


def default_instrument(spec: SymbolSpec) -> InstrumentSpec:
    cls = spec.asset_class
    contract = _CONTRACT_SIZE.get(spec.name, _CONTRACT_BY_CLASS.get(cls, 0))
    max_order, max_symbol = _LOT_CAPS[cls]
    if spec.name in _MIN_SPREAD:
        min_spread = _MIN_SPREAD[spec.name]
    else:
        min_spread = 5 if spec.name in _MAJORS else 15
    return InstrumentSpec(
        symbol=spec,
        contract_size=contract,
        leverage=LEVERAGE[cls],
        quote_currency=quote_currency(spec.name),
        max_order_volume_per_100k=max_order,
        max_symbol_volume_per_100k=max_symbol,
        spread=SpreadConfig(min_points=min_spread),
    )


def default_instruments() -> dict[str, InstrumentSpec]:
    return {s.name: default_instrument(s) for s in LAUNCH_SYMBOLS}


def conversion_route(currency: str, instruments: Mapping[str, InstrumentSpec]) -> tuple[str, bool]:
    """How to turn an amount in `currency` into USD.

    Returns (symbol, multiply): multiply by the symbol's price if it is
    CUR/USD, divide if it is USD/CUR. Raise if no route exists.
    """
    if f"{currency}/USD" in instruments:
        return f"{currency}/USD", True
    if f"USD/{currency}" in instruments:
        return f"USD/{currency}", False
    raise ValueError(f"no USD conversion route for {currency}")
