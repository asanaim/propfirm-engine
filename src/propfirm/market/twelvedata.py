"""Twelve Data's WebSocket price stream, as data (P2 step 6). No I/O here:
the connector (`propfirm.service.twelvedata`) does the network.

Protocol (wss://ws.twelvedata.com/v1/quotes/price, key as `apikey`):

- out: {"action": "subscribe", "params": {"symbols": "EUR/USD,XAU/USD"}},
  {"action": "heartbeat"} every 10 seconds, {"action": "reset"};
- in: {"event": "subscribe-status", "status": "ok", "success": [...],
  "fails": [...]}, {"event": "heartbeat", "status": "ok"},
  {"event": "price", "symbol": "EUR/USD", "timestamp": 1730880000,
  "price": 1.08215, ...} (UNIX seconds; `bid`, `ask`, `day_volume` where
  available, not used: the engine derives bid and ask, step 4).

Prices become integer points exactly (CLAUDE.md): the JSON number is read as
a decimal, never a float, and a price with more decimal places than the
symbol's `digits` is refused rather than rounded (`to_points`). How many
places each symbol really streams is measured in the trial (C10).
"""

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from propfirm.market.symbols import SYMBOLS, SymbolSpec

URL = "wss://ws.twelvedata.com/v1/quotes/price"
HEARTBEAT_SECONDS = 10  # Twelve Data: "advised to be sent every 10 seconds"


class TwelveDataError(ValueError):
    pass


# --- symbols ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SymbolMap:
    """Launch symbol <-> Twelve Data symbol, one to one."""

    to_source: Mapping[str, str]

    def __post_init__(self) -> None:
        unknown = set(self.to_source) - set(SYMBOLS)
        if unknown:
            raise TwelveDataError(f"not launch symbols: {sorted(unknown)}")
        if len(set(self.to_source.values())) != len(self.to_source):
            raise TwelveDataError("two launch symbols map to one Twelve Data symbol")
        for name, source in self.to_source.items():
            if not source or "," in source or source != source.strip():
                raise TwelveDataError(f"{name}: bad Twelve Data symbol {source!r}")

    @property
    def from_source(self) -> dict[str, str]:
        return {v: k for k, v in self.to_source.items()}

    def subset(self, names: tuple[str, ...]) -> SymbolMap:
        missing = [n for n in names if n not in self.to_source]
        if missing:
            raise TwelveDataError(f"no Twelve Data symbol for {missing}")
        return SymbolMap({n: self.to_source[n] for n in names})


def load_symbol_map(path: Path) -> SymbolMap:
    with path.open("rb") as f:
        data = tomllib.load(f)
    table = data.get("symbols")
    if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
        raise TwelveDataError(f"{path}: [symbols] maps launch symbols to strings")
    return SymbolMap({str(k): str(v) for k, v in table.items()})


# --- messages out ---------------------------------------------------------------------------


def subscribe(m: SymbolMap) -> str:
    symbols = ",".join(m.to_source[n] for n in sorted(m.to_source))
    return json.dumps({"action": "subscribe", "params": {"symbols": symbols}})


def heartbeat() -> str:
    return json.dumps({"action": "heartbeat"})


# --- messages in ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Price:
    source_symbol: str
    ts_s: int  # UNIX seconds, as sent
    price: Decimal


@dataclass(frozen=True, slots=True)
class SubscribeStatus:
    ok: bool
    success: tuple[str, ...]
    fails: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    ok: bool


@dataclass(frozen=True, slots=True)
class Other:
    """Anything else (kept for the log, never acted on)."""

    event: str
    raw: Mapping[str, Any]


Event = Price | SubscribeStatus | Heartbeat | Other


def _no_float(text: str) -> Decimal:
    return Decimal(text)


def _symbols(items: object) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    out = []
    for x in items:
        if isinstance(x, dict) and isinstance(x.get("symbol"), str):
            out.append(x["symbol"])
        elif isinstance(x, str):
            out.append(x)
    return tuple(out)


def parse(text: str | bytes) -> Event:
    """One message from the stream. Numbers are read as decimals."""
    try:
        data = json.loads(text, parse_float=_no_float, parse_int=int)
    except (ValueError, InvalidOperation) as exc:
        raise TwelveDataError(f"not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise TwelveDataError("a message is a JSON object")
    event = data.get("event")
    if event == "price":
        symbol, ts, price = data.get("symbol"), data.get("timestamp"), data.get("price")
        if not isinstance(symbol, str) or not symbol:
            raise TwelveDataError("price without a symbol")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
            raise TwelveDataError(f"{symbol}: timestamp must be whole UNIX seconds")
        if isinstance(price, bool) or not isinstance(price, int | Decimal):
            raise TwelveDataError(f"{symbol}: price must be a number")
        value = Decimal(price)
        if not value.is_finite() or value <= 0:
            raise TwelveDataError(f"{symbol}: price must be positive")
        return Price(symbol, ts, value)
    if event == "subscribe-status":
        return SubscribeStatus(
            data.get("status") == "ok", _symbols(data.get("success")), _symbols(data.get("fails"))
        )
    if event == "heartbeat":
        return Heartbeat(data.get("status") == "ok")
    return Other(str(event), data)


# --- prices --------------------------------------------------------------------------------


def places(price: Decimal) -> int:
    """Decimal places as sent (1.08200 -> 5: trailing zeros count, they are
    what the source streamed)."""
    exponent = price.as_tuple().exponent
    assert isinstance(exponent, int)  # finite
    return max(0, -exponent)


def to_points(price: Decimal, spec: SymbolSpec) -> int | None:
    """Exact integer points, or None when the price has more decimal places
    than the symbol's `digits` (refused, never rounded: C10 decides digits).
    Trailing zeros beyond `digits` are exact and accepted."""
    scaled = price.scaleb(spec.digits)
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)
