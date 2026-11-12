"""Products and sizes on sale, switched by owners or admins (review D4)."""

from dataclasses import replace
from typing import Any

import pytest

from propfirm.policy import (
    CataloguePolicy,
    PolicyError,
    all_sizes,
    check_catalogue,
    instant_funding,
    load_policy,
)
from propfirm.settings.authority import Refused, check_approver, check_proposer
from propfirm.settings.offering import Offering, offering_from_json, offering_json
from propfirm.settings.overlay import SettingChange, apply_changes, from_json, to_json
from propfirm.staff.model import Tier
from tests.test_settings import OWNERS, MemoryProposals, MemoryRecords, _approve, api

POLICY = load_policy()


def test_the_shipped_catalogue_follows_the_owner_decisions() -> None:
    c = POLICY.catalogue
    assert c.products_off == {"classic"}  # K2
    assert c.sizes_off == frozenset()
    assert c.one_instant_funding_per_trader and c.unpaid_hold_hours == 24
    assert POLICY.scaling.max_allocation_cents == 40_000_000  # $400,000
    assert [p.id for p in POLICY.products if c.on_sale(p)] == ["lite", "one-step", "instant"]
    assert [p.id for p in POLICY.products if instant_funding(p)] == ["instant"]
    assert all_sizes(POLICY) == (500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000, 20_000_000)


def test_sizes_off_leave_the_rest_on_sale() -> None:
    lite = POLICY.product("lite")
    c = CataloguePolicy(sizes_off=frozenset({500_000, 20_000_000}))
    assert [s for s, _ in c.sizes(lite)] == [1_000_000, 2_500_000, 5_000_000, 10_000_000]
    every = CataloguePolicy(sizes_off=frozenset(s for s, _ in lite.prices_cents))
    assert not every.on_sale(lite)  # no size left: not on sale


def test_only_existing_products_and_sizes_can_be_switched() -> None:
    check_catalogue(POLICY, frozenset({"lite"}), frozenset({1_000_000}))
    with pytest.raises(PolicyError, match="unknown products"):
        check_catalogue(POLICY, frozenset({"futures"}), frozenset())
    with pytest.raises(PolicyError, match="no product has the sizes"):
        check_catalogue(POLICY, frozenset(), frozenset({40_000_000}))  # 400K: no price yet


def test_the_setting_round_trips_and_replaces_the_files_lists() -> None:
    setting = Offering(frozenset({"instant"}), frozenset({500_000}))
    kind, value = to_json(setting)
    assert (kind, value) == ("offering", {"products_off": ["instant"], "sizes_off": [500_000]})
    assert from_json(kind, value) == setting == offering_from_json(offering_json(setting))
    with pytest.raises(PolicyError):
        offering_from_json({"products_off": ["lite", "lite"], "sizes_off": []})
    change = SettingChange(1, setting, "admin-1", None, "r", 0, "admin-2")
    policy = apply_changes(POLICY, [change])
    assert policy.catalogue.products_off == {"instant"}  # Classic back on sale
    assert policy.catalogue.sizes_off == {500_000}
    assert policy.catalogue.one_instant_funding_per_trader  # the file's rules stay
    # a product the file no longer has is ignored, not an error
    gone = SettingChange(2, Offering(frozenset({"retired"}), frozenset()), "a", None, "r", 0, "b")
    assert apply_changes(POLICY, [gone]).catalogue.products_off == frozenset()


def test_admins_propose_and_a_second_admin_or_owner_approves() -> None:
    setting = Offering(frozenset(), frozenset())
    check_proposer(setting, Tier.ADMIN)
    with pytest.raises(Refused):
        check_proposer(setting, Tier.SPECIALIST)
    assert check_approver(setting, "admin-1", "admin-2", Tier.ADMIN, None) == "admin"
    with pytest.raises(Refused, match="maker"):
        check_approver(setting, "admin-1", "admin-1", Tier.ADMIN, None)


def test_switching_sizes_through_the_api() -> None:
    records = MemoryRecords()
    kw: dict[str, Any] = {
        "records": records,
        "proposals": MemoryProposals(records),
        "tiers": OWNERS,
    }
    shown = api("GET", "/admin/offering", **kw).json()
    assert shown["allocation_cap_cents"] == 40_000_000
    assert [(p["id"], p["on"]) for p in shown["products"]][:2] == [
        ("lite", True),
        ("classic", False),
    ]
    catalogue = api("GET", "/catalogue", signed_in=False, **kw).json()
    assert [p["id"] for p in catalogue] == ["lite", "one-step", "instant"]
    body = {"products_off": ["classic"], "sizes_off": [500_000], "reason": "no 5K"}
    put = api("PUT", "/admin/offering", body, user="admin-1", **kw)
    assert put.status_code == 200, put.text
    assert 500_000 in [s["size_cents"] for s in api("GET", "/catalogue", **kw).json()[0]["sizes"]]
    _approve(kw, put.json()["proposal"]["id"])
    lite = api("GET", "/catalogue", signed_in=False, **kw).json()[0]
    assert 500_000 not in [s["size_cents"] for s in lite["sizes"]]
    bad = {"products_off": [], "sizes_off": [40_000_000], "reason": "x"}
    assert api("PUT", "/admin/offering", bad, user="admin-1", **kw).status_code == 422
    trader = api("PUT", "/admin/offering", body, roles=("trader",), user="trader-1", **kw)
    assert trader.status_code in (401, 403)


def test_the_file_refuses_a_catalogue_naming_nothing_real() -> None:
    with pytest.raises(PolicyError):
        check_catalogue(
            replace(POLICY, products=POLICY.products[:1]), frozenset({"instant"}), frozenset()
        )
