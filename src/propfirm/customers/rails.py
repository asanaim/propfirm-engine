"""Payment rails (build plan step 4, lines 1 and 3; D-14.24).

A rail is the code that talks to one payment provider. Each payment method
(rule AC13) names its rail by `adapter`; the method is the switch in the
back office (attached, enabled or disabled by a settings proposal) and
holds the rail's secrets, write-only (AC14). An order records the method
it was placed with as its `provider`, so a webhook, a statement
(D-14.21) and a refund all find the right rail.

The interface:
- `create_invoice(order_id, price_cents)`: an invoice and where the buyer
  pays it (a hosted page: no card or wallet data touches pipridge);
- `parse(headers, body)`: a signed notice from the provider, checked, as
  one `RailEvent` (paid, refunded, disputed, or nothing to do); a bad
  signature raises `RailRejected`;
- `refund`: not offered by every rail; where it is not, the money goes back
  by hand on the provider's side and staff record it (D-14.22).

Rails in this build:
- `sandbox`: development and tests only (PROPFIRM_SANDBOX=1).
- `manual`: a bank transfer or any payment the firm checks by hand. The
  order shows the method's instructions and its own number as the
  reference; one member of staff records the receipt and another confirms
  it, and only then is the order paid.
- `nowpayments`: crypto (USDT on TRC-20 and others), through a NOWPayments
  hosted invoice; payment notices (IPN) signed with HMAC-SHA512 over the
  body with its keys sorted, compared in constant time. Crypto payments
  cannot be charged back, so this rail never reports a dispute.
"""

import hashlib
import hmac
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from propfirm.customers.providers import Invoice

log = logging.getLogger(__name__)

EVENT_KINDS = ("paid", "refunded", "disputed", "nothing")


class RailRejected(Exception):
    """A notice that is not from the provider, or cannot be read."""


class RailUnavailable(Exception):
    """The rail cannot be used now (a missing secret, the provider down)."""


@dataclass(frozen=True, slots=True)
class RailEvent:
    kind: str  # one of EVENT_KINDS
    provider_ref: str  # the invoice's id, as stored on the order
    amount_cents: int | None = None  # what the provider says was paid, in USD cents
    detail: dict[str, Any] = field(default_factory=dict)


class PaymentRail(Protocol):
    name: str  # the method id the orders record as their provider
    adapter: str
    refunds: bool  # whether `refund` reaches the provider

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice: ...

    def parse(self, headers: Mapping[str, str], body: bytes) -> RailEvent: ...


# --- helpers ---------------------------------------------------------------------------


def usd_cents(value: object) -> int:
    """A provider's USD amount (a JSON number or text) in cents, exactly;
    an amount with a fraction of a cent is refused, never rounded."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RailRejected(f"not an amount: {value!r}") from exc
    cents = amount * 100
    if not cents.is_finite() or cents != cents.to_integral_value() or cents < 0:
        raise RailRejected(f"not a whole number of cents: {value!r}")
    return int(cents)


def dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _js_number(value: float) -> str:
    """A number as JavaScript's JSON.stringify writes it (the provider signs
    that text): integral values without a fraction, exponents as e-7/e+21."""
    if not math.isfinite(value):
        raise RailRejected("a number that is not finite")
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" in text:
        mantissa, exponent = text.split("e")
        sign = "-" if exponent.startswith("-") else "+"
        text = f"{mantissa}e{sign}{exponent.lstrip('+-').lstrip('0') or '0'}"
    return text


def sorted_json(value: Any) -> str:
    """JSON.stringify of `value` with every object's keys sorted, no spaces."""
    if isinstance(value, dict):
        items = ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{sorted_json(value[k])}" for k in sorted(value)
        )
        return "{" + items + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted_json(v) for v in value) + "]"
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _js_number(value)
    return json.dumps(value, ensure_ascii=False)


def ipn_signature(body: Any, secret: str) -> str:
    return hmac.new(secret.encode(), sorted_json(body).encode(), hashlib.sha512).hexdigest()


