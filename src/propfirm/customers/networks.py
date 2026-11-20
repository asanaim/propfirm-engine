"""Payout networks (D-14.26): the gates for adding a network.

Each network a payout can go to is listed here with its address check.
Which of them traders may use is a policy switch (`[payouts] networks`),
changed by a settings proposal like any other: adding one is an owner's
decision, not a code change. A network switched off accepts no new
wallets; wallets already on it are still paid, so no trader is stranded.

At launch only TRC20 is on (the functional outline). The others are
built and tested, ready to be switched on:

- TRC20: USDT on TRON. Base58Check of 0x41 and 20 bytes.
- ERC20: USDT on Ethereum; BEP20: USDT on BNB Smart Chain; POLYGON: USDT
  on Polygon PoS. One address format for all three: 0x and 40 hex
  digits. A mixed-case address must carry its EIP-55 checksum (a typo
  is caught); all lower or all upper case carries none and is accepted.
  Addresses are stored as given, so the checksum is kept for display.

The same address on ERC20, BEP20 and POLYGON is a different place for
the money: the trader chooses the network with the address, and a
payout goes on the network of the wallet.
"""

from collections.abc import Callable
from dataclasses import dataclass

from propfirm.customers.payout_flow import tron_address_valid

# --- Keccak-256 (the hash Ethereum uses; not NIST SHA3-256, which pads differently) ---

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK = (1 << 64) - 1
_RATE = 136  # bytes, for a 256-bit output


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _permute(lanes: list[list[int]]) -> None:
    for rc in _ROUND_CONSTANTS:
        c = [lanes[x][0] ^ lanes[x][1] ^ lanes[x][2] ^ lanes[x][3] ^ lanes[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                lanes[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(lanes[x][y], _ROTATIONS[x][y])
        for x in range(5):
            for y in range(5):
                lanes[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        lanes[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 with the original 0x01 padding, as Ethereum uses it."""
    padded = bytearray(data) + b"\x01"
    padded += b"\x00" * ((-len(padded)) % _RATE)
    padded[-1] |= 0x80
    lanes = [[0] * 5 for _ in range(5)]
    for start in range(0, len(padded), _RATE):
        block = padded[start : start + _RATE]
        for i in range(_RATE // 8):
            lanes[i % 5][i // 5] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _permute(lanes)
    out = b"".join(lanes[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


_HEX = frozenset("0123456789abcdefABCDEF")


def evm_address_valid(address: str) -> bool:
    """0x and 40 hex digits; mixed case must match EIP-55."""
    if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
        return False
    body = address[2:]
    if not set(body) <= _HEX:
        return False
    if body == body.lower() or body == body.upper():
        return True
    digest = keccak256(body.lower().encode()).hex()
    return all(
        (ch.upper() if int(digest[i], 16) >= 8 else ch.lower()) == ch for i, ch in enumerate(body)
    )


@dataclass(frozen=True, slots=True)
class Network:
    code: str
    name: str
    currency: str
    valid: Callable[[str], bool]
    hint: str  # what an address looks like, for the trader


NETWORKS: dict[str, Network] = {
    "TRC20": Network(
        "TRC20", "USDT on TRON (TRC-20)", "USDT", tron_address_valid, "starts with T, 34 characters"
    ),
    "ERC20": Network(
        "ERC20", "USDT on Ethereum (ERC-20)", "USDT", evm_address_valid, "0x and 40 characters"
    ),
    "BEP20": Network(
        "BEP20",
        "USDT on BNB Smart Chain (BEP-20)",
        "USDT",
        evm_address_valid,
        "0x and 40 characters",
    ),
    "POLYGON": Network(
        "POLYGON", "USDT on Polygon", "USDT", evm_address_valid, "0x and 40 characters"
    ),
}
