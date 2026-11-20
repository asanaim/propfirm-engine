"""D-14.19: a token works only on the app it was issued to. A portal token
is refused on staff endpoints and a back-office token on trader endpoints;
with no binding configured (local tools) both pass as before."""

import asyncio

import httpx
import pytest

from propfirm.api.app import Deps, create_app
from propfirm.api.auth import AppClients
from propfirm.settings.service import Settings
from tests.auth_rig import FakeCustomers, bearer, token, verifier
from tests.test_settings import BASE, T0, Engine, MemoryAcceptances, MemoryRecords

CLIENTS = AppClients(frozenset({"portal"}), frozenset({"backoffice"}))


def call(
    url: str, roles: tuple[str, ...], azp: str | None, clients: AppClients | None = CLIENTS
) -> httpx.Response:
    records = MemoryRecords()
    settings = Settings(BASE, records, Engine(), acceptances=MemoryAcceptances())
    deps = Deps(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        Engine(),
        verifier(),
        FakeCustomers(cifs={"u1": "CIF00000001"}),
        policy=settings,
        settings=settings,
        clock=lambda: T0,
        app_clients=clients,
    )
    app = create_app(deps)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await client.get(url, headers=bearer("u1", roles, azp=azp))

    return asyncio.run(go())


def wrong_app(r: httpx.Response) -> bool:
    return r.status_code == 403 and r.json()["detail"]["code"] == "wrong_app"


STAFF_URL = "/admin/offering"  # settings view
TRADER_URL = "/me/retakes"


@pytest.mark.parametrize("azp", ["portal", None, "dev-cli", "someone-else"])
def test_staff_endpoints_refuse_any_token_not_from_the_back_office(azp: str | None) -> None:
    refused = call(STAFF_URL, ("admin",), azp)
    assert wrong_app(refused)
    assert "trader portal" in refused.json()["detail"]["message"]


def test_the_back_office_token_reaches_staff_endpoints() -> None:
    assert call(STAFF_URL, ("admin",), "backoffice").status_code == 200


@pytest.mark.parametrize("azp", ["backoffice", None, "someone-else"])
def test_trader_endpoints_refuse_any_token_not_from_the_portal(azp: str | None) -> None:
    refused = call(TRADER_URL, ("trader",), azp)
    assert wrong_app(refused)
    assert "back office" in refused.json()["detail"]["message"]


def test_the_portal_token_reaches_trader_endpoints() -> None:
    assert not wrong_app(call(TRADER_URL, ("trader",), "portal"))


def test_without_binding_both_pass_as_before() -> None:
    assert call(STAFF_URL, ("admin",), "portal", clients=None).status_code == 200
    assert not wrong_app(call(TRADER_URL, ("trader",), "backoffice", clients=None))


def test_the_client_comes_from_azp() -> None:
    async def go() -> tuple[str | None, str | None]:
        a = await verifier().verify(token(azp="portal"))
        b = await verifier().verify(token())
        return a.client, b.client

    assert asyncio.run(go()) == ("portal", None)


def test_the_shipped_clients() -> None:
    from propfirm.service.config import ServiceConfig, _clients

    defaults = ServiceConfig("db", "valkey")
    assert defaults.oidc_portal_clients == ("portal", "dev-cli")
    assert defaults.oidc_office_clients == ("backoffice", "dev-cli")
    assert _clients(" portal , ,x ") == ("portal", "x")
    with pytest.raises(ValueError):
        _clients(" , ")
