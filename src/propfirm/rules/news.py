"""Restricted news events and the trade blocks they create [CONFIRMED CC-3].

No trade is opened or closed on an affected symbol from 2 minutes before to
2 minutes after a listed event, on the accounts whose rule set is news-
restricted (funded only, as FTMO; `RuleSet.news_restricted`). Which symbols
an event affects is a versioned instrument map, carried by each event so
old records replay with the map they were made under:
- "v1" (the original map): a currency event blocks forex pairs containing
  that currency, plus XAU, XAG, WTI and Brent when the currency is USD;
- "ftmo-1" (as FTMO's affected instruments, read 31 Oct 2026; current): a
  currency event blocks forex pairs containing that currency, plus gold
  (XAU) when the currency is USD; FTMO's US indices and DXY are not listed
  here;
- both: the weekly EIA oil inventory report blocks WTI and Brent only, and
  crypto is always exempt.

Bank of Japan decisions have no fixed time: the risk desk records the moment
the statement is published, and the block runs 2 minutes from then with no
lead time (`before_ms=0`) [DEFAULT].

Both window edges are inclusive: an event at T blocks [T - 2 min, T + 2 min].
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import added_field
from propfirm.market.spread import NewsWindow
from propfirm.market.symbols import AssetClass, SymbolSpec

TWO_MINUTES_MS = 120_000
INSTRUMENT_MAPS = ("v1", "ftmo-1")
CURRENT_MAP = "ftmo-1"  # new events use this map


class EventScope(StrEnum):
    CURRENCY = "currency"
    OIL_INVENTORY = "oil_inventory"


@dataclass(frozen=True, slots=True)
class RestrictedEvent:
    """One listed event. `currency` is required for CURRENCY scope only."""

    event_id: str
    ts: int  # release time, ms UTC
    scope: EventScope
    currency: str | None = None
    before_ms: int = TWO_MINUTES_MS
    after_ms: int = TWO_MINUTES_MS
    instrument_map: str = added_field("v1")  # added later: older events used v1

    def __post_init__(self) -> None:
        require_text("event_id", self.event_id)
        require_int("ts", self.ts, minimum=0)
        require_int("before_ms", self.before_ms, minimum=0)
        require_int("after_ms", self.after_ms, minimum=0)
        if not isinstance(self.scope, EventScope):
            raise TypeError("scope must be an EventScope")
        if (self.scope is EventScope.CURRENCY) != (self.currency is not None):
            raise ValueError("currency is required for currency events only")
        if self.currency is not None:
            require_text("currency", self.currency)
        if self.instrument_map not in INSTRUMENT_MAPS:
            raise ValueError(f"instrument_map must be one of {INSTRUMENT_MAPS}")
        if self.ts < self.before_ms:
            raise ValueError("window would start before the epoch")


def affected_symbols(event: RestrictedEvent, symbols: Iterable[SymbolSpec]) -> frozenset[str]:
    out: set[str] = set()
    for spec in symbols:
        cls = spec.asset_class
        if cls is AssetClass.CRYPTO:
            continue
        if event.scope is EventScope.OIL_INVENTORY:
            if cls is AssetClass.OIL:
                out.add(spec.name)
        elif cls is AssetClass.FOREX:
            if event.currency in spec.name.split("/"):
                out.add(spec.name)
        elif event.currency != "USD":
            continue
        elif event.instrument_map == "v1" and cls in (AssetClass.METAL, AssetClass.OIL):
            out.add(spec.name)
        elif cls is AssetClass.METAL and spec.name.startswith("XAU/"):
            out.add(spec.name)  # ftmo-1: gold only
    return frozenset(out)


def event_window(event: RestrictedEvent, symbols: Iterable[SymbolSpec]) -> NewsWindow | None:
    """The block `event` creates on `symbols` (both edges inclusive), or None
    when it affects none of them."""
    affected = affected_symbols(event, symbols)
    if not affected:
        return None
    return NewsWindow(affected, event.ts - event.before_ms, event.ts + event.after_ms + 1)


def trade_blocks(
    events: Iterable[RestrictedEvent], symbols: Mapping[str, SymbolSpec]
) -> tuple[NewsWindow, ...]:
    """One block per event that affects at least one configured symbol."""
    windows = (event_window(event, symbols.values()) for event in events)
    return tuple(w for w in windows if w is not None)
