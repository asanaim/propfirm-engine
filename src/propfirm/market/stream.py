"""Price updates over a Valkey Stream (step 2 transport).

One stream carries every symbol in a single total order. The engine itself
reads `propfirm.journal.stream`, which carries prices and every other input
in one order (needed for deterministic replay). Consumers read through a
consumer group: an update stays pending until acknowledged, and after a crash
the same consumer receives its unacknowledged updates again before new ones.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import valkey

from propfirm.market.types import PriceUpdate

FIELDS: tuple[str, ...] = ("symbol", "seq", "source_ts", "receive_ts", "price")
# the source's own bid and ask, both or neither (D-13.10)
QUOTE_FIELDS: tuple[str, ...] = ("bid", "ask")
_INT = re.compile(r"-?[0-9]+")


class MalformedMessageError(ValueError):
    """A stream entry that cannot be decoded into a PriceUpdate."""


@dataclass(frozen=True, slots=True)
class StreamMessage:
    entry_id: str
    update: PriceUpdate


def encode(update: PriceUpdate) -> dict[str, str]:
    return {
        "symbol": update.symbol,
        "seq": str(update.seq),
        "source_ts": str(update.source_ts),
        "receive_ts": str(update.receive_ts),
        "price": str(update.price),
    } | (
        {}
        if update.bid is None or update.ask is None
        else {"bid": str(update.bid), "ask": str(update.ask)}
    )


def decode(fields: Mapping[Any, Any]) -> PriceUpdate:
    """Decode stream fields strictly: exact field set, ASCII decimal integers."""
    if not fields:
        raise MalformedMessageError("empty entry (deleted or trimmed?)")
    data = {_text(k): _text(v) for k, v in fields.items()}
    if set(data) not in (set(FIELDS), set(FIELDS) | set(QUOTE_FIELDS)):
        raise MalformedMessageError(f"fields {sorted(data)} != {sorted(FIELDS)} (+ bid, ask)")
    try:
        return PriceUpdate(
            symbol=data["symbol"],
            seq=_int(data["seq"]),
            source_ts=_int(data["source_ts"]),
            receive_ts=_int(data["receive_ts"]),
            price=_int(data["price"]),
            bid=_int(data["bid"]) if "bid" in data else None,
            ask=_int(data["ask"]) if "ask" in data else None,
        )
    except (TypeError, ValueError) as exc:
        raise MalformedMessageError(str(exc)) from exc


class PriceStreamWriter:
    def __init__(self, client: valkey.Valkey, stream: str) -> None:
        self._client = client
        self._stream = stream

    def publish(self, update: PriceUpdate) -> str:
        return _text(self._client.xadd(self._stream, encode(update)))

    def publish_many(self, updates: Iterable[PriceUpdate]) -> list[str]:
        pipe = self._client.pipeline(transaction=False)
        for update in updates:
            pipe.xadd(self._stream, encode(update))
        results = pipe.execute()  # type: ignore[no-untyped-call]
        return [_text(entry_id) for entry_id in results]


class PriceStreamReader:
    """Reads one consumer's share of a stream through a consumer group."""

    def __init__(self, client: valkey.Valkey, stream: str, group: str, consumer: str) -> None:
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._pending_drained = False
        self._pending_cursor = "0"

    def ensure_group(self) -> None:
        """Create the group at the start of the stream if it does not exist."""
        try:
            self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except valkey.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read(self, count: int, block_ms: int | None = None) -> list[StreamMessage]:
        """Return up to `count` messages: first this consumer's unacknowledged
        ones (after a restart), then new ones. Messages stay pending until
        `ack` is called."""
        if count < 1:
            raise ValueError("count must be >= 1")
        if not self._pending_drained:
            # Walk this consumer's pending list from a cursor, so a long list
            # is returned in order and never repeated within this reader.
            pending = self._xreadgroup(self._pending_cursor, count, None)
            if pending:
                self._pending_cursor = pending[-1].entry_id
                return pending
            self._pending_drained = True
        return self._xreadgroup(">", count, block_ms)

    def ack(self, entry_ids: Sequence[str]) -> int:
        if not entry_ids:
            return 0
        return int(self._client.xack(self._stream, self._group, *entry_ids))  # type: ignore[arg-type]

    def _xreadgroup(self, start: str, count: int, block_ms: int | None) -> list[StreamMessage]:
        response = self._client.xreadgroup(
            self._group, self._consumer, {self._stream: start}, count=count, block=block_ms
        )
        messages: list[StreamMessage] = []
        for _stream, entries in response or []:  # type: ignore[union-attr]
            for entry_id, fields in entries:
                messages.append(StreamMessage(_text(entry_id), decode(fields or {})))
        return messages


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise MalformedMessageError(f"expected str or bytes, got {type(value).__name__}")


def _int(value: str) -> int:
    if not _INT.fullmatch(value):
        raise MalformedMessageError(f"not a decimal integer: {value!r}")
    return int(value)
