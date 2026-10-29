"""Engine values as plain JSON for the API.

Unlike the journal codec this is for reading, not for records: dataclasses
become objects with a "type" field, enums their value, tuples and frozensets
lists, and Fractions the string "n/d" (exact; never a float).
"""

import dataclasses
from enum import Enum
from fractions import Fraction
from typing import Any


def view(value: object) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, int):
        return value
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, tuple | list):
        return [view(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(view(v) for v in value)
    if isinstance(value, dict):
        return {str(k): view(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {"type": type(value).__name__}
        for f in dataclasses.fields(value):
            out[f.name] = view(getattr(value, f.name))
        return out
    raise TypeError(f"no JSON view for {type(value).__name__}")
