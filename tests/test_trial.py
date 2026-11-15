"""Free trial (D-13.17): Lite stage 1 rules on its own rule set, 14 days,
one per customer, never progressing; the engine ends it with ExpireAccount."""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from propfirm.customers.lifecycle import plan_for
from propfirm.customers.lifecycle_store import Plan
from propfirm.customers.trials import DAY_MS, LOOKBACK_DAYS, Trials
from propfirm.journal.inputs import ExpireAccount, InputRejected, InputRejectReason, OpenAccount
from propfirm.journal.machine import CODEC
from propfirm.market.types import PriceUpdate
from propfirm.policy import PolicyError, load_policy, parse_policy, stage_of
from propfirm.rules.addons import is_trial, trial_id
from propfirm.rules.events import AccountExpired
from propfirm.rules.products import Stage
from propfirm.service.gateway import Outcome
from propfirm.trading.events import CloseReason, OrderRejected, PositionClosed, RejectReason
from tests.test_cases import T0, buy, run
from tests.test_policy import edited
from tests.test_progression import account

POLICY = load_policy()
TRIAL = POLICY.trial_rule_set().rsv_id


def test_the_shipped_trial() -> None:
    t = POLICY.trial
    assert (t.on, t.product_id, t.size_cents, t.days) == (True, "lite", 1_000_000, 14)
    first = POLICY.product("lite").rule_sets[0]
    assert TRIAL == trial_id(first.rsv_id) == f"{first.rsv_id}-trial"
    # Lite stage 1 rules exactly, under its own id
    assert replace(POLICY.trial_rule_set(), rsv_id=first.rsv_id) == first
    assert stage_of(TRIAL) is Stage.S1 and is_trial(TRIAL) and not is_trial(first.rsv_id)
    assert POLICY.published_rule_sets()[-1].rsv_id == TRIAL


def test_no_trial_without_the_table_or_when_off() -> None:
    def off(data: dict[str, Any]) -> None:
        data["trial"]["on"] = False

    policy = parse_policy(edited(off))
    assert not any(is_trial(r.rsv_id) for r in policy.published_rule_sets())

    def gone(data: dict[str, Any]) -> None:
        del data["trial"]

    assert parse_policy(edited(gone)).trial.on is False


def test_a_trial_size_the_product_does_not_sell_is_refused() -> None:
    def bad(data: dict[str, Any]) -> None:
        data["trial"]["size_cents"] = 123

    with pytest.raises(PolicyError, match="trial"):
        parse_policy(edited(bad))


def test_a_trial_never_progresses() -> None:
    trial = account("lite", "s1", TRIAL)
    assert plan_for(POLICY, trial) == Plan(None, None, "free_trial")


def test_expiry_closes_positions_and_locks_the_account() -> None:
    inputs = [
        OpenAccount("A", TRIAL, 1_000_000, T0),
        PriceUpdate("EUR/USD", 1, T0, T0, 110_000),
        buy("o1", T0 + 1),
        PriceUpdate("EUR/USD", 2, T0 + 2, T0 + 2, 110_100),
        ExpireAccount("A", T0 + 3),
        buy("o2", T0 + 4),
        ExpireAccount("A", T0 + 5),
        ExpireAccount("Z", T0 + 6),
    ]
    machine, out = run(inputs)
    expired, closed = out[4][0], out[4][1]
    assert isinstance(expired, AccountExpired) and expired.rsv_id == TRIAL
    assert isinstance(closed, PositionClosed) and closed.reason is CloseReason.EXPIRY
    (refused,) = out[5]
    assert isinstance(refused, OrderRejected) and refused.reason is RejectReason.ACCOUNT_LOCKED
    (again,) = out[6]
    assert isinstance(again, InputRejected) and again.reason is InputRejectReason.ACCOUNT_NOT_ACTIVE
    (unknown,) = out[7]
    assert isinstance(unknown, InputRejected)
    assert unknown.reason is InputRejectReason.UNKNOWN_ACCOUNT
    assert machine.supervisor.status("A").value == "expired"
    replayed, _ = run(inputs)
    assert replayed.head == machine.head
    state = machine.state()
    assert CODEC.decode(CODEC.encode(state)) == state
    with pytest.raises(ValueError):
        ExpireAccount("", T0)


# --- the expiry job ---------------------------------------------------------------------


class FakeStore:
    def __init__(self, opened: dict[str, int]) -> None:
        self.opened = opened
        self.asked: list[tuple[int, int]] = []

    async def trials_opened(self, since: int, before: int) -> list[str]:
        self.asked.append((since, before))
        due = [(ts, a) for a, ts in self.opened.items() if since <= ts < before]
        return [a for _, a in sorted(due)]  # oldest first, as the store


class FakePolicy:
    async def current(self) -> Any:
        return POLICY


class FakeLive:
    def __init__(self, status: dict[str, str]) -> None:
        self.status = status

    async def account(self, account_id: str) -> dict[str, Any] | None:
        s = self.status.get(account_id)
        return None if s is None else {"status": s}


class FakeGateway:
    def __init__(self) -> None:
        self.sent: list[ExpireAccount] = []

    async def publish(self, item: ExpireAccount) -> str:
        self.sent.append(item)
        return f"e{len(self.sent)}"

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome:
        return Outcome(entry_id, 1, [{"type": "AccountExpired"}])


def test_the_job_ends_active_trials_past_their_days_hourly() -> None:
    now = T0 + 20 * DAY_MS
    store = FakeStore(
        {
            "ACC1": now - 14 * DAY_MS,  # exactly 14 days: due
            "ACC2": now - 14 * DAY_MS + 1,  # one millisecond short
            "ACC3": now - 15 * DAY_MS,  # due, but already breached
            "ACC4": now - 16 * DAY_MS,  # due
        }
    )
    live = FakeLive({"ACC1": "active", "ACC2": "active", "ACC3": "breached", "ACC4": "active"})
    gateway = FakeGateway()
    trials = Trials(store, FakePolicy(), live, gateway)  # type: ignore[arg-type]

    async def go() -> None:
        assert await trials.expire_due(now) == ["ACC4", "ACC1"]
        assert [g.account_id for g in gateway.sent] == ["ACC4", "ACC1"]
        assert gateway.sent[0].ts == now
        cutoff = now - 14 * DAY_MS
        assert store.asked == [(cutoff - LOOKBACK_DAYS * DAY_MS, cutoff + 1)]
        assert await trials.expire_due(now + 3_599_999) == []  # within the hour: not run
        assert len(store.asked) == 1

    asyncio.run(go())
