"""Payment, payout and screening providers, and their sandbox stand-ins.

The real providers (NOWPayments, C6; Didit, C7) need the operator's
accounts, which do not exist yet. Until then the sandbox versions stand in:
they follow the same interface, take no money and screen nobody for real.
They must never run in production; the API enables them only with
PROPFIRM_SANDBOX=1.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Invoice:
    provider: str
    provider_ref: str  # the provider's id for this payment, unique
    pay_url: str


class PaymentProvider(Protocol):
    name: str

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice: ...


@dataclass(frozen=True, slots=True)
class Screening:
    provider: str
    result: str  # "clear" or "hit"
    detail: dict[str, Any]


class Screener(Protocol):
    async def screen(self, full_name: str, date_of_birth: str, country: str) -> Screening: ...


@dataclass(frozen=True, slots=True)
class PayoutStatus:
    status: str  # "pending" or "paid"
    detail: dict[str, Any]  # the provider's evidence (e.g. the transaction hash)


class PayoutProvider(Protocol):
    """Sends USDT (TRC-20) to a trader's wallet (NOWPayments payouts, C6).

    `send` must be idempotent on `payout` (the PO id): a repeated call after
    a crash returns the same reference and sends nothing more."""

    name: str

    async def send(self, payout: str, network: str, address: str, amount_cents: int) -> str: ...

    async def status(self, ref: str) -> PayoutStatus: ...


class SandboxPayments:
    """Every invoice is paid by calling the sandbox completion endpoint."""

    name = "sandbox"

    async def create_invoice(self, order_id: str, price_cents: int) -> Invoice:
        return Invoice(self.name, f"sandbox-{order_id}", f"/dev/payments/{order_id}/complete")


class SandboxScreener:
    """Clear, except names containing "SANCTIONED" (to test the hit path)."""

    async def screen(self, full_name: str, date_of_birth: str, country: str) -> Screening:
        hit = "SANCTIONED" in full_name.upper()
        return Screening("sandbox", "hit" if hit else "clear", {"lists": "sandbox"})


class SandboxPayouts:
    """Sends nothing: every payout is "paid" at the first status poll."""

    name = "sandbox"

    async def send(self, payout: str, network: str, address: str, amount_cents: int) -> str:
        return f"sandbox-{payout}"

    async def status(self, ref: str) -> PayoutStatus:
        return PayoutStatus("paid", {"tx": f"sandbox-tx-{ref}"})
