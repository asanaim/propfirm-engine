"""Signed test tokens and a stand-in customer directory for API tests."""

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from propfirm.api.auth import StaticKeys, TokenVerifier
from propfirm.customers.store import AccountRecord, CifRecord, OrderRecord

ISSUER = "http://localhost:8080/realms/propfirm"
AUDIENCE = "propfirm-api"
KID = "test-key"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def verifier() -> TokenVerifier:
    return TokenVerifier(ISSUER, AUDIENCE, StaticKeys({KID: KEY.public_key()}))


def token(
    subject: str = "user-1",
    roles: tuple[str, ...] = ("trader",),
    email: str | None = "user-1@example.com",
    *,
    expires_in: int = 300,
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    key: Any = KEY,
    kid: str = KID,
    algorithm: str = "RS256",
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "email": email,
        "realm_access": {"roles": list(roles)},
    }
    for name in drop:
        claims.pop(name, None)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def bearer(subject: str = "user-1", roles: tuple[str, ...] = ("trader",)) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(subject, roles, f'{subject}@example.com')}"}


@dataclass
class FakeCustomers:
    """subject -> CIF, and ACC -> CIF."""

    cifs: dict[str, str] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)

    def record(self, subject: str) -> CifRecord:
        return CifRecord(
            cif=self.cifs[subject],
            subject=subject,
            email=f"{subject}@example.com",
            full_name="Test Person",
            date_of_birth=date(1990, 1, 1),
            residence_country="DE",
            status="registered",
            created_at=0,
        )

    async def cif_by_subject(self, subject: str) -> CifRecord | None:
        return self.record(subject) if subject in self.cifs else None

    async def account_owner(self, acc: str) -> str | None:
        return self.owners.get(acc)

    async def accounts_for(self, cif: str) -> list[AccountRecord]:
        return [
            AccountRecord(acc, cif, f"ORD-{acc}", "lite", "lite-s1-v1", 1_000_000, 0, "s1")
            for acc, owner in self.owners.items()
            if owner == cif
        ]

    async def orders_for(self, cif: str) -> list[OrderRecord]:
        return []

    async def order(self, ord_id: str) -> OrderRecord | None:
        return None
