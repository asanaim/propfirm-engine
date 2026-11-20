"""Payment rails (D-14.24): the signed notice exactly as NOWPayments signs
it (checked against Node's JSON.stringify), the invoice request, amounts
to the cent, and the rail each method gets."""

import asyncio
import json
from fractions import Fraction
from typing import Any

import httpx
import pytest

from propfirm.customers.rails import (
    ManualRail,
    NoDefaultRail,
    NowPaymentsRail,
    RailRejected,
    Rails,
    RailUnavailable,
    SandboxRail,
    ipn_signature,
    sorted_json,
    usd_cents,
)
from propfirm.settings.methods import PaymentMethod, Vault

# a notice as NOWPayments sends it; the expected text and signature were
# computed with Node: JSON.stringify of the recursively key-sorted object,
# HMAC-SHA512 with the IPN secret
BODY = (
    b'{"payment_id":5077125051,"invoice_id":4522625843,"payment_status":"finished",'
    b'"pay_address":"TXYZ","price_amount":109.0,"price_currency":"usd","pay_amount":110.25,'
    b'"actually_paid":110.25,"actually_paid_at_fiat":0,"pay_currency":"usdttrc20",'
    b'"order_id":"ORD00000001","order_description":"pipridge order ORD00000001",'
    b'"purchase_id":"5837122679","created_at":"2026-11-20T09:30:00.000Z",'
    b'"updated_at":"2026-11-20T09:41:13.153Z","outcome_amount":1e-7,'
    b'"fee":{"currency":"usdttrc20","depositFee":0.5,"withdrawalFee":0,"serviceFee":1.1},'
    b'"note":"caf\xc3\xa9 \xe2\x9c\x93"}'
)
NODE_TEXT = (
    '{"actually_paid":110.25,"actually_paid_at_fiat":0,"created_at":"2026-11-20T09:30:00.000Z",'
    '"fee":{"currency":"usdttrc20","depositFee":0.5,"serviceFee":1.1,"withdrawalFee":0},'
    '"invoice_id":4522625843,"note":"café ✓","order_description":"pipridge order ORD00000001",'
    '"order_id":"ORD00000001","outcome_amount":1e-7,"pay_address":"TXYZ","pay_amount":110.25,'
    '"pay_currency":"usdttrc20","payment_id":5077125051,"payment_status":"finished",'
    '"price_amount":109,"price_currency":"usd","purchase_id":"5837122679",'
    '"updated_at":"2026-11-20T09:41:13.153Z"}'
)
NODE_SIG = (
    "0f6784b4ba497fdfa3c93a65d9f441e7a738252fb871ed40a0a1b121beb22e4d"  # pragma: allowlist secret
    "f1ecb19cd0ec059fa6cabfc946dbeb82b1c33a93bc580685c0898dcc599c1e79"  # pragma: allowlist secret
)
SECRET = "test-ipn-secret"  # pragma: allowlist secret


def rail(http: httpx.AsyncClient | None = None, **kw: Any) -> NowPaymentsRail:
    return NowPaymentsRail(
        "crypto",
        http or httpx.AsyncClient(),
        kw.get("api_key", "key"),
        kw.get("ipn_secret", SECRET),
        kw.get("callback", "https://pipridge.example"),
        sandbox=kw.get("sandbox", True),
    )


def test_the_signed_text_is_javascripts() -> None:
    assert sorted_json(json.loads(BODY)) == NODE_TEXT
    assert ipn_signature(json.loads(BODY), SECRET) == NODE_SIG


def test_a_signed_finished_payment_is_paid() -> None:
    event = rail().parse({"X-NOWPayments-Sig": NODE_SIG.upper()}, BODY)
    assert (event.kind, event.provider_ref, event.amount_cents) == ("paid", "4522625843", 10900)
    assert event.detail["pay_currency"] == "usdttrc20"


@pytest.mark.parametrize(
    "headers",
    [{}, {"x-nowpayments-sig": "0" * 128}, {"x-nowpayments-sig": NODE_SIG[:-1] + "0"}],
)
def test_an_unsigned_or_forged_notice_is_refused(headers: dict[str, str]) -> None:
    with pytest.raises(RailRejected, match="bad signature"):
        rail().parse(headers, BODY)
    with pytest.raises(RailRejected, match="ipn_secret"):
        rail(ipn_secret=None).parse({"x-nowpayments-sig": NODE_SIG}, BODY)


def signed(data: dict[str, Any]) -> tuple[dict[str, str], bytes]:
    return {"x-nowpayments-sig": ipn_signature(data, SECRET)}, json.dumps(data).encode()


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("waiting", "nothing"),
        ("confirming", "nothing"),
        ("partially_paid", "nothing"),
        ("failed", "nothing"),
        ("expired", "nothing"),
        ("finished", "paid"),
        ("refunded", "refunded"),
    ],
)
def test_only_finished_is_money_received(status: str, kind: str) -> None:
    headers, body = signed({"invoice_id": 7, "payment_status": status, "price_amount": "55"})
    event = rail().parse(headers, body)
    assert (event.kind, event.provider_ref, event.amount_cents) == (kind, "7", 5500)


