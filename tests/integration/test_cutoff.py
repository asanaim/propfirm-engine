"""Leavers' logins against PostgreSQL and the development Keycloak."""

import asyncio
import uuid

import httpx
import pytest

from propfirm.service.feeder import now_ms
from propfirm.staff.cutoff import Cutoffs, IdentityError, KeycloakAdmin
from propfirm.staff.directory import Actor, Owner
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
H = 3_600_000
OK = {"reason": "ok"}
PASSWORD = "pw-leaver-longer-1"  # the realm asks for 12 characters


class Down:
    name = "down"

    async def disable(self, subject: str) -> None:
        raise IdentityError("HTTP 503")


def test_a_leaver_loses_the_login_at_once_or_leads_are_alerted(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    async def scenario() -> None:
        def clock() -> int:
            return now_ms() + offset["ms"]

        async with platform(database_url, valkey_url, pg_schema, start_engine=False, clock=clock,
                            owners=OWNERS) as p:  # fmt: skip
            assert p.identity is not None and p.cutoffs is not None and p.staff is not None
            body = {"subject": "kc-spec", "name": "Spec", "kind": "staff", "tier": 2,
                    "reason": "hired"}  # fmt: skip
            joined = (await p.call("POST", "/staff/members", "kc-owner-a", body, ())).json()
            for g in joined["grants"]:
                await p.call("POST", f"/staff/grants/{g['grt']}/approve", "kc-owner-b", OK, ())
            sid = joined["sid"]
            left = await p.call("POST", f"/staff/members/{sid}/leave", "kc-owner-a", OK, ())
            assert left.status_code == 200, left.text
            assert p.identity.disabled == ["kc-spec"]  # at once, not at the next round
            (done,) = (await p.call("GET", "/staff/cutoffs", "kc-owner-a", None, ())).json()
            assert (done["sid"], done["status"], done["how"]) == (sid, "done", "sandbox")

            # the provider is down: retried, then leads and admins are alerted
            down = Cutoffs(p.staff.store, Down(), p.desk)
            body = {**body, "subject": "kc-spec2"}
            joined = (await p.call("POST", "/staff/members", "kc-owner-a", body, ())).json()
            sid2 = joined["sid"]
            for g in joined["grants"]:
                await p.call("POST", f"/staff/grants/{g['grt']}/approve", "kc-owner-b", OK, ())
            await p.staff.leave(Actor("kc-owner-a"), sid2, "left", clock())
            await down.advance(clock())
            await down.advance(clock())
            assert p.desk is not None
            assert await p.desk.open_for(f"cutoff:{sid2}") == []  # not late yet
            offset["ms"] = H
            await down.advance(clock())
            await down.advance(clock())  # alerted once
            (alert,) = await p.desk.open_for(f"cutoff:{sid2}")
            listed = (await p.call("GET", "/staff/cutoffs", "kc-owner-a", None, ())).json()
            late = next(c for c in listed if c["sid"] == sid2)
            assert (late["status"], late["last_error"]) == ("late", "IdentityError: HTTP 503")
            offset["ms"] = 24 * H
            await down.advance(clock())
            assert len(await p.desk.open_for(f"cutoff:{sid2}")) == 2  # past the hard limit
            confirmed = await p.call("POST", f"/staff/cutoffs/{sid2}/done", "kc-owner-a", OK, ())
            assert confirmed.json()["status"] == "done", confirmed.text
            assert confirmed.json()["how"] == "by hand: kc-owner-a"
            assert await p.desk.open_for(f"cutoff:{sid2}") == []
            assert alert > 0

    asyncio.run(scenario())


def test_keycloak_disables_the_login_and_ends_its_sessions(keycloak_url: str) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient() as http:
            admin = KeycloakAdmin(http, keycloak_url, "propfirm", "staff-cutoff",
                                  "dev-staff-cutoff-secret")  # fmt: skip
            token = await admin._token()
            headers = {"Authorization": f"Bearer {token}"}
            name = f"leaver-{uuid.uuid4().hex[:8]}@example.com"
            users = f"{keycloak_url}/admin/realms/propfirm/users"
            created = await http.post(users, headers=headers, json={
                "username": name, "email": name, "enabled": True, "emailVerified": True,
                "firstName": "Leaver", "lastName": "Test",
                "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
            })  # fmt: skip
            assert created.status_code == 201, created.text
            subject = created.headers["Location"].rsplit("/", 1)[1]
            login = {"grant_type": "password", "client_id": "dev-cli", "username": name,
                     "password": PASSWORD}  # fmt: skip
            token_url = f"{keycloak_url}/realms/propfirm/protocol/openid-connect/token"
            assert (await http.post(token_url, data=login)).status_code == 200
            await admin.disable(subject)
            assert (await http.post(token_url, data=login)).status_code in (400, 401)
            user = (await http.get(f"{users}/{subject}", headers=headers)).json()
            assert user["enabled"] is False
            await admin.disable(str(uuid.uuid4()))  # an unknown login: nothing to end
            await http.delete(f"{users}/{subject}", headers=headers)

    asyncio.run(scenario())
