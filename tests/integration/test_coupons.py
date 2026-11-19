"""Coupons screen (D-14.14): staff create discount codes with an end and a
use limit, switch them, and see their uses and what they gave away."""

import asyncio

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
LITE_25K = {**LITE_10K, "size_cents": 2_500_000}


def test_codes_are_created_limited_switched_and_counted(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            new = {"code": "LAUNCH10", "discount_percent": "10", "max_uses": 1, "reason": "launch"}
            created = await p.call("POST", "/staff/codes", "admin-1", new, ADMIN)
            assert created.status_code == 201, created.text
            assert (created.json()["valid"], created.json()["uses"]) == (True, 0)
            # the same code again, a share above every product's cap, an end in the past
            again = await p.call("POST", "/staff/codes", "admin-1", new, ADMIN)
            high = {**new, "code": "BIG", "discount_percent": "25"}
            past = {**new, "code": "OLD", "expires_at": now_ms() - 1}
            refused = [
                again.status_code,
                (await p.call("POST", "/staff/codes", "admin-1", high, ADMIN)).status_code,
                (await p.call("POST", "/staff/codes", "admin-1", past, ADMIN)).status_code,
                (await p.call("POST", "/staff/codes", "u1", new)).status_code,  # a trader
            ]
            assert refused == [409, 422, 422, 403]

            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body={**LITE_25K, "code": "LAUNCH10"})).json()
            assert order["discount_kind"] == "code"
            await p.call("POST", order["pay_url"])
            listed = (await p.call("GET", "/staff/codes", "admin-1", None, ADMIN)).json()
            assert [
                (c["code"], c["uses"], c["paid"], c["given_cents"], c["valid"]) for c in listed
            ] == [
                ("LAUNCH10", 1, 1, 1_090, False)  # $109 less 10 %; used up
            ]

            # used up: a second order gets no discount
            p2 = {**ADULT, "full_name": "Bea Trader"}
            registered = await p.call("POST", "/me", "u2", p2)
            assert registered.status_code == 201, registered.text
            used_up = await p.call("POST", "/orders", "u2", {**LITE_25K, "code": "LAUNCH10"})
            assert used_up.status_code == 422

            # switched off, then on: every change kept
            off = {"on": False, "reason": "paused"}
            switched = await p.call("POST", "/staff/codes/LAUNCH10/switch", "admin-1", off, ADMIN)
            assert switched.json()["on"] is False
            on = {"on": True, "reason": "back"}
            await p.call("POST", "/staff/codes/LAUNCH10/switch", "admin-2", on, ADMIN)
            shown = (await p.call("GET", "/staff/codes/LAUNCH10", "admin-1", None, ADMIN)).json()
            assert [(c["change"], c["by_login"], c["reason"]) for c in shown["changes"]] == [
                ("created", "admin-1", "launch"),
                ("off", "admin-1", "paused"),
                ("on", "admin-2", "back"),
            ]
            missing = await p.call("POST", "/staff/codes/NONE/switch", "admin-1", off, ADMIN)
            assert missing.status_code == 404

    asyncio.run(scenario())