def test_amounts_are_whole_cents_or_refused() -> None:
    assert (usd_cents(109.0), usd_cents("55.5"), usd_cents(0)) == (10900, 5550, 0)
    for bad in ("55.555", "-1", "x", float("inf")):
        with pytest.raises(RailRejected):
            usd_cents(bad)


def test_the_invoice_asks_for_usdt_on_tron_in_dollars() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 4522625843, "invoice_url": "https://pay/iid=1"})

    async def go() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            return await rail(http).create_invoice("ORD00000001", 10_900)

    invoice = asyncio.run(go())
    assert (invoice.provider, invoice.provider_ref, invoice.pay_url) == (
        "crypto",
        "4522625843",
        "https://pay/iid=1",
    )
    (request,) = seen
    assert str(request.url) == "https://api-sandbox.nowpayments.io/v1/invoice"
    assert request.headers["x-api-key"] == "key"
    assert json.loads(request.content) == {
        "price_amount": "109.00",
        "price_currency": "usd",
        "order_id": "ORD00000001",
        "order_description": "pipridge order ORD00000001",
        "ipn_callback_url": "https://pipridge.example/webhooks/payments/crypto",
        "success_url": "https://pipridge.example/shop?order=ORD00000001",
        "cancel_url": "https://pipridge.example/shop",
        "pay_currency": "usdttrc20",
    }


def test_an_invoice_the_provider_did_not_make_is_unavailable() -> None:
    async def go(answer: httpx.Response, **kw: Any) -> Any:
        transport = httpx.MockTransport(lambda _: answer)
        async with httpx.AsyncClient(transport=transport) as http:
            return await rail(http, **kw).create_invoice("ORD00000001", 100)

    for response in (httpx.Response(500), httpx.Response(200, json={"id": 1})):
        with pytest.raises(RailUnavailable):
            asyncio.run(go(response))
    with pytest.raises(RailUnavailable, match="api_key"):
        asyncio.run(go(httpx.Response(200), api_key=None))
    with pytest.raises(RailUnavailable):
        asyncio.run(NoDefaultRail().create_invoice("ORD1", 100))


def method(method_id: str, adapter: str) -> PaymentMethod:
    return PaymentMethod(
        method_id, "payment", method_id.title(), adapter, True, 0, 0, 0, Fraction(0)
    )


class Methods:
    def __init__(self, vault: Vault) -> None:
        self._vault = vault

    async def methods(self) -> dict[str, PaymentMethod]:
        return {
            "bank": method("bank", "manual"),
            "crypto": method("crypto", "nowpayments"),
            "dev": method("dev", "sandbox"),
            "payout": PaymentMethod(
                "payout", "withdrawal", "Payout", "manual", True, 0, 0, 0, Fraction(0)
            ),
        }

    async def secrets(self) -> dict[tuple[str, str], Any]:
        nonce = bytes(12)
        return {
            ("crypto", "api_key"): self._vault.seal("crypto", "api_key", "k-1", nonce),
            ("crypto", "ipn_secret"): self._vault.seal("crypto", "ipn_secret", SECRET, nonce),
        }


def test_each_method_gets_its_rail_with_its_secrets() -> None:
    vault = Vault(bytes(range(32)))
    rails = Rails(
        Methods(vault), vault, httpx.AsyncClient(), "https://p/", sandbox=False, default=None
    )

    async def go() -> None:
        bank = await rails.for_method("bank")
        assert isinstance(bank, ManualRail)
        invoice = await bank.create_invoice("ORD00000002", 100)
        assert (invoice.provider, invoice.provider_ref, invoice.pay_url) == (
            "bank",
            "bank-ORD00000002",
            "",
        )
        crypto = await rails.for_method("crypto")
        assert isinstance(crypto, NowPaymentsRail)
        assert crypto.parse({"x-nowpayments-sig": NODE_SIG}, BODY).kind == "paid"
        for refused in ("dev", "payout", "nothing"):  # sandbox off; not a payment; unknown
            with pytest.raises(RailUnavailable):
                await rails.for_method(refused)
        with pytest.raises(RailUnavailable, match="no payment method"):
            await rails.for_method(None)

    asyncio.run(go())
    sandboxed = Rails(Methods(vault), vault, None, None, sandbox=True, default=SandboxRail())
    assert asyncio.run(sandboxed.for_method(None)).name == "sandbox"
    assert asyncio.run(sandboxed.for_method("dev")).name == "dev"
