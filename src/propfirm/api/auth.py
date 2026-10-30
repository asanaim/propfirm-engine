"""Who is calling: Keycloak access tokens, verified locally.

Every request except the public ones (/health, /catalogue, /access) carries
`Authorization: Bearer <access token>` from Keycloak. The API checks the
token itself: RS256 signature against the realm's published keys, issuer,
audience ("propfirm-api"), expiry and subject. Roles come from the realm
roles in the token: `trader` (default for self-registered users), `staff`
(employees) and `admin`; what each role may see is decided in
`propfirm.api.app`.

Keys are fetched from the realm's JWKS address and cached; an unknown key
id triggers one refetch (Keycloak key rotation), at most every 30 seconds,
so a stream of forged key ids cannot hammer Keycloak.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

ALGORITHMS = ["RS256"]
LEEWAY_S = 30  # clock difference tolerated between Keycloak and the API


class AuthError(Exception):
    """The request carries no valid access token."""


# `amr` values (RFC 8176, and Keycloak's) that prove a second factor; the
# realm must add the claim to access tokens (an "amr" mapper).
SECOND_FACTORS = frozenset({"otp", "mfa", "hwk", "swk", "webauthn", "sms"})


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str  # Keycloak user id: the login identity, mapped to one CIF
    email: str | None
    roles: frozenset[str]
    mfa: bool = False  # the login used a second factor (token claim `amr`)
    auth_time: int | None = None  # ms UTC: when the user last authenticated (`auth_time`)

    def has(self, role: str) -> bool:
        return role in self.roles


class KeySource(Protocol):
    async def key(self, kid: str) -> Any: ...


class StaticKeys:
    """Fixed keys (tests, or keys pinned by configuration)."""

    def __init__(self, keys: Mapping[str, Any]) -> None:
        self._keys = dict(keys)

    async def key(self, kid: str) -> Any:
        found = self._keys.get(kid)
        if found is None:
            raise AuthError("unknown signing key")
        return found


class JwksKeys:
    """The realm's published keys, cached; refetched for an unknown key id."""

    def __init__(self, url: str, client: httpx.AsyncClient, min_refresh_s: float = 30) -> None:
        self._url = url
        self._client = client
        self._keys: dict[str, Any] = {}
        self._fetched_at = float("-inf")
        self._min_refresh_s = min_refresh_s

    async def key(self, kid: str) -> Any:
        if kid not in self._keys and time.monotonic() - self._fetched_at >= self._min_refresh_s:
            await self._refresh()
        found = self._keys.get(kid)
        if found is None:
            raise AuthError("unknown signing key")
        return found

    async def _refresh(self) -> None:
        self._fetched_at = time.monotonic()
        response = await self._client.get(self._url, timeout=5.0)
        response.raise_for_status()
        keys: dict[str, Any] = {}
        for jwk in response.json().get("keys", []):
            if jwk.get("use", "sig") == "sig" and jwk.get("alg", "RS256") in ALGORITHMS:
                keys[jwk["kid"]] = jwt.PyJWK(jwk).key
        self._keys = keys


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, keys: KeySource) -> None:
        self._issuer = issuer
        self._audience = audience
        self._keys = keys

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed token") from exc
        if header.get("alg") not in ALGORITHMS or not isinstance(header.get("kid"), str):
            raise AuthError("unsupported token")
        key = await self._keys.key(header["kid"])
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise AuthError("token has no subject")
        realm = claims.get("realm_access") or {}
        roles = realm.get("roles") if isinstance(realm, dict) else None
        email = claims.get("email")
        amr = claims.get("amr")
        methods = {m for m in amr if isinstance(m, str)} if isinstance(amr, list) else set()
        raw_time = claims.get("auth_time")
        auth_time: int | None = None
        if isinstance(raw_time, int) and not isinstance(raw_time, bool) and raw_time >= 0:
            auth_time = raw_time * 1000
        return Principal(
            subject=subject,
            email=email if isinstance(email, str) else None,
            roles=frozenset(r for r in roles or () if isinstance(r, str)),
            mfa=bool(methods & SECOND_FACTORS),
            auth_time=auth_time,
        )


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing bearer token")
    return token.strip()
