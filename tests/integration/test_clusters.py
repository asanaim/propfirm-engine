"""Identity clusters through real records (D-14.27): a shared wallet, a
duplicate identity and matching trades link customers; a wallet link
opens a case; the reserve counts a cluster as one trader."""

import asyncio
from typing import Any

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_corrections import project
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
# pragma: allowlist nextline secret
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRADE = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 10}


def test_clusters(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            assert p.clusters is not None and p.cases is not None
            market = Market(p)
            await market.price(110_000)
            for i, who in enumerate(("u1", "u2", "u3", "u4"), start=1):
                await p.call("POST", "/me", who, {**ADULT, "full_name": f"Trader {who}"})
                order = (await p.call("POST", "/orders", who, LITE_10K)).json()
                await p.call("POST", order["pay_url"], who)
                acc = f"ACC0000000{i}"
                await eventually(lambda: p.live.account(acc))  # noqa: B023 awaited at once

            # u1 and u2: one wallet
            for who in ("u1", "u2"):
                made = await p.call("POST", "/me/wallet", who, {"address": USDT})
                assert made.status_code == 201, made.text
            # u2 and u3: the same verified person (the funding check's alert)
            await p.cases.alert(
                "CIF00000003", None, "duplicate_identity", "fraud", "medium",
                {"onboarding": "ONB00000009", "same_identity_as": ["CIF00000002"]}, now_ms(),
            )  # fmt: skip
            # u4 copies nobody yet: 4 matching opens are not enough
            for _ in range(4):
                for acc in ("ACC00000001", "ACC00000004"):
                    opened = await p.call("POST", f"/accounts/{acc}/orders", f"u{acc[-1]}", TRADE)
                    assert opened.json()["status"] == "done", opened.text
            await project(p, database_url)
            assert await p.clusters.link(now_ms(), every_ms=0) == 2  # wallet, identity
            assert await p.clusters.link(now_ms(), every_ms=0) == 0  # nothing new

            listed = (await p.call("GET", "/staff/clusters", "admin-1", None, ADMIN)).json()
            assert [c["members"] for c in listed] == [["CIF00000001", "CIF00000002", "CIF00000003"]]
            reasons = sorted((x["a_cif"], x["b_cif"], x["reason"]) for x in listed[0]["links"])
            assert reasons == [
                ("CIF00000001", "CIF00000002", "wallet"),
                ("CIF00000002", "CIF00000003", "identity"),
            ]
            # a shared wallet opens a case on both customers
            conn = await asyncpg.connect(database_url)
            try:
                raised = await conn.fetch(
                    f"SELECT cif, detail->>'linked_to' AS other "
                    f"FROM {p.config.customer_schema}.alerts WHERE source = 'cluster' ORDER BY cif"
                )
            finally:
                await conn.close()
            assert [(r["cif"], r["other"]) for r in raised] == [
                ("CIF00000001", "CIF00000002"),
                ("CIF00000002", "CIF00000001"),
            ]

            # the fifth matching open links u1 and u4 by their trades
            for acc in ("ACC00000001", "ACC00000004"):
                await p.call("POST", f"/accounts/{acc}/orders", f"u{acc[-1]}", TRADE)
            await project(p, database_url)
            assert await p.clusters.link(now_ms(), every_ms=0) == 1
            mine: Any = (
                await p.call("GET", "/staff/customers/CIF00000004/cluster", "admin-1", None, ADMIN)
            ).json()
            assert mine["cluster"] == "CIF00000001"
            assert mine["members"] == ["CIF00000001", "CIF00000002", "CIF00000003", "CIF00000004"]
            trades = next(x for x in mine["links"] if x["reason"] == "trades")
            assert trades["evidence"] == {"matches": 5, "symbols": ["EUR/USD"]}
            alone = await p.call(
                "GET", "/staff/customers/CIF00000009/cluster", "admin-1", None, ADMIN
            )
            assert alone.json() == {"cluster": None, "members": ["CIF00000009"], "links": []}
            refused = await p.call("GET", "/staff/clusters", "u1")
            assert refused.status_code == 403

    asyncio.run(scenario())