# --- sandbox ---------------------------------------------------------------------------


class SandboxRail:
    """Development only: paid by the sandbox completion endpoint; its
    notices are signed with a fixed key so the webhook path can be tested."""

    adapter = "sandbox"
    refunds = False
    KEY = "sandbox-signing-key"  # pragma: allowlist secret

    def __init__(self, name: str = "sandbox") -> None:
        self.name = name

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice:
        return Invoice(self.name, f"sandbox-{order_id}", f"/dev/payments/{order_id}/complete")

    def parse(self, headers: Mapping[str, str], body: bytes) -> RailEvent:
        return _ipn(headers, body, self.KEY, "x-sandbox-sig")


class NoDefaultRail:
    """Production with no method named: nothing to pay with. Once methods
    are attached a purchase names one (rule AC13), so this answers only
    before the first method exists."""

    name = "none"
    adapter = "none"
    refunds = False

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice:
        raise RailUnavailable("no payment method is attached")

    def parse(self, headers: Mapping[str, str], body: bytes) -> RailEvent:
        raise RailRejected("no rail")


# --- manual: a bank transfer, checked by hand -------------------------------------------


class ManualRail:
    adapter = "manual"
    refunds = False

    def __init__(self, name: str) -> None:
        self.name = name

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice:
        # no page to send the buyer to: the order shows the method's
        # instructions, with the order number as the payment reference
        return Invoice(self.name, f"{self.name}-{order_id}", "")

    def parse(self, headers: Mapping[str, str], body: bytes) -> RailEvent:
        raise RailRejected("a manual method takes no notices")


# --- NOWPayments -----------------------------------------------------------------------

NOWPAYMENTS_API = "https://api.nowpayments.io/v1"
NOWPAYMENTS_SANDBOX_API = "https://api-sandbox.nowpayments.io/v1"
# their payment statuses: only `finished` is money received in full
NOWPAYMENTS_PAID = "finished"
NOWPAYMENTS_REFUNDED = "refunded"
# one coin: the single-currency fee (1 %) instead of the multi-currency one
# (1.5 %), vendor-stated; and the coin payouts use (functional outline)
NOWPAYMENTS_CURRENCY = "usdttrc20"


def _ipn(headers: Mapping[str, str], body: bytes, secret: str, header: str) -> RailEvent:
    given = next((v for k, v in headers.items() if k.lower() == header), "")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RailRejected("not JSON") from exc
    if not isinstance(data, dict):
        raise RailRejected("not a JSON object")
    if not given or not hmac.compare_digest(given.lower(), ipn_signature(data, secret)):
        raise RailRejected("bad signature")
    ref = data.get("invoice_id")
    if ref is None:
        return RailEvent("nothing", "", None, {"reason": "no invoice"})
    status = str(data.get("payment_status", ""))
    detail = {
        k: data[k]
        for k in ("payment_id", "payment_status", "pay_currency", "actually_paid", "order_id")
        if k in data
    }
    amount = usd_cents(data["price_amount"]) if "price_amount" in data else None
    kind = {NOWPAYMENTS_PAID: "paid", NOWPAYMENTS_REFUNDED: "refunded"}.get(status, "nothing")
    if status == "disputed" and header == "x-sandbox-sig":  # the sandbox's test status only
        kind = "disputed"
    return RailEvent(kind, str(ref), amount, detail)


