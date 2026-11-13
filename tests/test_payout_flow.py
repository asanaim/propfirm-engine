"""The payout process (fold), the TRON wallet check, and the engine's payout
debit, without a database."""

import hashlib
import re
from typing import Any

import pytest

from propfirm.api.auth import SECOND_FACTORS
from propfirm.customers.payout_flow import (
    PAYOUT_KINDS,
    PAYOUT_STATUSES,
    PayoutError,
    PayoutEvent,
    payout_state,
    tron_address_valid,
)
from propfirm.customers.store import _MIGRATIONS
from propfirm.journal.inputs import (
    ClosePosition,
    DebitPayout,
    InputRejected,
    InputRejectReason,
    OpenAccount,
    PlaceOrder,
    VoidAccount,
)
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.rules.events import PayoutDebited
from propfirm.service.setup import setup_from_policy
from propfirm.trading.engine import FIRM_PAYOUTS, payout_entry
from propfirm.trading.events import PositionClosed, PositionOpened
from propfirm.trading.orders import OrderRequest, OrderType, Side

PO = "PO00000001"
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # the USDT contract on TRON

type Step = tuple[str, int, dict[str, Any]]


def fold(*steps: Step) -> Any:
    return payout_state(PO, [PayoutEvent(PO, kind, ts, detail) for kind, ts, detail in steps])


REQUESTED: Step = ("requested", 1, {"approvals_required": 2})
TWO: tuple[Step, ...] = (REQUESTED, ("approved", 2, {"by": "s1"}), ("approved", 3, {"by": "s2"}))


# --- the process ----------------------------------------------------------------------


def test_requested_approved_twice_debited_sent_paid() -> None:
    assert fold(REQUESTED).status == "requested"
    one = fold(REQUESTED, ("approved", 2, {"by": "s1"}))
    assert (one.status, one.approvals) == ("requested", ("s1",))  # one of two
    assert (fold(*TWO).status, fold(*TWO).approvals) == ("approved", ("s1", "s2"))
    debited = fold(*TWO, ("rechecked", 4, {}), ("debit_attempted", 5, {}), ("debited", 6, {}))
    assert (debited.status, debited.debited_at) == ("debited", 6)
    retry = (*TWO, ("debited", 6, {}), ("send_failed", 7, {}))
    assert fold(*retry).status == "debited"  # sent again later
    sent = fold(*retry, ("sent", 8, {"ref": "r1"}))
    assert (sent.status, sent.provider_ref) == ("sent", "r1")
    paid = fold(*retry, ("sent", 8, {"ref": "r1"}), ("paid", 9, {"tx": "t"}))
    assert (paid.status, paid.paid_at, paid.debited_at, paid.failure) == ("paid", 9, 6, None)


def test_denied_cancelled_or_failed() -> None:
    assert fold(REQUESTED, ("denied", 2, {"by": "s1", "reason": "r"})).failure == "r"
    assert fold(*TWO, ("denied", 4, {"by": "s3", "reason": "late"})).status == "denied"
    assert fold(REQUESTED, ("cancelled", 2, {"by": "u1"})).status == "cancelled"
    failed = fold(*TWO, ("failed", 4, {"reason": "the amount changed"}))
    assert (failed.status, failed.failure) == ("failed", "the amount changed")
    assert fold(REQUESTED, ("failed", 2, {"reason": "x"})).status == "failed"
    refused = fold(*TWO, ("debit_attempted", 4, {}), ("failed", 5, {"reason": "engine"}))
    assert refused.status == "failed"  # the engine refused the debit
    retried = fold(*TWO, ("debit_attempted", 4, {}), ("debit_attempted", 5, {}), ("debited", 6, {}))
    assert retried.status == "debited"


@pytest.mark.parametrize(
    "steps",
    [
        (),
        (("approved", 1, {"by": "s1"}),),  # no request
        (REQUESTED, REQUESTED),
        (("requested", 1, {"approvals_required": 0}),),
        (REQUESTED, ("approved", 2, {"by": "s1"}), ("approved", 3, {"by": "s1"})),  # twice
        (*TWO, ("approved", 4, {"by": "s3"})),  # already approved
        (*TWO, ("cancelled", 4, {"by": "u1"})),  # approved: too late to cancel
        (REQUESTED, ("debited", 2, {})),  # not approved
        (REQUESTED, ("rechecked", 2, {})),
        (REQUESTED, ("debit_attempted", 2, {})),
        (*TWO, ("sent", 4, {"ref": "r"})),  # not debited
        (*TWO, ("debited", 4, {}), ("paid", 5, {})),  # not sent
        (*TWO, ("debited", 4, {}), ("failed", 5, {"reason": "x"})),  # the money is out
        (*TWO, ("debited", 4, {}), ("denied", 5, {"by": "s3", "reason": "x"})),
        (*TWO, ("debit_attempted", 4, {}), ("denied", 5, {"by": "s3", "reason": "x"})),
        (REQUESTED, ("cancelled", 2, {}), ("approved", 3, {"by": "s1"})),
        (REQUESTED, ("paid_twice", 2, {})),
    ],
)
def test_histories_no_valid_payout_has_are_refused(steps: tuple[Step, ...]) -> None:
    with pytest.raises(PayoutError):
        fold(*steps)


