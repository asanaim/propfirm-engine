"""Automatic risk limits sent to the engine (build plan step 5, line 6;
D-14.34).

Every minute the API loop builds the whole set the platform has decided:
- each account's class in force (D-14.5, D-14.28): class C at
  `c_lot_percent` of its lot caps, class D at `d_lot_percent` (0: close
  only); A and B at full caps;
- each exposure cut in force (D-14.32): halve and quarter, at
  `halve_lot_percent` and `quarter_lot_percent`, for the classes the cut
  binds.
It goes to the engine as one journaled SetRiskLimits input, with the
settings version it was decided under, whenever it differs from the set
last sent (and once after start, since the engine may hold an older one).
The engine applies it to new exposure; the journal replays every refusal.
"""

import logging
from typing import Any, Protocol

import asyncpg

from propfirm.customers.exposure import CLASSES_CUT, Cut
from propfirm.journal.inputs import Input, SetRiskLimits
from propfirm.trading.orders import Side
from propfirm.trading.risk import FULL, RiskCut, RiskLimits

log = logging.getLogger(__name__)


class Gateway(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Any: ...


class Settings(Protocol):
    async def current(self) -> Any: ...

    async def version(self) -> int: ...


class Cuts(Protocol):
    async def cuts(self) -> dict[tuple[str, str], Cut]: ...


def build(
    version: int,
    classes: dict[str, str],
    cuts: dict[tuple[str, str], Cut],
    class_policy: Any,
    exposure_policy: Any,
) -> RiskLimits:
    """The set from classes and cuts in force (pure)."""
    percents = {
        level: pct
        for level, pct in (
            ("halve", exposure_policy.halve_lot_percent),
            ("quarter", exposure_policy.quarter_lot_percent),
        )
    }
    class_percent = tuple(
        (cls, pct)
        for cls, pct in (("C", class_policy.c_lot_percent), ("D", class_policy.d_lot_percent))
        if pct < FULL
    )
    return RiskLimits(
        version,
        tuple(sorted((acc, cls) for acc, cls in classes.items() if cls != "B")),
        class_percent,
        tuple(
            RiskCut(symbol, Side(side), percents[c.level], tuple(sorted(CLASSES_CUT[c.level])))
            for (symbol, side), c in sorted(cuts.items())
        ),
    )


class RiskSync:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        settings: Settings,
        exposure: Cuts | None,
        gateway: Gateway,
        wait_s: float = 5.0,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._settings = settings
        self._exposure = exposure
        self._gateway = gateway
        self._wait_s = wait_s
        self._sent: RiskLimits | None = None
        self._last = -1

    async def _classes(self) -> dict[str, str]:
        """Each account's class in force, whoever set it (latest change)."""
        rows = await self._pool.fetch(
            f"SELECT DISTINCT ON (acc) acc, class FROM {self._s}.account_class_changes "
            f"ORDER BY acc, at DESC"
        )
        return {str(r["acc"]): str(r["class"]) for r in rows}

    async def wanted(self) -> RiskLimits:
        current = await self._settings.current()
        cuts = await self._exposure.cuts() if self._exposure is not None else {}
        return build(
            await self._settings.version(),
            await self._classes(),
            cuts,
            current.classes,
            current.exposure,
        )

    async def sync(self, now: int, every_ms: int = 60_000) -> bool:
        """Send the set when it changed; True when sent and applied."""
        if self._last >= 0 and now - self._last < every_ms:
            return False
        self._last = now
        limits = await self.wanted()
        if limits == self._sent:
            return False
        outcome = await self._gateway.submit(SetRiskLimits(limits, now), self._wait_s)
        applied = any(e.get("type") == "RiskLimitsSet" for e in outcome.events)
        if applied:
            self._sent = limits
        else:
            log.warning("risk limits not applied: %s", outcome.events)
        return applied
