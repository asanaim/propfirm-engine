"""Access tokens: signature, issuer, audience, expiry, subject, key rotation."""

import asyncio
import json
from typing import Any

import httpx
import jwt
import pytest

from propfirm.api.auth import AuthError, JwksKeys, Principal, bearer_token
from tests.auth_rig import KEY, KID, OTHER_KEY, token, verifier


def verify(raw: str) -> Principal:
    return asyncio.run(verifier().verify(raw))


def test_a_valid_token_gives_the_principal() -> None:
    principal = verify(token("abc", ("trader", "staff"), "a@example.com"))
    roles = frozenset({"trader", "staff"})
    assert principal == Principal("abc", "a@example.com", roles, mfa=True)  # amr: pwd, otp
    assert principal.has("staff") and not principal.has("admin")


@pytest.mark.parametrize(
    ("amr", "mfa"),
    [
        (("pwd",), False),
        ((), False),
        (("pwd", "otp"), True),
        (("webauthn",), True),
        (("hwk",), True),
        (("pwd", "OTP"), False),  # exact values only
    ],
)
def test_mfa_comes_from_the_amr_claim(amr: tuple[str, ...], mfa: bool) -> None:
    assert verify(token(amr=amr)).mfa is mfa


def test_a_missing_amr_claim_is_no_mfa() -> None:
    assert verify(token(drop=("amr",))).mfa is False


@pytest.mark.parametrize(
    "raw",
    [
        token(expires_in=-120),  # expired beyond the 30 s leeway
        token(audience="account"),  # not issued for the API
        token(issuer="http://evil.example/realms/propfirm"),
        token(key=OTHER_KEY),  # signed by another key under our key id
        token(kid="unknown"),
        token(drop=("sub",)),
        token(drop=("exp",)),
        token(drop=("aud",)),
        jwt.encode({"sub": "x"}, "s" * 32, algorithm="HS256", headers={"kid": KID}),  # HMAC
        "not-a-token",
        "",
    ],
)
def test_invalid_tokens_are_refused(raw: str) -> None:
    with pytest.raises(AuthError):
        verify(raw)


def test_unsigned_token_is_refused() -> None:
    header = {"alg": "none", "kid": KID}
    good = token()
    payload = good.split(".")[1]
    head = jwt.utils.base64url_encode(json.dumps(header).encode()).decode()
    with pytest.raises(AuthError):
        verify(f"{head}.{payload}.")


def test_bearer_header_parsing() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer  abc ") == "abc"
    for bad in (None, "", "Basic abc", "Bearer", "Bearer   "):
        with pytest.raises(AuthError):
            bearer_token(bad)


def test_jwks_keys_refresh_on_rotation_but_not_on_every_unknown_kid() -> None:
    calls: list[int] = []
    keys: dict[str, Any] = {"keys": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=keys)

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        source = JwksKeys("http://kc/certs", client, min_refresh_s=0)
        with pytest.raises(AuthError):
            await source.key(KID)  # not published yet
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
        keys["keys"] = [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]
        assert await source.key(KID) is not None  # rotated in: one refetch
        assert await source.key(KID) is not None  # cached: no fetch
        assert len(calls) == 2
        limited = JwksKeys("http://kc/certs", client, min_refresh_s=3600)
        for _ in range(5):
            with pytest.raises(AuthError):
                await limited.key("forged")
        assert len(calls) == 3  # one fetch, then rate-limited
        await client.aclose()

    asyncio.run(scenario())
