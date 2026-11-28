"""Extension slots (rule AC22; D-15.3): shapes, provider ids, public view."""

import pytest

from propfirm.policy import PolicyError
from propfirm.settings.extensions import (
    Extension,
    extension_from_json,
    extension_json,
    extensions_in_force,
    public_view,
    secret_owner,
)

CHAT_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def test_each_slot_takes_only_its_providers_id() -> None:
    assert Extension("chat", True, CHAT_ID).provider == "crisp"
    assert Extension("captcha", True, "0x4AAAAAAAAAAAAAAA").provider == "turnstile"
    assert Extension("analytics", True, "a" * 32).provider == "cloudflare"
    assert Extension("chat", False, "").public_id == ""  # off needs no id
    for slot, bad in (
        ("chat", "a" * 32),
        ("captcha", CHAT_ID),
        ("analytics", "A" * 32),
        ("chat", f"{CHAT_ID}\n"),
        ("chat", ""),
    ):
        with pytest.raises(PolicyError):
            Extension(slot, True, bad)
    with pytest.raises(PolicyError):
        Extension("chat", False, "<script>")  # an id given must still be one
    with pytest.raises(PolicyError):
        Extension("ads", True, CHAT_ID)
    with pytest.raises(PolicyError):
        Extension("chat", 1, CHAT_ID)  # type: ignore[arg-type]


def test_json_round_trip_and_the_latest_in_force() -> None:
    e = Extension("chat", True, CHAT_ID)
    assert extension_from_json(extension_json(e)) == e
    newer = Extension("chat", False, CHAT_ID)
    found = extensions_in_force([e, "other", newer])
    assert found == {"chat": newer}


def test_public_view_names_switched_on_slots_only() -> None:
    found = {
        "chat": Extension("chat", True, CHAT_ID),
        "analytics": Extension("analytics", False, "a" * 32),
    }
    assert public_view(found) == {
        "chat": {"provider": "crisp", "public_id": CHAT_ID},
        "captcha": None,
        "analytics": None,
    }
    assert secret_owner("captcha") == "ext-captcha"
