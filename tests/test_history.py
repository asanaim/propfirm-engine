"""Order and customer status folded from their append-only histories."""

import re
from collections.abc import Mapping

import pytest

from propfirm.customers.history import (
    ORDER_KINDS,
    HistoryError,
    OrderEvent,
    OrderState,
    cif_status,
    order_state,
)
from propfirm.customers.store import _MIGRATIONS

ORD = "ORD00000001"
type Step = tuple[str, int, Mapping[str, object]]


def history(*steps: Step) -> list[OrderEvent]:
    return [OrderEvent(ORD, kind, ts, detail) for kind, ts, detail in steps]


CREATED: Step = ("created", 1, {"price_cents": 3_960})
INVOICED: Step = ("invoiced", 2, {"provider_ref": "inv-1"})
PAID: Step = ("paid", 3, {"provider_ref": "inv-1"})
ASSIGNED: Step = ("account_assigned", 4, {"acc": "ACC00000001"})
ATTEMPT: Step = ("provision_attempted", 5, {"acc": "ACC00000001", "entry_id": "1-0"})
PROVISIONED: Step = ("provisioned", 6, {"acc": "ACC00000001", "journal_seq": 9})


def test_a_provisioned_order() -> None:
    state = order_state(ORD, history(CREATED, INVOICED, PAID, ASSIGNED, ATTEMPT, PROVISIONED))
    assert state == OrderState("provisioned", "inv-1", "ACC00000001", None, 3, 6)


def test_each_stage_of_an_order() -> None:
    assert order_state(ORD, history(CREATED)) == OrderState(
        "pending", "pending-ORD00000001", None, None, None, None
    )
    assert order_state(ORD, history(CREATED, INVOICED)).provider_ref == "inv-1"
    assert order_state(ORD, history(CREATED, INVOICED, PAID)).status == "paid"
    waiting = order_state(ORD, history(CREATED, INVOICED, PAID, ASSIGNED, ATTEMPT, ATTEMPT))
    assert (waiting.status, waiting.acc) == ("paid", "ACC00000001")  # retried, not yet open


def test_repeated_payment_notices_change_nothing() -> None:
    repeat: Step = ("payment_repeated", 7, {"provider_ref": "inv-1"})
    done = history(CREATED, INVOICED, PAID, ASSIGNED, ATTEMPT, PROVISIONED)
    assert order_state(ORD, [*done, *history(repeat, repeat)]) == order_state(ORD, done)
    paid = history(CREATED, INVOICED, PAID, repeat)
    assert order_state(ORD, paid) == order_state(ORD, history(CREATED, INVOICED, PAID))


def test_a_failed_order_keeps_the_reason() -> None:
    failed: Step = ("failed", 6, {"reason": "unknown_rule_set", "journal_seq": 9})
    state = order_state(ORD, history(CREATED, INVOICED, PAID, ASSIGNED, ATTEMPT, failed))
    assert (state.status, state.failure, state.provisioned_at) == (
        "failed",
        "unknown_rule_set",
        None,
    )


@pytest.mark.parametrize(
    "steps",
    [
        (),  # no created event
        (PAID,),
        (CREATED, CREATED),
        (CREATED, PAID, INVOICED),  # invoice after payment
        (CREATED, INVOICED, INVOICED),
        (CREATED, INVOICED, ("payment_repeated", 3, {})),  # a repeat before the first
        (CREATED, INVOICED, ASSIGNED),  # account before payment
        (CREATED, INVOICED, PAID, ASSIGNED, ASSIGNED),
        (CREATED, INVOICED, PAID, ATTEMPT),  # attempt without an account
        (CREATED, INVOICED, PAID, PROVISIONED),
        (CREATED, INVOICED, PAID, ASSIGNED, PROVISIONED, PROVISIONED),
        (CREATED, INVOICED, PAID, ASSIGNED, PROVISIONED, ("failed", 7, {"reason": "x"})),
        (CREATED, ("refunded", 2, {})),  # not an order event (yet)
    ],
)
def test_histories_no_valid_order_has_are_refused(
    steps: tuple[Step, ...],
) -> None:
    with pytest.raises(HistoryError):
        order_state(ORD, history(*steps))


def test_an_event_of_another_order_is_refused() -> None:
    with pytest.raises(HistoryError):
        order_state(ORD, [OrderEvent("ORD00000002", "created", 1, {})])


def test_customer_status() -> None:
    assert cif_status("CIF1", ["registered"]) == "registered"
    assert cif_status("CIF1", ["registered", "blocked"]) == "blocked"
    for kinds in ([], ["blocked"], ["registered", "registered"]):
        with pytest.raises(HistoryError):
            cif_status("CIF1", kinds)


def test_the_migration_allows_exactly_the_order_event_kinds() -> None:
    sql = dict(_MIGRATIONS)[2]
    found = re.search(r"kind IN \(([^)]*)\)", sql)
    assert found is not None
    assert tuple(re.findall(r"'([a-z_]+)'", found.group(1))) == ORDER_KINDS
