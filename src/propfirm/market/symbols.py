"""The 30 launch symbols (Confirmed Configuration CC-5).

The symbol list is confirmed. `digits` (decimal places of a price) are
engine defaults for synthetic prices and must be reconciled with the precision
Twelve Data actually streams, measured in the P2 trial (confirmation C10).
"""

from dataclasses import dataclass
from enum import StrEnum

from propfirm._checks import require_int, require_text


class AssetClass(StrEnum):
    FOREX = "forex"
    METAL = "metal"
    OIL = "oil"
    CRYPTO = "crypto"


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    name: str
    asset_class: AssetClass
    digits: int

    def __post_init__(self) -> None:
        require_text("name", self.name)
        require_int("digits", self.digits, minimum=0)
        if self.digits > 8:
            raise ValueError(f"digits must be <= 8, got {self.digits}")


def _fx(name: str) -> SymbolSpec:
    return SymbolSpec(name, AssetClass.FOREX, 3 if name.endswith("/JPY") else 5)


_FOREX_MAJORS = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD")
_FOREX_CROSSES = (
    "EUR/GBP",
    "EUR/JPY",
    "EUR/CHF",
    "EUR/AUD",
    "EUR/CAD",
    "EUR/NZD",
    "GBP/JPY",
    "GBP/CHF",
    "GBP/AUD",
    "GBP/CAD",
    "AUD/JPY",
    "AUD/CAD",
    "CAD/JPY",
    "CHF/JPY",
)

LAUNCH_SYMBOLS: tuple[SymbolSpec, ...] = (
    *(_fx(n) for n in _FOREX_MAJORS),
    *(_fx(n) for n in _FOREX_CROSSES),
    SymbolSpec("XAU/USD", AssetClass.METAL, 2),
    SymbolSpec("XAG/USD", AssetClass.METAL, 3),
    SymbolSpec("WTI", AssetClass.OIL, 2),
    SymbolSpec("Brent", AssetClass.OIL, 2),
    SymbolSpec("BTC/USD", AssetClass.CRYPTO, 2),
    SymbolSpec("ETH/USD", AssetClass.CRYPTO, 2),
    SymbolSpec("SOL/USD", AssetClass.CRYPTO, 2),
    SymbolSpec("XRP/USD", AssetClass.CRYPTO, 4),
    SymbolSpec("LTC/USD", AssetClass.CRYPTO, 2),
)

SYMBOLS: dict[str, SymbolSpec] = {s.name: s for s in LAUNCH_SYMBOLS}
