"""OANDA v20 price stream as data: the reference (second) price source for
the stale price detector (D-14.36). No I/O here: the connector
(`propfirm.service.reference`) does the network.

Protocol (HTTP streaming, one JSON object per line):
GET {stream}/v3/accounts/{account}/pricing/stream?instruments=EUR_USD,...
with `Authorization: Bearer <token>`; in:
- {"type": "PRICE", "instrument": "EUR_USD", "time":
  "2026-11-28T10:15:03.123456789Z", "bids": [{"price": "1.08215", ...}],
  "asks": [{"price": "1.08229", ...}], "tradeable": true, ...};
- {"type": "HEARTBEAT", "time": "..."} about every 5 seconds.

These prices never price anything: they are compared with the engine's
fills. They become integer points with explicit, named rounding that widens
the quote (bid down, ask up), so a fill is never called better than the
reference because of rounding.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

from propfirm.market.symbols import SYMBOLS, SymbolSpec

PRACTICE_STREAM = "https://stream-fxpractice.oanda.com"
LIVE_STREAM = "https://stream-fxtrade.oanda.com"


class OandaError(ValueError):
    pass


# launch symbol -> OANDA instrument: every forex pair and metal by name, the
# oils by OANDA's own names; crypto is not on OANDA's global practice server
INSTRUMENTS = {
    **{
        name: name.replace("/", "_")
        for name, spec in SYMBOLS.items()
        if spec.asset_class.value in ("forex", "metal")
    },
    "WTI": "WTICO_USD",
    "Brent": "BCO_USD",
}
_SYMBOL_OF = {v: k for k, v in INSTRUMENTS.items()}


def instrument(name: str) -> str:
    """Launch symbol -> OANDA instrument: EUR/USD -> EUR_USD."""
    found = INSTRUMENTS.get(name)
    if found is None:
        raise OandaError(f"{name} has no OANDA instrument")
    return found


def symbol(instrument_name: str) -> str | None:
    """OANDA instrument -> launch symbol, None when not one of ours."""
    return _SYMBOL_OF.get(instrument_name)


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    ts: int  # ms UTC, the source's time (truncated to the millisecond)
    bid: int  # points, rounded down
    ask: int  # points, rounded up


@dataclass(frozen=True, slots=True)
class Heartbeat:
    ts: int


def time_ms(text: str) -> int:
    """RFC 3339 with up to nine fractional digits, UTC: milliseconds,
    truncated (explicit: a reference quote is never later than stated)."""
    if not text.endswith("Z"):
        raise OandaError(f"not a UTC time: {text!r}")
    head, _, frac = text[:-1].partition(".")
    try:
        whole = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise OandaError(f"bad time: {text!r}") from exc
    if frac and not frac.isdigit():
        raise OandaError(f"bad time: {text!r}")
    millis = int((frac + "000")[:3]) if frac else 0
    return int(whole.timestamp()) * 1000 + millis


def to_points(price: str, spec: SymbolSpec, *, up: bool) -> int:
    """A decimal price string as points of `spec.digits`, rounded up (ask)
    or down (bid): widening, never tightening, the reference quote."""
    try:
        value = Decimal(price)
    except InvalidOperation as exc:
        raise OandaError(f"bad price: {price!r}") from exc
    if not value.is_finite() or value <= 0:
        raise OandaError(f"bad price: {price!r}")
    scaled = value.scaleb(spec.digits)
    return int(scaled.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR))


def _best(levels: object) -> str:
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
        raise OandaError("no price level")
    price = levels[0].get("price")
    if not isinstance(price, str):
        raise OandaError("price is not a string")
    return price


def parse(line: str | bytes) -> Quote | Heartbeat | None:
    """One stream line: a quote for one of our symbols, a heartbeat, or None
    (another instrument, a non-tradeable price, an empty line)."""
    text = line.decode() if isinstance(line, bytes) else line
    if not text.strip():
        return None
    try:
        data = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise OandaError("not JSON") from exc
    if not isinstance(data, dict):
        raise OandaError("not an object")
    kind = data.get("type")
    if kind == "HEARTBEAT":
        return Heartbeat(time_ms(str(data.get("time", ""))))
    if kind != "PRICE":
        return None
    name = symbol(str(data.get("instrument", "")))
    if name is None or data.get("tradeable") is False:
        return None
    spec = SYMBOLS[name]
    bid = to_points(_best(data.get("bids")), spec, up=False)
    ask = to_points(_best(data.get("asks")), spec, up=True)
    if ask < bid:
        raise OandaError(f"{name}: ask below bid")
    return Quote(name, time_ms(str(data.get("time", ""))), bid, ask)
