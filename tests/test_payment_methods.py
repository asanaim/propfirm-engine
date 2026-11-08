"""Rules AC02, AC13, AC14: payment and withdrawal methods and their secrets."""

import base64
import os
from fractions import Fraction
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag

from propfirm.policy import PolicyError
from propfirm.settings.methods import (
    MethodSecret,
    PaymentMethod,
    SecretsUnavailable,
    UserField,
    Vault,
    method_from_json,
)
from propfirm.settings.overlay import from_json, public_json, to_json
from propfirm.staff.model import Tier
from tests.test_settings import OWNERS, MemoryProposals, MemoryRecords, api

KEY = bytes(range(32))


def card(**over: Any) -> dict[str, Any]:
    body = {
        "kind": "payment",
        "name": "Card (sandbox)",
        "adapter": "sandbox",
        "enabled": True,
        "min_cents": 1_000,
        "max_cents": 100_000,
        "fixed_charge_cents": 0,
        "percent_charge": "0",
        "instructions": "",
        "user_fields": [],
        "reason": "launch",
    }
    return {**body, **over}


def test_a_method_checks_itself_and_round_trips() -> None:
    m = method_from_json({**card(), "method_id": "card"})
    assert m.allows(1_000) and m.allows(100_000) and not m.allows(999) and not m.allows(100_001)
    assert from_json(*to_json(m)) == m
    for bad in ({"adapter": "paypal"}, {"min_cents": 5, "max_cents": 4}, {"percent_charge": "100"},
                {"kind": "refund"}):  # fmt: skip
        with pytest.raises(PolicyError):
            method_from_json({**card(**bad), "method_id": "card"})


def test_the_charge_is_fixed_plus_percent_rounded_up_to_a_cent() -> None:
    bank = PaymentMethod("bank", "withdrawal", "Bank", "manual", True, 12_200, 100_000, 100,
                         Fraction(2, 100), "", (UserField("iban", "IBAN"),))  # fmt: skip
    assert bank.charge_cents(12_200) == 100 + 244
    assert bank.charge_cents(12_201) == 100 + 245  # 244.02 cents rounds up
    free = PaymentMethod("x", "withdrawal", "X", "manual", True, 0, 0, 0, Fraction(0))
    assert free.charge_cents(5) == 0 and free.allows(10**12)  # 0: no maximum


def test_secrets_are_sealed_bound_to_their_method_and_never_shown() -> None:
    vault = Vault(KEY)
    sealed = vault.seal("card", "api_key", "sk_live_123", os.urandom(12))
    assert "sk_live_123" not in str(to_json(sealed))
    assert vault.open(sealed) == "sk_live_123"
    moved = MethodSecret("other", "api_key", sealed.nonce, sealed.ciphertext)
    with pytest.raises(InvalidTag):
        vault.open(moved)  # a ciphertext cannot be moved to another method
    assert public_json(sealed)[1] == {
        "method_id": "card",
        "name": "api_key",
        "value": "set (hidden)",
    }
    with pytest.raises(SecretsUnavailable):
        Vault(None).seal("card", "api_key", "x", os.urandom(12))
    env = {"PROPFIRM_SECRETS_KEY": base64.b64encode(KEY).decode()}
    assert Vault.from_env(env).open(sealed) == "sk_live_123"


