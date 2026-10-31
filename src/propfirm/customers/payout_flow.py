"""Payout wallets and the payout process (outline M06, M19), rebuilt from an
append-only history.

Wallet: USDT on TRON (TRC-20), the only payout method [CONFIRMED]. A wallet
is checked as a TRON address (Base58Check, version byte 0x41); a new or
changed wallet is used only after a cooling-off period, and only a login
that used MFA may set it (policy). How ownership is proven beyond that is
open (D31).

A payout (PO00000001), from request to money sent:
1. requested by the trader when eligible (on request, as FTMO and
   FundedNext), with the eligibility snapshot: the trader's amount and what
   leaves the account (P + V); the account takes no new orders until the
   debit when trading is paused (D23);
2. approved, or denied by one employee (until the debit is attempted); the
   trader may cancel before approval is complete. Approval, as the request
   recorded it (`approval`):
   - pass_authorise [decided 31 Oct 2026]: a pass (`"as": "pass"`, a T2 or
     above), then an authorisation (`"as": "authorise"`, an admin) by a
     different employee; who may do each step is checked by the API;
   - count (requests recorded before): `approvals_required` different
     employees;
   never on the employee's own customer record;
3. once approved, eligibility is computed again: anything changed and the
   payout fails (the trader may ask again);
4. the engine takes the amount out of the account (DebitPayout; one per
   payout, ever);
5. the payout provider sends USDT to the wallet; the provider's
   confirmation marks it paid.

Events: requested, approved, denied, cancelled, rechecked, failed,
debit_attempted, debited, sent, send_failed, paid. `payouts` status columns
are a cache of `payout_state`.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PAYOUT_KINDS = (
    "requested",
    "approved",
    "denied",
    "cancelled",
    "rechecked",
    "failed",
    "debit_attempted",
    "debited",
    "sent",
    "send_failed",
    "paid",
)
PAYOUT_STATUSES = (
    "requested",
    "approved",
    "debited",
    "sent",
    "paid",
    "denied",
    "cancelled",
    "failed",
)
IN_PROGRESS = ("requested", "approved")  # the account's payout not yet taken out
TAKEN = ("debited", "sent", "paid")  # out of the account: counts as a payout made

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def tron_address_valid(address: str) -> bool:
    """A TRON (TRC-20) address: Base58Check of 0x41 + 20 bytes, 34 characters."""
    if not isinstance(address, str) or len(address) != 34 or not address.startswith("T"):
        return False
    number = 0
    for ch in address:
        index = _B58.find(ch)
        if index < 0:
            return False
        number = number * 58 + index
    try:
        raw = number.to_bytes(25, "big")
    except OverflowError:
        return False
    body, checksum = raw[:21], raw[21:]
    digest = hashlib.sha256(hashlib.sha256(body).digest()).digest()
    return body[0] == 0x41 and digest[:4] == checksum


class PayoutError(ValueError):
    """Events that no valid payout produces."""


@dataclass(frozen=True, slots=True)
class PayoutEvent:
    po: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


APPROVALS = ("count", "pass_authorise")
STEPS = ("pass", "authorise")


@dataclass(frozen=True, slots=True)
class PayoutState:
    status: str
    approvals: tuple[str, ...]  # employees who approved, in order
    debited_at: int | None
    paid_at: int | None
    provider_ref: str | None
    failure: str | None
    approval: str = "count"

    @property
    def next_step(self) -> str | None:
        """With pass_authorise and approval not complete: the step due."""
        if self.approval != "pass_authorise" or self.status != "requested":
            return None
        return STEPS[len(self.approvals)]


def payout_state(po: str, events: Iterable[PayoutEvent]) -> PayoutState:
    status: str | None = None
    required = 0
    approval = "count"
    approvals: list[str] = []
    debited_at: int | None = None
    paid_at: int | None = None
    ref: str | None = None
    failure: str | None = None
    attempted = False  # the engine may have taken the money: no denial any more

    def refuse(event: PayoutEvent) -> PayoutError:
        return PayoutError(f"{po}: {event.kind} cannot follow status {status}")

    def need(event: PayoutEvent, *allowed: str) -> None:
        if status not in allowed:
            raise refuse(event)

    for event in events:
        if event.po != po or event.kind not in PAYOUT_KINDS:
            raise PayoutError(f"{po}: unexpected event {event.po} {event.kind}")
        d = event.detail
        match event.kind:
            case "requested":
                if status is not None:
                    raise refuse(event)
                status, required = "requested", int(d["approvals_required"])
                approval = str(d.get("approval", "count"))
                if required < 1 or approval not in APPROVALS:
                    raise refuse(event)
                if approval == "pass_authorise" and required != len(STEPS):
                    raise refuse(event)
            case "approved":
                need(event, "requested")
                if d["by"] in approvals:
                    raise refuse(event)  # one approval per employee
                step = d.get("as")
                due = STEPS[len(approvals)] if approval == "pass_authorise" else None
                if step != due:
                    raise refuse(event)  # a pass first, then the authorisation
                approvals.append(str(d["by"]))
                if len(approvals) == required:
                    status = "approved"
            case "denied":
                need(event, "requested", "approved")
                if attempted:
                    raise refuse(event)
                status, failure = "denied", str(d["reason"])
            case "cancelled":
                need(event, "requested")
                status = "cancelled"
            case "rechecked":
                need(event, "approved")
            case "debit_attempted":
                need(event, "approved")
                attempted = True
            case "failed":
                need(event, "requested", "approved")
                status, failure = "failed", str(d["reason"])
            case "debited":
                need(event, "approved")
                status, debited_at = "debited", event.ts
            case "sent":
                need(event, "debited")
                status, ref = "sent", str(d["ref"])
            case "send_failed":
                need(event, "debited")
            case "paid":
                need(event, "sent")
                status, paid_at = "paid", event.ts
    if status is None:
        raise PayoutError(f"{po}: no requested event")
    return PayoutState(status, tuple(approvals), debited_at, paid_at, ref, failure, approval)
