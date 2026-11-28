"""The public leaderboard end to end: a funded account appears with its
gain; the trader can hide it."""

import asyncio

import asyncpg
import pytest

from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration


def test_a_funded_account_is_ranked_and_its_trader_can_opt_out(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            await funded_instant(p)  # Instant, in profit after one trade
            board = (await p.api.get("/leaderboard")).json()  # public: no sign-in
            (row,) = board["rows"]
            assert (row["rank"], row["name"], row["country"], row["product_id"]) == (
                1,
                "Ada T.",
                "DE",
                "instant",
            )
            assert row["gain_cents"] > 0 and row["gain_basis_points"] > 0
            assert (await p.call("GET", "/me/leaderboard")).json() == {"visible": True}
            hidden = await p.call("PUT", "/me/leaderboard", body={"visible": False})
            assert hidden.json() == {"visible": False}
            assert (await p.call("GET", "/me/leaderboard")).json() == {"visible": False}
            shown = await p.call("PUT", "/me/leaderboard", body={"visible": True})
            assert shown.json() == {"visible": True}
            again = await p.call("PUT", "/me/leaderboard", body={"visible": True})
            assert again.json() == {"visible": True}
            # each change of mind is a record, never a deletion (rule AC27);
            # repeating the current choice adds nothing
            conn = await asyncpg.connect(database_url)
            try:
                choices = await conn.fetch(
                    f"SELECT visible FROM {pg_schema}_c.leaderboard_choices ORDER BY id"
                )
            finally:
                await conn.close()
            assert [r["visible"] for r in choices] == [False, True]

    asyncio.run(scenario())
