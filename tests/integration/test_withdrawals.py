"""Withdrawal methods and payouts sent by hand (D-14.25): once a method is
on, a payout must name one; its limits are checked and its charge taken
at request time; the manual rail waits for the transfer to be recorded;
the books carry what was sent and the charge kept."""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import ADULT, eventually, platform
from tests.integration.test_payouts import DAY, USDT, _payable, instant_without_consistency
from tests.integration.test_progression import STAFF
from tests.integration.test_settings import OWNERS, two_owners

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def method(**over: Any) -> dict[str, Any]:
    return {
        "kind": "withdrawal",
        "name": "USDT (TRC-20), sent by staff",
        "adapter": "manual",
        "enabled": True,
        "min_cents": 1_000,
        "max_cents": 0,
        "fixed_charge_cents": 100,
        "percent_charge": "1",
        "instructions": "",
        "user_fields": [],
        "reason": "launch",
        **over,
    }


def test_withdrawal_methods_and_manual_payouts(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            instant_without_consistency(),
            funded=True,
            clock=clock,
            wait_s=2.0,
            owners=OWNERS,
        ) as p:
            assert p.payouts is not None and p.books is not None
            acc = await funded_instant(p)
            await eventually(lambda: _payable(p, acc))
            await p.call("POST", "/me/wallet", body={"address": USDT})
            offset["ms"] = 15 * DAY
            amount = (await p.call("GET", f"/accounts/{acc}/payout")).json()["trader_amount_cents"]

            done = await two_owners(p, "/admin/payment-methods/usdt-hand", method())
            assert done.status_code == 200, done.text
            url = f"/accounts/{acc}/payouts"
            bare = await p.call("POST", url)
            assert (bare.status_code, bare.json()["detail"]["code"]) == (422, "choose_method")
            wrong = await p.call("POST", url, body={"method": "card"})
            assert wrong.json()["detail"]["code"] == "choose_method"

            # limits are the method's: one above the payout refuses it
            tight = method(min_cents=amount + 1)
            assert (
                await two_owners(p, "/admin/payment-methods/usdt-hand", tight)
            ).status_code == 200
            low = await p.call("POST", url, body={"method": "usdt-hand"})
            assert (low.status_code, low.json()["detail"]["code"]) == (409, "outside_limits")
            assert (
                await two_owners(p, "/admin/payment-methods/usdt-hand", method())
            ).status_code == 200

            made = await p.call("POST", url, body={"method": "usdt-hand"})
            assert made.status_code == 201, made.text
            payout = made.json()
            charge = 100 + -(-amount // 100)  # $1 fixed + 1 %, rounded up
            assert (payout["method_id"], payout["charge_cents"]) == ("usdt-hand", charge)
            assert payout["net_cents"] == amount - charge
            po = payout["po"]
            for who, roles in (("staff-1", STAFF), ("admin-1", ADMIN)):
                await p.call("POST", f"/staff/payouts/{po}/approve", who, {"reason": "ok"}, roles)
            await p.payouts.advance(clock() + 1)  # debited
            await p.payouts.advance(clock() + 2)  # handed to the manual rail
            record = await p.payouts.store.payout(po)
            assert record is not None and record.status == "sent"
            waiting = (await p.call("GET", "/staff/transfers", "admin-1", None, ADMIN)).json()
            assert [(w["po"], w["net_cents"], w["address"]) for w in waiting] == [
                (po, amount - charge, USDT)
            ]
            await p.payouts.advance(clock() + 3)
            assert (await p.payouts.store.payout(po)).status == "sent"  # type: ignore[union-attr]

            body = {"tx_ref": "a1b2c3"}
            sent = await p.call("POST", f"/staff/payouts/{po}/transfer", "admin-1", body, ADMIN)
            assert sent.status_code == 200, sent.text
            twice = await p.call("POST", f"/staff/payouts/{po}/transfer", "admin-1", body, ADMIN)
            assert (twice.status_code, twice.json()["detail"]["code"]) == (409, "recorded")
            await p.payouts.advance(clock() + 4)
            paid = await p.payouts.store.payout(po)
            assert paid is not None and paid.status == "paid"
            assert (await p.call("GET", "/staff/transfers", "admin-1", None, ADMIN)).json() == []

            # the books: cash out is what was sent; the charge lowers the payout cost
            await p.books.sync(clock(), every_ms=0)
            entries = (await p.call("GET", "/staff/books/entries", "admin-1", None, ADMIN)).json()
            settled = next(e for e in entries if e["entry_id"] == f"payout-paid:{po}")
            postings = {x["account"]: x["amount_cents"] for x in settled["postings"]}
            assert postings == {
                "liability:payouts_payable": amount,
                "asset:cash": -(amount - charge),
                "expense:payouts": -charge,
            }
            drawn = next(e for e in entries if e["entry_id"] == f"reserve-draw:{po}")
            assert {x["account"]: x["amount_cents"] for x in drawn["postings"]} == {
                "asset:cash": amount - charge,
                "asset:reserve": -(amount - charge),
            }
            tb = (await p.call("GET", "/staff/books/trial-balance", "admin-1", None, ADMIN)).json()
            assert tb["total_cents"] == 0

    asyncio.run(scenario())


def test_a_network_opened_by_the_policy_takes_wallets(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    """D-14.26: a closed network refuses new wallets; opened, its addresses
    are checked as its own; the wallet keeps its network."""
    evm = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    async def scenario() -> None:
        base = instant_without_consistency()
        async with platform(database_url, valkey_url, pg_schema, base, funded=True) as p:
            await p.call("POST", "/me", body=ADULT)
            assert [n["code"] for n in (await p.call("GET", "/payout-networks")).json()] == [
                "TRC20"
            ]
            closed = await p.call("POST", "/me/wallet", body={"address": evm, "network": "BEP20"})
            assert (closed.status_code, closed.json()["detail"]["code"]) == (422, "invalid")
        opened = replace(base, payouts=replace(base.payouts, networks=("TRC20", "BEP20")))
        async with platform(database_url, valkey_url, pg_schema, opened, funded=True) as p:
            await p.call("POST", "/me", body=ADULT)
            listed = (await p.call("GET", "/payout-networks")).json()
            assert [n["code"] for n in listed] == ["TRC20", "BEP20"]
            typo = evm[:-1] + "D"  # the checksum catches a wrong case
            bad = await p.call("POST", "/me/wallet", body={"address": typo, "network": "BEP20"})
            assert "0x and 40 characters" in bad.json()["detail"]["message"]
            tron_on_bsc = await p.call(
                "POST", "/me/wallet", body={"address": USDT, "network": "BEP20"}
            )
            assert tron_on_bsc.status_code == 422
            made = await p.call("POST", "/me/wallet", body={"address": evm, "network": "BEP20"})
            assert made.status_code == 201, made.text
            assert (made.json()["network"], made.json()["address"]) == ("BEP20", evm)
            mine = (await p.call("GET", "/me/wallet")).json()
            assert (mine["network"], mine["address"]) == ("BEP20", evm)

    asyncio.run(scenario())
