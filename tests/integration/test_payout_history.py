"""Payout eligibility reads the account's whole projected history for the
consistency re-entry merge (D-14.30). Every event other than a position
open or close must be skipped: the first version read them all and failed
on any funded account with the consistency rule once its history was
projected."""

import asyncio

import pytest

from tests.integration.test_cases import funded_instant
from tests.integration.test_corrections import project
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration


def test_eligibility_with_a_projected_history(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            acc = await funded_instant(p)  # Instant: best day at most 50 %
            await project(p, database_url)
            view = await p.call("GET", f"/accounts/{acc}/payout")
            assert view.status_code == 200, view.text
            names = [c["name"] for c in view.json()["checks"]]
            assert "consistency" in names

    asyncio.run(scenario())
