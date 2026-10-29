"""Engine inputs over one Valkey Stream.

Every producer (price connector, order gateway, billing, the clock) appends
to the same stream, so the engine reads one total order. Each entry has a
single field, "input", holding the canonical JSON of one input (prices as a
full PriceUpdate; only the journal stores them by reference).

The engine reads through a consumer group. An entry stays pending until
acknowledged, and a restarted consumer with the same name receives its
pending entries again, in order, before new ones. The runner acknowledges
only after the journal has committed, so an entry is never lost; it may be
delivered twice, and the runner recognises the repeat by its id.

An entry that does not decode becomes a `Malformed` input, so the journal
records it instead of dropping it or stalling on it.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import valkey
import valkey.asyncio as avalkey

from propfirm.journal.codec import CodecError
from propfirm.journal.inputs import INPUT_TYPES, Input, Malformed
from propfirm.journal.machine import CODEC

FIELD = "input"


@dataclass(frozen=True, slots=True)
class InputMessage:
    entry_id: str
    item: Input


def encode_input(item: Input) -> dict[str, str]:
    if isinstance(item, Malformed) or not isinstance(item, INPUT_TYPES):
        raise TypeError(f"cannot send {type(item).__name__} as an input")
    return {FIELD: CODEC.encode(item)}


def _lenient(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="backslashreplace")
    return value if isinstance(value, str) else repr(value)


def _strict(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError(f"expected str or bytes, got {type(value).__name__}")


def decode_input(fields: Mapping[Any, Any] | None) -> Input:
    """Decode a stream entry's fields; anything invalid becomes Malformed."""
    if not fields:
        return Malformed("{}", "empty entry (deleted or trimmed before it was read)")
    try:
        data = {_strict(k): _strict(v) for k, v in fields.items()}
        if set(data) != {FIELD}:
            raise CodecError(f"fields {sorted(data)} != [{FIELD!r}]")
        item = CODEC.decode(data[FIELD])
        if isinstance(item, Malformed) or not isinstance(item, INPUT_TYPES):
            raise CodecError(f"{type(item).__name__} is not an input")
        return item
    except (CodecError, UnicodeDecodeError, TypeError) as exc:
        raw = {_lenient(k): _lenient(v) for k, v in fields.items()}
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return Malformed(text, f"{type(exc).__name__}: {exc}")


class InputStreamWriter:
    def __init__(self, client: avalkey.Valkey, stream: str) -> None:
        self._client = client
        self._stream = stream

    async def publish(self, item: Input, entry_id: str = "*") -> str:
        """Append one input; `entry_id` "*" lets Valkey assign the id."""
        result = await self._client.xadd(self._stream, encode_input(item), id=entry_id)
        return _strict(result)

    async def publish_many(self, items: Iterable[Input]) -> list[str]:
        pipe = self._client.pipeline(transaction=False)
        for item in items:
            pipe.xadd(self._stream, encode_input(item))
        return [_strict(entry_id) for entry_id in await pipe.execute()]


class InputStreamReader:
    """One consumer of the engine's consumer group."""

    def __init__(self, client: avalkey.Valkey, stream: str, group: str, consumer: str) -> None:
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._pending_drained = False
        self._pending_cursor = "0"

    async def ensure_group(self) -> None:
        """Create the group at the start of the stream if it does not exist."""
        try:
            await self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except valkey.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(self, count: int, block_ms: int | None = None) -> list[InputMessage]:
        """Up to `count` messages: this consumer's pending ones first (after a
        restart), then new ones. Each stays pending until `ack`."""
        if count < 1:
            raise ValueError("count must be >= 1")
        if not self._pending_drained:
            pending = await self._xreadgroup(self._pending_cursor, count, None)
            if pending:
                self._pending_cursor = pending[-1].entry_id
                return pending
            self._pending_drained = True
        return await self._xreadgroup(">", count, block_ms)

    async def ack(self, entry_ids: Sequence[str]) -> int:
        if not entry_ids:
            return 0
        return int(await self._client.xack(self._stream, self._group, *entry_ids))

    async def _xreadgroup(self, start: str, count: int, block_ms: int | None) -> list[InputMessage]:
        response = await self._client.xreadgroup(
            self._group, self._consumer, {self._stream: start}, count=count, block=block_ms
        )
        messages: list[InputMessage] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                messages.append(InputMessage(_strict(entry_id), decode_input(fields)))
        return messages