def test_an_admin_proposes_a_method_and_only_an_owner_approves() -> None:
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    assert api("GET", "/payment-methods", signed_in=False, **kw).json() == []
    assert (
        api("PUT", "/admin/payment-methods/card", card(), user="admin-1", **kw).status_code == 200
    )
    for user in ("admin-1", "admin-2"):  # the proposer, then another admin
        refused = api(
            "POST", "/admin/settings/proposals/1/approve", {"reason": "x"}, user=user, **kw
        )
        assert refused.status_code == 403
    ok = api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-a", **kw)
    assert ok.status_code == 200, ok.text
    listed = api("GET", "/payment-methods", signed_in=False, **kw).json()
    assert [(m["method_id"], m["min_cents"]) for m in listed] == [("card", 1_000)]
    assert "adapter" not in listed[0]
    assert api("GET", "/payment-methods?kind=withdrawal", signed_in=False, **kw).json() == []
    # disabling is a change like any other
    api("PUT", "/admin/payment-methods/card", card(enabled=False), user="owner-a", **kw)
    own = api("POST", "/admin/settings/proposals/2/approve", {"reason": "x"}, user="owner-a", **kw)
    assert own.json()["detail"]["code"] == "four_eyes"
    api("POST", "/admin/settings/proposals/2/approve", {"reason": "ok"}, user="owner-b", **kw)
    assert api("GET", "/payment-methods", signed_in=False, **kw).json() == []


def test_a_secret_is_proposed_sealed_and_listed_only_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROPFIRM_SECRETS_KEY", base64.b64encode(KEY).decode())
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    secret = {"value": "sk_live_123", "reason": "provider key"}
    unknown = api(
        "PUT", "/admin/payment-methods/card/secrets/api_key", secret, user="admin-1", **kw
    )
    assert unknown.status_code == 404
    api("PUT", "/admin/payment-methods/card", card(), user="admin-1", **kw)
    api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-a", **kw)
    proposed = api(
        "PUT", "/admin/payment-methods/card/secrets/api_key", secret, user="admin-1", **kw
    )
    assert proposed.status_code == 200, proposed.text
    assert "sk_live_123" not in proposed.text
    assert proposed.json()["proposal"]["value"]["value"] == "set (hidden)"
    api("POST", "/admin/settings/proposals/2/approve", {"reason": "ok"}, user="owner-b", **kw)
    admin = api("GET", "/admin/payment-methods", **kw).json()
    assert admin["methods"][0]["secrets"] == ["api_key"]
    for url in (
        "/admin/payment-methods",
        "/admin/settings/changes",
        "/admin/settings/proposals?open=false",
    ):
        assert "sk_live_123" not in api("GET", url, **kw).text
    stored = records.changes[-1].setting
    assert isinstance(stored, MethodSecret)
    assert Vault(KEY).open(stored) == "sk_live_123"


def test_without_a_key_no_secret_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROPFIRM_SECRETS_KEY", raising=False)
    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    api("PUT", "/admin/payment-methods/card", card(), user="admin-1", **kw)
    api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-a", **kw)
    refused = api("PUT", "/admin/payment-methods/card/secrets/api_key",
                  {"value": "x", "reason": "r"}, user="admin-1", **kw)  # fmt: skip
    assert (refused.status_code, refused.json()["detail"]["code"]) == (503, "no_secrets_key")


def test_once_methods_are_attached_a_purchase_names_an_enabled_one() -> None:
    from tests.auth_rig import FakeCustomers

    records = MemoryRecords()
    proposals = MemoryProposals(records)
    kw: dict[str, Any] = {"records": records, "proposals": proposals, "tiers": OWNERS}
    api("PUT", "/admin/payment-methods/card", card(max_cents=2_000), user="admin-1", **kw)
    api("POST", "/admin/settings/proposals/1/approve", {"reason": "ok"}, user="owner-a", **kw)
    trader: dict[str, Any] = {**kw, "roles": ("trader",), "user": "u1",
                              "customers": FakeCustomers(cifs={"u1": "CIF00000001"})}  # fmt: skip
    order = {"product_id": "one-step", "size_cents": 2_500_000}
    missing = api("POST", "/orders", order, **trader)
    assert (missing.status_code, missing.json()["detail"]["code"]) == (422, "method")
    over = api("POST", "/orders", {**order, "method": "card"}, **trader)
    assert (over.status_code, over.json()["detail"]["code"]) == (422, "method_limits")
    assert Tier.OWNER  # the approver of every method change
