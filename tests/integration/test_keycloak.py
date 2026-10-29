"""Real Keycloak tokens (development realm) accepted by the API's verifier."""

import asyncio
import os

import httpx
import pytest

from propfirm.api.app import Deps, create_app
from propfirm.api.auth import AuthError, JwksKeys, TokenVerifier
from tests.auth_rig import FakeCustomers

pytestmark = pytest.mark.integration

USERS = {
    "trader1@example.com": "dev-trader-password",
    "staff1@example.com": "dev-staff-password",
}


async def password_token(http: httpx.AsyncClient, base: str, user: str) -> str:
    response = await http.post(
        f"{base}/realms/propfirm/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "dev-cli",
            "username": user,
            "password": USERS[user],
        },
    )
    response.raise_for_status()
    token: str = response.json()["access_token"]
    return token


def test_keycloak_tokens_verify_with_roles(keycloak_url: str) -> None:
    issuer = os.environ.get("KEYCLOAK_ISSUER", f"{keycloak_url}/realms/propfirm")

    async def scenario() -> None:
        async with httpx.AsyncClient() as http:
            certs = f"{keycloak_url}/realms/propfirm/protocol/openid-connect/certs"
            verifier = TokenVerifier(issuer, "propfirm-api", JwksKeys(certs, http))
            trader = await verifier.verify(
                await password_token(http, keycloak_url, "trader1@example.com")
            )
            staff = await verifier.verify(
                await password_token(http, keycloak_url, "staff1@example.com")
            )
            assert trader.roles == {"trader"} and trader.email == "trader1@example.com"
            assert staff.roles == {"trader", "staff"}
            wrong_audience = TokenVerifier(issuer, "someone-else", JwksKeys(certs, http))
            with pytest.raises(AuthError):
                await wrong_audience.verify(
                    await password_token(http, keycloak_url, "trader1@example.com")
                )

            raw = await password_token(http, keycloak_url, "trader1@example.com")
            deps = Deps(None, None, None, verifier, FakeCustomers())  # type: ignore[arg-type]
            transport = httpx.ASGITransport(app=create_app(deps))
            async with httpx.AsyncClient(transport=transport, base_url="http://api") as api:
                me = await api.get("/me", headers={"Authorization": f"Bearer {raw}"})
                assert me.status_code == 404  # signed in, not registered yet
                assert (await api.get("/me")).status_code == 401

    asyncio.run(scenario())
