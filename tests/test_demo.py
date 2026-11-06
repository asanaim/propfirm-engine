"""The demo data's logins (propfirm.demo.seed): never outside the sandbox."""

import asyncio
import base64
import hashlib
import json

import pytest

from propfirm.demo.seed import (
    DEMO_TOTP_BASE32,
    DEMO_TOTP_SECRET,
    PASSWORD,
    demo_id,
    logins,
    password_credential,
    seed,
    totp,
    user_representation,
)
from propfirm.staff.model import ROLES


def test_one_employee_per_level_per_section_plus_admins_owners_and_traders() -> None:
    emails = [u.email for u in logins()]
    assert len(emails) == len(set(emails)) == 2 + 2 + 4 * len(ROLES) + 5
    for section in ROLES:
        for level in ("apprentice", "associate", "specialist", "lead"):
            assert f"{section}-{level}@example.com" in emails
    assert all(u.otp for u in logins() if not u.email.startswith("trader-"))
    assert not any(u.otp for u in logins() if u.email.startswith("trader-"))


def test_ids_are_fixed_so_a_rerun_finds_the_same_people() -> None:
    assert demo_id("owner-a@example.com") == demo_id("owner-a@example.com")
    assert demo_id("owner-a@example.com") != demo_id("owner-b@example.com")


def test_the_password_is_stored_as_keycloak_hashes_it() -> None:
    cred = password_credential(PASSWORD, b"0123456789abcdef")
    secret, data = json.loads(cred["secretData"]), json.loads(cred["credentialData"])
    expected = hashlib.pbkdf2_hmac("sha512", b"12345", b"0123456789abcdef", 210_000, dklen=64)
    assert base64.b64decode(secret["value"]) == expected
    assert (data["algorithm"], data["hashIterations"]) == ("pbkdf2-sha512", 210_000)
    rep = user_representation(logins()[0])
    assert rep["realmRoles"] == ["default-roles-propfirm"]
    assert [c["type"] for c in rep["credentials"]] == ["password", "otp"]


def test_the_authenticator_key_and_codes() -> None:
    padded = DEMO_TOTP_BASE32 + "=" * (-len(DEMO_TOTP_BASE32) % 8)
    assert base64.b32decode(padded) == DEMO_TOTP_SECRET
    # RFC 6238 test vector (SHA-1, T = 59 s): 94287082, last six digits
    assert totp(b"12345678901234567890", 59) == "287082"


def test_refused_outside_the_sandbox() -> None:
    with pytest.raises(SystemExit, match="sandbox"):
        asyncio.run(seed({"DATABASE_URL": "postgresql://x", "VALKEY_URL": "redis://x"}))
