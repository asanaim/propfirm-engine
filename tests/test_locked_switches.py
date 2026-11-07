"""Rule AC16: security switches locked in production."""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from propfirm.policy import (
    DEFAULT_POLICY,
    LOCKED_SWITCHES,
    PolicyError,
    load_policy,
    parse_policy,
    switch_value,
)

ROOT = Path(__file__).resolve().parents[1]


def raw() -> dict[str, Any]:
    with DEFAULT_POLICY.open("rb") as fh:
        return tomllib.load(fh)


def test_the_shipped_policy_has_every_switch_at_its_locked_value() -> None:
    policy = load_policy()
    assert {s.name: switch_value(policy, s.name) for s in LOCKED_SWITCHES} == {
        s.name: s.locked_at for s in LOCKED_SWITCHES
    }


@pytest.mark.parametrize("switch", LOCKED_SWITCHES, ids=lambda s: s.name)
def test_the_policy_file_cannot_turn_a_switch_the_other_way(switch: Any) -> None:
    data = raw()
    table, key = switch.name.split(".")
    data[table][key] = not switch.locked_at
    with pytest.raises(PolicyError, match=r"AC16|AC00"):
        parse_policy(data)


def test_every_switch_explains_itself() -> None:
    assert all(len(s.explains) > 20 for s in LOCKED_SWITCHES)


def test_logins_keep_a_strong_password_policy_and_lockout() -> None:
    realm = json.loads((DEFAULT_POLICY.parent / "keycloak" / "propfirm-realm.json").read_text())
    assert realm["bruteForceProtected"] is True
    assert realm["sslRequired"] in ("external", "all")
    rules = realm["passwordPolicy"].split(" and ")
    assert "notUsername" in rules and "notEmail" in rules
    length = next(int(r[7:-1]) for r in rules if r.startswith("length("))
    assert length >= 12


def test_the_web_server_asks_browsers_to_stay_on_https() -> None:
    caddy = (ROOT / "web" / "Caddyfile").read_text()
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in caddy
