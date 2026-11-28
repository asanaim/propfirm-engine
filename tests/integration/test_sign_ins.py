"""Account sharing through the API (D-14.35): sign ins are recorded once
each without the IP address; a fourth device in a week and two places
1,800 km apart in minutes raise alerts on the customer's case and ask for a
step up before the next wallet change."""

import asyncio

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, platform
from tests.integration.test_payouts import USDT

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
DHAKA = {"X-Country": "BD", "X-Latitude": "23.8103", "X-Longitude": "90.4125"}
DELHI = {"X-Country": "IN", "X-Latitude": "28.6139", "X-Longitude": "77.2090"}


def test_account_sharing(database_url: str, valkey_url: str, pg_schema: str) -> None:
    offset = [0]

    def clock() -> int:
        return now_ms() + offset[0]

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, clock=clock) as p:
            await p.call("POST", "/me", "u1", ADULT)

            async def seen(headers: dict[str, str], device: str, age_s: int) -> int:
                h = headers | {"X-Device-Id": device}
                r = await p.call("GET", "/me", "u1", login_age_s=age_s, headers=h)
                return r.status_code

            # three devices from Dhaka, the same sign in seen twice
            for i, age in enumerate((3_000, 2_000, 1_000)):
                assert await seen(DHAKA, f"device-{i}", age) == 200
            assert await seen(DHAKA, "device-0", 3_000) == 200

            conn = await asyncpg.connect(database_url)
            s = p.config.customer_schema
            try:
                rows = await conn.fetch(f"SELECT * FROM {s}.sign_ins ORDER BY at")
                assert len(rows) == 3
                assert rows[0]["country"] == "BD" and (rows[0]["lat"], rows[0]["lon"]) == (238, 904)
                assert len(rows[0]["device"]) == 32  # a hash, not the id
                assert "ip" not in rows[0]
                assert await conn.fetch(f"SELECT * FROM {s}.alerts") == []

                # a fourth device from Delhi 16 minutes later: two rules at once
                assert await seen(DELHI, "device-3", 40) == 200
                alerts = await conn.fetch(
                    f"SELECT severity, detail->>'rule' AS rule FROM {s}.alerts ORDER BY severity"
                )
                assert sorted((a["rule"], a["severity"]) for a in alerts) == [
                    ("devices", "medium"),
                    ("travel", "high"),
                ]
            finally:
                await conn.close()

            # the next wallet change needs a second factor entered after that
            offset[0] = 2_000
            stale = await p.call("POST", "/me/wallet", body={"address": USDT}, login_age_s=5)
            assert stale.status_code == 401, stale.text
            assert "max_age=0" in stale.headers["WWW-Authenticate"]
            fresh = await p.call("POST", "/me/wallet", body={"address": USDT})
            assert fresh.status_code == 201, fresh.text

            listed = await p.call(
                "GET", "/staff/customers/CIF00000001/sign-ins", "admin-1", None, ADMIN
            )
            body = listed.json()
            assert len(body["sign_ins"]) >= 4 and body["step_up_since"] is not None

    asyncio.run(scenario())
