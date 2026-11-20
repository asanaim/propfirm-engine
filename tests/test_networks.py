"""Payout networks (D-14.26): Keccak-256 and EIP-55 against published
vectors, each network's address check, and the policy gate."""

from typing import Any

import pytest

from propfirm.customers.networks import NETWORKS, evm_address_valid, keccak256
from propfirm.policy import PAYOUT_NETWORKS, PolicyError, load_policy, parse_policy
from tests.test_policy import edited

TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # Tether's USDT contract on TRON


def test_keccak_is_ethereums_not_nist_sha3() -> None:
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert keccak256(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )
    long = keccak256(b"a" * 200)  # crosses the 136-byte block
    assert len(long) == 32 and long != keccak256(b"a" * 199)


@pytest.mark.parametrize(
    "address",
    [  # EIP-55's own examples
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
        "0x52908400098527886e0f7030069857d2e4169ee7",  # all lower: no checksum
        "0x8617E340B3D01FA5F11F306F4090FD50E238070D",  # all upper: no checksum
    ],
)
def test_good_evm_addresses(address: str) -> None:
    assert evm_address_valid(address)


@pytest.mark.parametrize(
    "address",
    [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD",  # one letter's case wrong
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe",  # 39 digits
        "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed00",  # no 0x
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeg",  # not hex
        TRON,
    ],
)
def test_bad_evm_addresses(address: str) -> None:
    assert not evm_address_valid(address)


def test_each_network_checks_its_own_addresses() -> None:
    assert NETWORKS["TRC20"].valid(TRON)
    assert not NETWORKS["TRC20"].valid("0x52908400098527886e0f7030069857d2e4169ee7")
    for code in ("ERC20", "BEP20", "POLYGON"):
        assert NETWORKS[code].valid("0x52908400098527886e0f7030069857d2e4169ee7")
        assert not NETWORKS[code].valid(TRON)


def test_the_gate_opens_only_built_networks() -> None:
    assert load_policy().payouts.networks == ("TRC20",)

    def with_networks(value: object) -> Any:
        def change(data: dict[str, Any]) -> None:
            data["payouts"]["networks"] = value

        return edited(change)

    assert parse_policy(with_networks(["TRC20", "BEP20"])).payouts.networks == ("TRC20", "BEP20")
    for bad, match in (
        (["SOL"], "not built"),
        ([], "one or more"),
        (["TRC20", "TRC20"], "each once"),
    ):
        with pytest.raises(PolicyError, match=match):
            parse_policy(with_networks(bad))


def test_the_policy_knows_every_network_built() -> None:
    assert tuple(NETWORKS) == PAYOUT_NETWORKS