class NowPaymentsRail:
    adapter = "nowpayments"
    refunds = False  # refunds go back by hand from the NOWPayments dashboard

    def __init__(
        self,
        name: str,
        http: httpx.AsyncClient,
        api_key: str | None,
        ipn_secret: str | None,
        callback_base: str | None,
        *,
        sandbox: bool,
        pay_currency: str | None = NOWPAYMENTS_CURRENCY,
    ) -> None:
        self.name = name
        self._http = http
        self._key = api_key
        self._secret = ipn_secret
        self._base = NOWPAYMENTS_SANDBOX_API if sandbox else NOWPAYMENTS_API
        self._callback = callback_base
        self._currency = pay_currency

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice:
        if not self._key or not self._callback:
            raise RailUnavailable(f"{self.name}: api_key or PROPFIRM_PUBLIC_URL is not set")
        body: dict[str, Any] = {
            "price_amount": dollars(price_cents),
            "price_currency": "usd",
            "order_id": order_id,
            "order_description": f"pipridge order {order_id}",
            "ipn_callback_url": f"{self._callback}/webhooks/payments/{self.name}",
            "success_url": f"{self._callback}/shop?order={order_id}",
            "cancel_url": f"{self._callback}/shop",
        }
        if self._currency:
            body["pay_currency"] = self._currency
        try:
            response = await self._http.post(
                f"{self._base}/invoice",
                json=body,
                headers={"x-api-key": self._key},
                timeout=20.0,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RailUnavailable(f"{self.name}: the invoice was not created") from exc
        if "id" not in data or "invoice_url" not in data:
            raise RailUnavailable(f"{self.name}: an answer without an invoice")
        return Invoice(self.name, str(data["id"]), str(data["invoice_url"]))

    def parse(self, headers: Mapping[str, str], body: bytes) -> RailEvent:
        if not self._secret:
            raise RailRejected(f"{self.name}: ipn_secret is not set")
        return _ipn(headers, body, self._secret, "x-nowpayments-sig")


ADAPTERS = ("sandbox", "manual", "nowpayments")
SECRETS: dict[str, tuple[str, ...]] = {
    "sandbox": (),
    "manual": (),
    "nowpayments": ("api_key", "ipn_secret"),
}


# --- the rail of a method ----------------------------------------------------------------


class MethodSource(Protocol):
    async def methods(self) -> Mapping[str, Any]: ...

    async def secrets(self) -> Mapping[tuple[str, str], Any]: ...


class Opener(Protocol):
    def open(self, secret: Any) -> str: ...


class Rails:
    """The rail of each payment method, built from the method in force and
    its secrets, opened only when needed and never kept. With no method
    named (no methods attached yet), the default rail: the sandbox in
    development, else none."""

    def __init__(
        self,
        methods: MethodSource,
        vault: Opener,
        http: httpx.AsyncClient | None,
        callback_base: str | None,
        *,
        sandbox: bool,
        default: PaymentRail | None = None,
    ) -> None:
        self._methods = methods
        self._vault = vault
        self._http = http
        self._callback = callback_base.rstrip("/") if callback_base else None
        self._sandbox = sandbox
        self.default = default

    async def method(self, method_id: str) -> Any:
        method = (await self._methods.methods()).get(method_id)
        if method is None or method.kind != "payment":
            raise RailUnavailable(f"no payment method {method_id}")
        return method

    async def for_method(self, method_id: str | None) -> PaymentRail:
        """The rail of `method_id` (enabled or not: a disabled method still
        hears about its old orders), or the default rail."""
        if method_id is None:
            if self.default is None:
                raise RailUnavailable("no payment method is attached")
            return self.default
        method = await self.method(method_id)
        if method.adapter == "sandbox":
            if not self._sandbox:
                raise RailUnavailable("the sandbox rail runs only with PROPFIRM_SANDBOX=1")
            return SandboxRail(method_id)
        if method.adapter == "manual":
            return ManualRail(method_id)
        if method.adapter == "nowpayments":
            if self._http is None:
                raise RailUnavailable("no HTTP client")
            secrets = await self._secrets(method_id)
            return NowPaymentsRail(
                method_id,
                self._http,
                secrets.get("api_key"),
                secrets.get("ipn_secret"),
                self._callback,
                sandbox=self._sandbox,
            )
        raise RailUnavailable(f"no rail for adapter {method.adapter}")

    async def _secrets(self, method_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for (owner, name), sealed in (await self._methods.secrets()).items():
            if owner == method_id:
                out[name] = self._vault.open(sealed)
        return out