PASS_AUTHORISE: Step = ("requested", 1, {"approvals_required": 2, "approval": "pass_authorise"})
PASSED: Step = ("approved", 2, {"by": "s1", "as": "pass", "tier": 2})


def test_a_senior_passes_then_an_admin_authorises() -> None:
    """[decided 31 Oct 2026]: a T2 or above passes, an admin authorises."""
    assert fold(PASS_AUTHORISE).next_step == "pass"
    passed = fold(PASS_AUTHORISE, PASSED)
    assert (passed.status, passed.next_step, passed.approval) == (
        "requested",
        "authorise",
        "pass_authorise",
    )
    done = fold(PASS_AUTHORISE, PASSED, ("approved", 3, {"by": "a1", "as": "authorise"}))
    assert (done.status, done.approvals, done.next_step) == ("approved", ("s1", "a1"), None)
    assert fold(*TWO).next_step is None  # requested under the count rule


@pytest.mark.parametrize(
    "steps",
    [
        (PASS_AUTHORISE, ("approved", 2, {"by": "a1", "as": "authorise"})),  # no pass yet
        (PASS_AUTHORISE, ("approved", 2, {"by": "s1"})),  # no step named
        (PASS_AUTHORISE, PASSED, ("approved", 3, {"by": "s2", "as": "pass"})),  # passed twice
        (PASS_AUTHORISE, PASSED, ("approved", 3, {"by": "s1", "as": "authorise"})),  # same one
        (REQUESTED, ("approved", 2, {"by": "s1", "as": "pass"})),  # the count rule has no steps
        (("requested", 1, {"approvals_required": 3, "approval": "pass_authorise"}),),
        (("requested", 1, {"approvals_required": 2, "approval": "anyone"}),),
    ],
)
def test_pass_and_authorise_out_of_order_are_refused(steps: tuple[Step, ...]) -> None:
    with pytest.raises(PayoutError):
        fold(*steps)


def test_an_event_of_another_payout_is_refused() -> None:
    with pytest.raises(PayoutError):
        payout_state(PO, [PayoutEvent("PO00000002", "requested", 1, {"approvals_required": 1})])


def test_the_migration_allows_exactly_the_kinds_and_statuses() -> None:
    sql = dict(_MIGRATIONS)[6]
    kinds = re.search(r"kind IN \(('requested'[^)]*)\)", sql)
    statuses = re.search(r"status IN \(('requested'[^)]*)\)", sql)
    assert kinds is not None and statuses is not None
    assert tuple(re.findall(r"'([a-z_]+)'", kinds.group(1))) == PAYOUT_KINDS
    assert tuple(re.findall(r"'([a-z_]+)'", statuses.group(1))) == PAYOUT_STATUSES


# --- the wallet -----------------------------------------------------------------------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58check(body: bytes) -> str:
    raw = body + hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    number, text = int.from_bytes(raw, "big"), ""
    while number:
        number, rest = divmod(number, 58)
        text = _B58[rest] + text
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + text


def test_tron_addresses() -> None:
    assert tron_address_valid(USDT)
    assert tron_address_valid(b58check(b"\x41" + bytes(range(20))))
    assert b58check(b"\x41" + bytes(20)) == "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
    assert tron_address_valid("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb")
    assert not tron_address_valid(USDT[:-1] + "u")  # checksum
    assert not tron_address_valid(USDT[:-1])  # length
    assert not tron_address_valid(USDT[:5] + "0" + USDT[6:])  # not base 58
    assert not tron_address_valid("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")  # bitcoin
    other = b58check(b"\x42" + bytes(range(20)))  # another version byte
    assert len(other) == 34 and not tron_address_valid(other)
    assert not tron_address_valid("T" + "z" * 33)  # too large for 25 bytes
    assert not tron_address_valid(123)  # type: ignore[arg-type]


def test_mfa_is_read_from_the_amr_claim() -> None:
    assert {"otp", "webauthn"} <= SECOND_FACTORS and "pwd" not in SECOND_FACTORS


# --- the engine -------------------------------------------------------------------------

