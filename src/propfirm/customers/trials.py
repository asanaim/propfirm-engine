"""Free trial expiry (D-13.17): a trial account ends `days` days after it
opened. The check runs with the lifecycle loop, at most hourly; the engine
closes the positions at the current prices and locks the account
(`ExpireAccount`, status expired). Not a breach, so no email (D-14.1)."""

import logging
from typing import Any, Protocol

from propfirm.customers.store import CustomerStore
from propfirm.journal.inputs import ExpireAccount
from propfirm.policy import PolicySource
from propfirm.service.gateway import Outcome

log = logging.getLogger(__name__)

DAY_MS = 86_400_000
LOOKBACK_DAYS = 30  # trials older than days + this were expired long ago


class Gateway(Protocol):
    async def publish(self, item: ExpireAccount) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class LiveAccounts(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


class Trials:
    def __init__(
        self,
        store: CustomerStore,
        policy: PolicySource,
        live: LiveAccounts,
        gateway: Gateway,
        wait_s: float = 5.0,
    ) -> None:
        self._store = store
        self._policy = policy
        self._live = live
        self._gateway = gateway
        self._wait_s = wait_s
        self._last = -1

    async def expire_due(self, now: int, every_ms: int = 3_600_000) -> list[str]:
        """End every active trial opened `days` days or more before `now`;
        return their ACC ids. Runs at most once per `every_ms`."""
        if self._last >= 0 and now - self._last < every_ms:
            return []
        self._last = now
        days = (await self._policy.current()).trial.days
        cutoff = now - days * DAY_MS
        due = await self._store.trials_opened(cutoff - LOOKBACK_DAYS * DAY_MS, cutoff + 1)
        ended = []
        for acc in due:
            view = await self._live.account(acc)
            if view is None or view.get("status") != "active":
                continue
            entry = await self._gateway.publish(ExpireAccount(acc, now))
            outcome = await self._gateway.outcome(entry, self._wait_s)
            if any(e.get("type") == "AccountExpired" for e in outcome.events):
                ended.append(acc)
            else:
                log.warning("trial %s not expired: %s", acc, outcome.events)
        return ended
