"""Payment rails through real records (D-14.24): methods attached by two
owners, a NOWPayments invoice and its signed notices (forged, waiting,
wrong amount, paid, repeated), a bank transfer recorded and confirmed by
two people, and a provider's dispute recorded as a chargeback."""

import asyncio
import json
from typing import Any

import pytest

from propfirm.customers.rails import SandboxRail, ipn_signature
from tests.integration.test_customers import ADULT, LITE_10K, Platform, eventually, platform
from tests.integration.test_settings import OWNERS, two_owners

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
IPN = "ipn-test-secret"  # pragma: allowlist secret


def method(name: str, adapter: str, instructions: str = "") -> dict[str, Any]:
    return {
        "kind": "payment",
        "name": name,
        "adapter": adapter,
        "enabled": True,
        "min_cents": 0,
        "max_cents": 0,
        "fixed_charge_cents": 0,
        "percent_charge": "0",
        "instructions": instructions,
        "user_fields": [],
        "reason": "launch",
    }


async def notice(p: Platform, method_id: str, data: dict[str, Any], secret: str) -> Any:
    header = "x-sandbox-sig" if secret == SandboxRail.KEY else "x-nowpayments-sig"
    return await p.api.post(
        f"/webhooks/payments/{method_id}",
        content=json.dumps(data).encode(),
        headers={header: ipn_signature(data, secret), "content-type": "application/json"},
    )


def test_payment_rails(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, owners=OWNERS) as p:
            for mid, body in (
                ("crypto", method("USDT (TRC-20)", "nowpayments")),
                ("bank", method("Bank transfer", "manual", "IBAN XX00 0000; quote the order")),
                ("dev", method("Sandbox", "sandbox")),
            ):
                assert (
                    await two_owners(p, f"/admin/payment-methods/{mid}", body)
                ).status_code == 200
            for name, value in (("api_key", "k-1"), ("ipn_secret", IPN)):
                url = f"/admin/payment-methods/crypto/secrets/{name}"
                done = await two_owners(p, url, {"value": value, "reason": "vendor keys"})
                assert done.status_code == 200, done.text

            # crypto: an invoice at NOWPayments, then its notices
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", {**LITE_10K, "method": "crypto"})).json()
            assert order["pay_url"] == "https://pay.example/9001"
            price = int(order["price_cents"])
            dollars = f"{price // 100}.{price % 100:02d}"
            paid = {"invoice_id": 9001, "payment_status": "finished", "price_amount": dollars}

            forged = await p.api.post(
                "/webhooks/payments/crypto",
                content=json.dumps(paid).encode(),
                headers={"x-nowpayments-sig": "0" * 128},
            )
            assert forged.status_code == 401
            waiting = await notice(p, "crypto", {**paid, "payment_status": "waiting"}, IPN)
            assert waiting.json()["done"] == "nothing"
            short = await notice(p, "crypto", {**paid, "price_amount": "1.00"}, IPN)
            assert short.json()["done"] == "amount_differs"
            mine = (await p.call("GET", "/me", "u1")).json()
            assert mine["orders"][0]["status"] == "pending"
            told = (await p.call("GET", "/staff/notifications", "admin-1", None, ADMIN)).json()
            assert [n["kind"] for n in told] == ["payment_mismatch"]
            ok = await notice(p, "crypto", paid, IPN)
            assert ok.json()["done"] == "paid"
            again = await notice(p, "crypto", paid, IPN)
            assert again.status_code == 200  # sent twice, opened once
            await eventually(lambda: p.live.account("ACC00000001"))
            unknown = await notice(p, "crypto", {**paid, "invoice_id": 1}, IPN)
            assert unknown.json()["done"] == "unknown_invoice"

            # bank transfer: instructions, then a receipt confirmed by two
            await p.call("POST", "/me", "u2", {**ADULT, "full_name": "Second"})
            bank = (await p.call("POST", "/orders", "u2", {**LITE_10K, "method": "bank"})).json()
            assert bank["pay_url"] == ""
            assert bank["pay_instructions"] == "IBAN XX00 0000; quote the order"
            assert bank["pay_reference"] == bank["ord"]
            listed = (await p.call("GET", "/staff/receipts", "admin-1", None, ADMIN)).json()
            assert [o["ord"] for o in listed["awaiting"]] == [bank["ord"]]
            url = f"/staff/orders/{bank['ord']}/receipts"
            body = {"amount_cents": price, "reference": "BANK-77", "received_at": p.clock()}
            wrong = await p.call("POST", url, "admin-1", {**body, "amount_cents": 1}, ADMIN)
            assert (wrong.status_code, wrong.json()["detail"]["code"]) == (409, "amount_differs")
            crypto_order = f"/staff/orders/{order['ord']}/receipts"
            not_manual = await p.call("POST", crypto_order, "admin-1", body, ADMIN)
            assert not_manual.json()["detail"]["code"] == "not_manual"
            rcp = (await p.call("POST", url, "admin-1", body, ADMIN)).json()["rcp"]
            twice = await p.call("POST", url, "admin-1", body, ADMIN)
            assert twice.json()["detail"]["code"] == "recorded"
            decide = f"/staff/receipts/{rcp}/decision"
            same = await p.call("POST", decide, "admin-1", {"confirm": True, "reason": "x"}, ADMIN)
            assert (same.status_code, same.json()["detail"]["code"]) == (403, "same_person")
            done = await p.call(
                "POST", decide, "admin-2", {"confirm": True, "reason": "on statement"}, ADMIN
            )
            assert done.json()["status"] == "confirmed"
            await eventually(lambda: p.live.account("ACC00000002"))

            # a provider's dispute: a chargeback at once
            await p.call("POST", "/me", "u3", {**ADULT, "full_name": "Third"})
            dev = (await p.call("POST", "/orders", "u3", {**LITE_10K, "method": "dev"})).json()
            ref = f"sandbox-{dev['ord']}"
            settled = {"invoice_id": ref, "payment_status": "finished", "price_amount": dollars}
            assert (await notice(p, "dev", settled, SandboxRail.KEY)).json()["done"] == "paid"
            disputed = {**settled, "payment_status": "disputed", "payment_id": "case-9"}
            assert (await notice(p, "dev", disputed, SandboxRail.KEY)).json()["done"] == "disputed"
            charges = (await p.call("GET", "/staff/chargebacks", "admin-1", None, ADMIN)).json()
            assert [(c["ord"], c["provider_case"], c["recorded_by"]) for c in charges] == [
                (dev["ord"], "case-9", "rail:dev")
            ]
            # NOWPayments never reports a dispute: the same status is nothing there
            crypto_dispute = await notice(p, "crypto", {**paid, "payment_status": "disputed"}, IPN)
            assert crypto_dispute.json()["done"] == "nothing"

    asyncio.run(scenario())