T0 = 1_790_000_000_000


class Rig:
    def __init__(self) -> None:
        self.machine = Machine(CODEC.encode(setup_from_policy(load_policy())), {1: bytes(32)})
        self.machine.use_price_key(1)
        self.inputs: list[Any] = []
        self.seq = 0

    def __call__(self, item: Any) -> list[Any]:
        self.inputs.append(item)
        record = self.machine.apply(f"{len(self.inputs)}-0", item).record
        events = CODEC.decode(record.events)
        assert isinstance(events, tuple)
        return list(events)

    def price(self, ts: int, mid: int) -> None:
        self.seq += 1
        self(PriceUpdate("EUR/USD", self.seq, ts, ts, mid))


def refused(events: list[Any]) -> InputRejectReason:
    (event,) = events
    assert isinstance(event, InputRejected)
    return event.reason


def test_a_payout_debit_takes_the_profit_once_and_lowers_the_floors() -> None:
    rig = Rig()
    rig(OpenAccount("A", "lite-funded-v4", 1_000_000, T0))
    rig.price(T0, 110_000)
    request = OrderRequest("o1", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100)
    (opened,) = [e for e in rig(PlaceOrder(request, T0 + 1)) if isinstance(e, PositionOpened)]
    for step in range(1, 21):
        rig.price(T0 + 1 + step, 110_000 + 15 * step)
    assert refused(rig(DebitPayout("A", 100, PO, T0 + 30))) is InputRejectReason.POSITIONS_OPEN
    closed = [
        e
        for e in rig(ClosePosition("A", opened.position_id, T0 + 31))
        if isinstance(e, PositionClosed)
    ]
    engine = rig.machine.supervisor.engine
    profit = engine.balance("A") - 1_000_000
    assert profit > 20_000 and closed
    too_much = rig(DebitPayout("A", profit + 1, PO, T0 + 32))
    assert refused(too_much) is InputRejectReason.PAYOUT_EXCEEDS_PROFIT
    before = next(a for a in rig.machine.supervisor.state().accounts if a.account_id == "A")

    events = rig(DebitPayout("A", profit, PO, T0 + 33))
    (debited,) = [e for e in events if isinstance(e, PayoutDebited)]
    assert (debited.account_id, debited.payout, debited.amount_cents) == ("A", PO, profit)
    assert (debited.balance_cents, debited.rsv_id, debited.ts) == (
        1_000_000,
        "lite-funded-v4",
        T0 + 33,
    )
    assert engine.balance("A") == 1_000_000 and engine.has_entry(payout_entry(PO))
    entry = rig.machine.ledger.entries()[-1]
    assert (entry.entry_id, entry.kind, entry.reference) == (f"payout:{PO}", "payout", PO)
    assert rig.machine.ledger.balances()[FIRM_PAYOUTS] == profit
    after = next(a for a in rig.machine.supervisor.state().accounts if a.account_id == "A")
    assert (after.day_base, after.high_water) == (
        before.day_base - profit,
        before.high_water - profit,
    )
    assert rig.machine.supervisor.status("A").value == "active"  # no breach from the payout

    again = rig(DebitPayout("A", profit, PO, T0 + 34))
    assert refused(again) is InputRejectReason.DUPLICATE_PAYOUT
    assert engine.balance("A") == 1_000_000
    other = rig(DebitPayout("A", 1, "PO00000002", T0 + 35))
    assert refused(other) is InputRejectReason.PAYOUT_EXCEEDS_PROFIT  # back at the baseline
    unknown = rig(DebitPayout("Z", 1, "PO00000003", T0 + 36))
    assert refused(unknown) is InputRejectReason.UNKNOWN_ACCOUNT

    rig(VoidAccount("A", "CAS00000001", T0 + 37))
    late = rig(DebitPayout("A", profit, PO, T0 + 38))
    assert refused(late) is InputRejectReason.DUPLICATE_PAYOUT  # a retry, whatever came after
    voided = rig(DebitPayout("A", 1, "PO00000004", T0 + 39))
    assert refused(voided) is InputRejectReason.ACCOUNT_NOT_ACTIVE

    replay = Rig()
    for item in rig.inputs:
        replay(item)
    assert replay.machine.head == rig.machine.head
    state = rig.machine.state()
    assert CODEC.decode(CODEC.encode(state)) == state


def test_debit_input_validation() -> None:
    with pytest.raises(ValueError):
        DebitPayout("A", 0, PO, T0)
    with pytest.raises(ValueError):
        DebitPayout("", 1, PO, T0)
    with pytest.raises(ValueError):
        DebitPayout("A", 1, "", T0)
    assert DebitPayout("A", 1, PO, T0).amount_cents == 1
