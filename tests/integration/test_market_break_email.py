"""The market break rule end to end (D-13.23): the engine closes a funded
account's trades before a long closure, and the trader is emailed once."""

import asyncio
from dataclasses import replace

import pytest

from propfirm.journal.inputs import UpdateTrading
from propfirm.market.types import PriceUpdate
from propfirm.service.feeder import now_ms
from propfirm.trading.hours import ALWAYS
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import eventually, platform

pytestmark = pytest.mark.integration


def test_trades_closed_before_a_break_are_emailed_once(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            acc = await funded_instant(p)
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
            opened = (await p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
            assert opened["status"] == "done", opened

            # EUR/USD closes for 3 hours starting 5 minutes and 2 seconds from now:
            # its flat-by time is 2 seconds away
            start = now_ms() + 5 * 60_000 + 2_000
            policy = await p.settings.current()
            instruments = tuple(
                replace(
                    i,
                    hours=replace(
                        ALWAYS, closures=((start, start + 3 * 3_600_000),), flat_before_minutes=5
                    ),
                )
                for i in policy.trading.instruments
                if i.name == "EUR/USD"
            )
            await p.writer.publish(UpdateTrading("trading-break-test", instruments, None, now_ms()))
            await asyncio.sleep(2.5)
            t = now_ms()
            await p.writer.publish(PriceUpdate("EUR/USD", 10_000, t, t, 110_000))

            breaks = p.breaks
            assert breaks is not None

            async def written() -> list[str]:
                return await breaks.sweep(now_ms(), await p.settings.notices())

            (ntf,) = await eventually(written)
            assert await breaks.sweep(now_ms()) == []  # never twice
            assert p.notifications is not None and p.mailer is not None
            await p.notifications.advance(now_ms())
            ((recipient, subject, body),) = [m for m in p.mailer.sent if "market break" in m[1]]
            assert recipient == "u1@example.com"
            assert subject == f"Account {acc}: trades closed before the market break"
            assert "EUR/USD buy 1.00 lot: closed" in body and "not a rule breach" in body
            assert ntf.startswith("NTF")
            account = await p.live.account(acc)
            assert account is not None and not account["positions"]

    asyncio.run(scenario())
