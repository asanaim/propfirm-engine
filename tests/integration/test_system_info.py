"""System information through the API, against PostgreSQL and Valkey (rule AC23)."""

import asyncio

import pytest

from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration


def test_admins_read_what_is_running(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def go() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False) as p:
            got = await p.call("GET", "/admin/system", "admin-1", None, ("admin",))
            assert got.status_code == 200, got.text
            info = got.json()
            assert info["application"]["revision"] == "test-build"
            assert [(x["name"], x["status"]) for x in info["parts"]] == [
                ("PostgreSQL", "ok"),
                ("Valkey", "ok"),
            ]
            assert info["parts"][0]["version"].startswith("PostgreSQL ")
            staff = await p.call("GET", "/admin/system", "staff-1", None, ("staff",))
            assert staff.status_code == 403
            assert (await p.call("GET", "/admin/system")).status_code == 403  # a trader

    asyncio.run(go())
