"""Canonical JSON for journal records.

Each value has exactly one encoding, so the hash of an encoded record is
stable across processes and machines:

- object keys sorted, no whitespace, ASCII only (non-ASCII is escaped);
- int, str, true/false and null as plain JSON; floats are never accepted;
- every integer fits a signed 64-bit integer (PostgreSQL BIGINT), and no
  string holds NUL or a lone surrogate (PostgreSQL text cannot store them),
  so anything that decodes can also be stored;
- tuple as an array (decoded back to a tuple); lists are rejected;
- enum member as {"_t": <enum class>, "v": <value>};
- Fraction as {"_t": "Fraction", "n": <numerator>, "d": <denominator>};
- frozenset as {"_t": "frozenset", "v": [<items sorted by their encoding>]};
- mapping with str keys as {"_t": "dict", "v": {...}} (decoded to a dict);
- registered dataclass as {"_t": <class name>, <every field>: <value>}.

Decoding is strict: unknown tags, missing or extra fields, floats, NaN,
duplicate keys and any text that is not the canonical encoding of its own
value are errors, and decoded dataclasses run their own validation.
"""

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from enum import Enum
from fractions import Fraction
from typing import Any

from propfirm._checks import is_int

_TAG = "_t"
_INT64 = range(-(2**63), 2**63)
_BUILTIN_TAGS = frozenset({"Fraction", "frozenset", "dict"})


class CodecError(ValueError):
    """A value that cannot be encoded, or text that is not a canonical encoding."""


def digest(text: str) -> str:
    """SHA-256 of canonical (ASCII) text, as 64 lowercase hex digits."""
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _no_float(text: str) -> object:
    raise CodecError(f"floats are not allowed: {text}")


def _no_constant(text: str) -> object:
    raise CodecError(f"{text} is not allowed")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CodecError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _check_int(value: int) -> int:
    if value not in _INT64:
        raise CodecError(f"integer {value} does not fit 64 bits")
    return value


def _check_str(value: str) -> str:
    for ch in value:
        if ch == "\x00" or "\ud800" <= ch <= "\udfff":
            raise CodecError(f"string holds {ch!r}")
    return value


def _dumps(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Codec:
    """Encodes and decodes the registered dataclasses and enums (and the
    built-in values above). Names must be unique: the class name is the tag."""

    def __init__(self, types: Iterable[type]) -> None:
        self._types: dict[str, type] = {}
        for cls in types:
            name = cls.__name__
            if name in self._types or name in _BUILTIN_TAGS:
                raise ValueError(f"type name {name!r} is registered twice or reserved")
            if issubclass(cls, Enum):
                if not all(isinstance(m.value, str) for m in cls):
                    raise TypeError(f"{name}: only str-valued enums are supported")
            elif dataclasses.is_dataclass(cls):
                for f in dataclasses.fields(cls):
                    if f.name == _TAG or not f.init:
                        raise TypeError(f"{name}.{f.name}: unsupported field")
            else:
                raise TypeError(f"{name} is neither a dataclass nor an enum")
            self._types[name] = cls

    def encode(self, value: object) -> str:
        return _dumps(self._to_json(value))

    def decode(self, text: str) -> object:
        """Decode `text`, which must be the canonical encoding of its value."""
        if not isinstance(text, str):
            raise CodecError("text must be str")
        try:
            data = json.loads(
                text,
                object_pairs_hook=_unique_keys,
                parse_float=_no_float,
                parse_constant=_no_constant,
            )
        except json.JSONDecodeError as exc:
            raise CodecError(f"invalid JSON: {exc}") from exc
        value = self._from_json(data)
        if self.encode(value) != text:
            raise CodecError("text is not in canonical form")
        return value

    # --- encoding -----------------------------------------------------------

    def _registered(self, cls: type) -> str:
        name = cls.__name__
        if self._types.get(name) is not cls:
            raise CodecError(f"type {name} is not registered")
        return name

    def _to_json(self, value: object) -> object:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, Enum):
            return {_TAG: self._registered(type(value)), "v": value.value}
        if isinstance(value, int) and is_int(value):
            return _check_int(value)
        if isinstance(value, str):
            return _check_str(value)
        if isinstance(value, Fraction):
            n, d = _check_int(value.numerator), _check_int(value.denominator)
            return {_TAG: "Fraction", "n": n, "d": d}
        if isinstance(value, tuple):
            return [self._to_json(v) for v in value]
        if isinstance(value, frozenset):
            items = sorted((self._to_json(v) for v in value), key=_dumps)
            return {_TAG: "frozenset", "v": items}
        if isinstance(value, Mapping):
            out: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CodecError("mapping keys must be str")
                out[_check_str(key)] = self._to_json(item)
            return {_TAG: "dict", "v": out}
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            encoded: dict[str, object] = {_TAG: self._registered(type(value))}
            for f in dataclasses.fields(value):
                encoded[f.name] = self._to_json(getattr(value, f.name))
            return encoded
        raise CodecError(f"cannot encode {type(value).__name__}")

    # --- decoding -----------------------------------------------------------

    def _from_json(self, data: object) -> object:
        if data is None or isinstance(data, bool):
            return data
        if isinstance(data, int):
            return _check_int(data)
        if isinstance(data, str):
            return _check_str(data)
        if isinstance(data, list):
            return tuple(self._from_json(v) for v in data)
        if not isinstance(data, dict):
            raise CodecError(f"unexpected JSON value {type(data).__name__}")
        tag = data.get(_TAG)
        if not isinstance(tag, str):
            raise CodecError("object without a type tag")
        try:
            return self._tagged(tag, data)
        except CodecError:
            raise
        except (TypeError, ValueError) as exc:
            raise CodecError(f"{tag}: {exc}") from exc

    def _tagged(self, tag: str, data: dict[str, object]) -> object:
        if tag == "Fraction":
            _keys(data, {"n", "d"})
            n, d = data["n"], data["d"]
            if not isinstance(n, int) or not isinstance(d, int) or not (is_int(n) and is_int(d)):
                raise CodecError("Fraction needs integer n and d")
            if d < 1:
                raise CodecError("Fraction needs d >= 1")
            return Fraction(n, d)
        if tag == "frozenset":
            _keys(data, {"v"})
            items = data["v"]
            if not isinstance(items, list):
                raise CodecError("frozenset needs an array")
            return frozenset(self._from_json(v) for v in items)
        if tag == "dict":
            _keys(data, {"v"})
            body = data["v"]
            if not isinstance(body, dict):
                raise CodecError("dict needs an object")
            return {_check_str(key): self._from_json(v) for key, v in body.items()}
        cls = self._types.get(tag)
        if cls is None:
            raise CodecError(f"unknown type tag {tag!r}")
        if issubclass(cls, Enum):
            _keys(data, {"v"})
            return cls(data["v"])
        assert dataclasses.is_dataclass(cls)
        names = [f.name for f in dataclasses.fields(cls)]
        _keys(data, set(names))
        return cls(**{name: self._from_json(data[name]) for name in names})


def _keys(data: dict[str, object], expected: set[str]) -> None:
    got = set(data) - {_TAG}
    if got != expected:
        raise CodecError(f"fields {sorted(got)} != {sorted(expected)}")
