"""Order and customer status, rebuilt from their append-only history.

The record of an order is its events (table `order_events`); the status
columns of `orders` are a cache of `order_state(events)`, written in the same
transaction as each event and rebuilt from the events at API start (as
balances are rebuilt from the ledger). Likewise a customer's status is a
cache of `cif_status(events)` over `cif_events`.

Order events, in the order they can happen:
- created: the order's facts (product, size, prices, discount, rule set);
- invoiced: the payment provider's reference;
- paid: the first payment notice (pending -> paid);
- payment_repeated: any later notice for the same order (no change);
- account_assigned: the ACC id, allocated once;
- provision_attempted: OpenAccount sent to the engine (stream entry id);
  one per attempt, so retries are visible;
- provisioned: the engine opened the account (journal seq) (paid -> provisioned);
- failed: the engine refused it (reason, journal seq) (paid -> failed).
An event that cannot follow the ones before it is an error, never skipped.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ORDER_KINDS = (
    "created",
    "invoiced",
    "paid",
    "payment_repeated",
    "account_assigned",
    "provision_attempted",
    "provisioned",
    "failed",
)
CIF_STATUS_KINDS = {"registered": "registered", "blocked": "blocked"}


class HistoryError(ValueError):
    """Events that no valid history produces."""


@dataclass(frozen=True, slots=True)
class OrderEvent:
    ord: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OrderState:
    """The cached columns of `orders`."""

    status: str  # pending, paid, provisioned, failed
    provider_ref: str
    acc: str | None
    failure: str | None
    paid_at: int | None
    provisioned_at: int | None


def placeholder_ref(ord_id: str) -> str:
    """The provider reference an order has before its invoice exists."""
    return f"pending-{ord_id}"


def order_state(ord_id: str, events: Iterable[OrderEvent]) -> OrderState:
    status: str | None = None
    provider_ref = placeholder_ref(ord_id)
    acc: str | None = None
    failure: str | None = None
    paid_at: int | None = None
    provisioned_at: int | None = None

    def refuse(event: OrderEvent) -> HistoryError:
        return HistoryError(f"{ord_id}: {event.kind} cannot follow status {status}")

    for event in events:
        if event.ord != ord_id or event.kind not in ORDER_KINDS:
            raise HistoryError(f"{ord_id}: unexpected event {event.ord} {event.kind}")
        match event.kind:
            case "created":
                if status is not None:
                    raise refuse(event)
                status = "pending"
            case "invoiced":
                if status != "pending" or provider_ref != placeholder_ref(ord_id):
                    raise refuse(event)
                provider_ref = str(event.detail["provider_ref"])
            case "paid":
                if status != "pending":
                    raise refuse(event)
                status, paid_at = "paid", event.ts
            case "payment_repeated":
                if status in (None, "pending"):
                    raise refuse(event)
            case "account_assigned":
                if status != "paid" or acc is not None:
                    raise refuse(event)
                acc = str(event.detail["acc"])
            case "provision_attempted":
                if status != "paid" or acc is None:
                    raise refuse(event)
            case "provisioned":
                if status != "paid" or acc is None:
                    raise refuse(event)
                status, provisioned_at = "provisioned", event.ts
            case "failed":
                if status != "paid":
                    raise refuse(event)
                status, failure = "failed", str(event.detail["reason"])
    if status is None:
        raise HistoryError(f"{ord_id}: no created event")
    return OrderState(status, provider_ref, acc, failure, paid_at, provisioned_at)


def cif_status(cif: str, kinds: Iterable[str]) -> str:
    """The status after a customer's events (oldest first)."""
    status: str | None = None
    for kind in kinds:
        if kind == "registered":
            if status is not None:
                raise HistoryError(f"{cif}: registered twice")
            status = "registered"
        elif kind in CIF_STATUS_KINDS:
            if status is None:
                raise HistoryError(f"{cif}: {kind} before registration")
            status = CIF_STATUS_KINDS[kind]
    if status is None:
        raise HistoryError(f"{cif}: no registered event")
    return status
