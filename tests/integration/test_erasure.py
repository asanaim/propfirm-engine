"""Erasure of personal data through the API (rule AC27; D-15.5): owners
only, two of them; refused while the data must be kept; the records stay,
their personal data is overwritten, and nothing is deleted."""

import asyncio
import json

import asyncpg
import pytest

from propfirm.customers.erasure import retained_until
from tests.integration.test_customers import ADULT, platform
from tests.integration.test_settings import ADMIN, OWNERS

pytestmark = pytest.mark.integration

T0 = 1_793_491_200_000  # 2026-11-01 00:00 UTC
WALLET = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"


def test_erasure(database_url: str, valkey_url: str, pg_schema: str) -> None:
    now = [T0]

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            owners=OWNERS,
            clock=lambda: now[0],
            start_engine=False,
        ) as p:
            assert (await p.call("POST", "/me", body=ADULT)).status_code == 201
            s = f"{pg_schema}_c"
            conn = await asyncpg.connect(database_url)
            try:
                first = await conn.fetchrow(f"SELECT cif, email FROM {s}.cif")
                assert first is not None
                cif, email = first["cif"], first["email"]
                await conn.execute(
                    f"INSERT INTO {s}.wallets VALUES ('WAL00000001', $1, 'TRC20', $2, $3, "
                    f"'u1', '{{}}')",
                    cif,
                    WALLET,
                    T0,
                )
                await conn.execute(
                    f"INSERT INTO {s}.sign_ins VALUES ($1, $2, 'dev-a', 'DE', 525, 134), "
                    f"($1, $2, 'dev-b', 'DE', 525, 134)",
                    cif,
                    T0,
                )
                version = (await p.api.get("/subscribe")).json()["version"]
                sub = {"email": email, "source": "footer", "version": version}
                assert (await p.api.post("/subscribers", json=sub)).status_code == 202

                ask = {"cif": cif, "reason": "erasure request by email"}
                admin = await p.call("POST", "/admin/erasures", "admin-1", ask, ADMIN)
                assert admin.status_code == 403
                early = await p.call("POST", "/admin/erasures", "owner-a", ask, ())
                assert early.status_code == 409, early.text
                assert "kept until" in early.json()["detail"]["message"]

                now[0] = retained_until(T0, 5)  # five full years after the last activity
                made = await p.call("POST", "/admin/erasures", "owner-a", ask, ())
                assert made.status_code == 201, made.text
                eid = made.json()["id"]
                again = await p.call("POST", "/admin/erasures", "owner-b", ask, ())
                assert again.json()["detail"]["code"] == "pending"
                url = f"/admin/erasures/{eid}/decision"
                yes = {"decision": "approve", "reason": "retention ended"}
                own = await p.call("POST", url, "owner-a", yes, ())
                assert own.json()["detail"]["code"] == "same_person"

                done = await p.call("POST", url, "owner-b", yes, ())
                assert done.status_code == 200, done.text
                erased = done.json()["erased"]
                assert erased["cif"] == 1 and erased["wallets"] == 1
                assert erased["sign_ins"] == 2 and erased["subscriber_events"] == 1

                person = await conn.fetchrow(f"SELECT * FROM {s}.cif WHERE cif = $1", cif)
                assert person is not None
                assert person["full_name"] == "Erased" and email not in str(dict(person))
                assert person["status"] == "blocked"
                assert person["residence_country"] == "DE"  # names nobody alone
                walls = await conn.fetch(f"SELECT address FROM {s}.wallets")
                assert [w["address"] for w in walls] == ["erased"]
                devices = await conn.fetch(f"SELECT device, country FROM {s}.sign_ins ORDER BY 1")
                assert [tuple(d) for d in devices] == [("erased:1", None), ("erased:2", None)]
                subs = await conn.fetch(f"SELECT email FROM {s}.subscriber_events")
                assert {r["email"] for r in subs} == {f"erased:{cif}"}

                listed = (await p.call("GET", "/admin/erasures", "owner-a", None, ())).json()
                assert listed[0]["status"] == "approved" and listed[0]["erased"] == erased
                twice = await p.call("POST", "/admin/erasures", "owner-a", ask, ())
                assert twice.json()["detail"]["message"] == "already erased"

                # the run is recorded: naming it again opens nothing, and
                # nothing is ever deleted
                async with conn.transaction():
                    await conn.execute("SELECT set_config('propfirm.erasure', $1, true)", str(eid))
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(f"UPDATE {s}.wallets SET address = 'x'")
                for sql in (
                    f"UPDATE {s}.wallets SET address = 'erased'",
                    f"DELETE FROM {s}.cif",
                    f"DELETE FROM {s}.erasures",
                    f"UPDATE {s}.erasure_runs SET erased = '{{}}'",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(sql)
                run = await conn.fetchval(f"SELECT erased FROM {s}.erasure_runs")
                assert json.loads(run) == erased
            finally:
                await conn.close()

    asyncio.run(scenario())


def test_an_open_case_or_rejection_keeps_the_data(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    now = [T0]

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            owners=OWNERS,
            clock=lambda: now[0],
            start_engine=False,
        ) as p:
            assert (await p.call("POST", "/me", body=ADULT)).status_code == 201
            cif = "CIF00000001"
            now[0] = retained_until(T0, 5)
            ask = {"cif": cif, "reason": "erasure request"}
            made = await p.call("POST", "/admin/erasures", "owner-a", ask, ())
            eid = made.json()["id"]
            url = f"/admin/erasures/{eid}/decision"
            cancel = {"decision": "cancel", "reason": "wrong customer"}
            assert (await p.call("POST", url, "owner-b", cancel, ())).status_code == 403
            no = {"decision": "reject", "reason": "dispute expected"}
            assert (await p.call("POST", url, "owner-b", no, ())).json()["erased"] is None
            assert (await p.call("POST", url, "owner-a", cancel, ())).status_code == 409
            unknown = await p.call("POST", "/admin/erasures/999/decision", "owner-b", no, ())
            assert unknown.status_code == 404
            missing = {"cif": "CIF99999999", "reason": "no such person"}
            assert (await p.call("POST", "/admin/erasures", "owner-a", missing, ())).json()[
                "detail"
            ]["message"] == "no customer by that number"
            name = await p.call("GET", "/me", "u1")
            assert name.json()["full_name"] == "Ada Trader"  # nothing was overwritten

    asyncio.run(scenario())
