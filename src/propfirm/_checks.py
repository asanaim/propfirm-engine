"""Strict type checks shared by engine records.

`bool` is a subclass of `int` in Python, so `isinstance(True, int)` is true.
Engine numbers must reject booleans and floats explicitly.
"""


def is_int(value: object) -> bool:
    """True only for real integers, never for bool or float."""
    return isinstance(value, int) and not isinstance(value, bool)


def require_int(name: str, value: object, *, minimum: int | None = None) -> int:
    """Return `value` if it is a real integer at or above `minimum`, else raise."""
    if not is_int(value):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    assert isinstance(value, int)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def require_text(name: str, value: object) -> str:
    """Return `value` if it is a non-empty string without surrounding whitespace."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and unpadded, got {value!r}")
    return value
