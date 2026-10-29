import pytest

from propfirm.market.stream import MalformedMessageError, decode, encode
from propfirm.market.types import PriceUpdate

U = PriceUpdate("EUR/USD", 7, 1_790_000_000_000, 1_790_000_000_150, 110_123)


def test_round_trip_str_and_bytes() -> None:
    fields = encode(U)
    assert decode(fields) == U
    assert decode({k.encode(): v.encode() for k, v in fields.items()}) == U


@pytest.mark.parametrize(
    "mutate",
    [
        {"seq": "7.0"},
        {"seq": "+7"},
        {"seq": " 7"},
        {"seq": "7_0"},
        {"seq": "\u0667"},  # Arabic-Indic seven: str.isdigit() accepts it, we must not
        {"price": "0"},
        {"price": "-5"},
        {"symbol": ""},
    ],
)
def test_strict_decoding(mutate: dict[str, str]) -> None:
    with pytest.raises(MalformedMessageError):
        decode({**encode(U), **mutate})


def test_missing_or_extra_fields_rejected() -> None:
    fields = encode(U)
    del fields["price"]
    with pytest.raises(MalformedMessageError):
        decode(fields)
    with pytest.raises(MalformedMessageError):
        decode({**encode(U), "bid": "1"})
    with pytest.raises(MalformedMessageError):
        decode({})
