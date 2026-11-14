"""The public leaderboard (14 Nov 2026), as FTMO's: funded accounts ranked
by their gain on the starting balance, highest first.

Each row shows a display name (first name and the initial of the last
name: "Ada T."), the country, the product and size, the gain in percent
and in dollars. Only funded accounts that are active are ranked; a trader
can opt out from their profile and is then left off. Equity comes from the
engine's live view, so the board follows the market; it is read at most
once per `TTL_MS` and served from memory between reads.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from propfirm.customers.store import AccountRecord

TTL_MS = 60_000
TOP = 20


def display_name(full_name: str) -> str:
    """First name and the last name's initial: "Ada Lovelace" -> "Ada L."."""
    parts = full_name.split()
    if not parts:
        return "Trader"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."


@dataclass(frozen=True, slots=True)
class Row:
    rank: int
    name: str
    country: str
    product_id: str
    size_cents: int
    gain_cents: int
    gain_basis_points: int  # hundredths of a percent, rounded toward zero

    def view(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "name": self.name,
            "country": self.country,
            "product_id": self.product_id,
            "size_cents": self.size_cents,
            "gain_cents": self.gain_cents,
            "gain_basis_points": self.gain_basis_points,
        }


def rank(
    entrants: Iterable[tuple[AccountRecord, str, str]],
    live: dict[str, dict[str, Any]],
    top: int = TOP,
) -> list[Row]:
    """Rank active funded accounts by gain (equity over the starting
    balance), highest first; ties by the account number. Accounts without a
    live view or not active are left out. Rounding: basis points toward
    zero (named; the dollars are exact)."""
    scored: list[tuple[int, int, str, AccountRecord, str, str]] = []
    for account, name, country in entrants:
        view = live.get(account.acc)
        if view is None or view.get("status") != "active":
            continue
        start = int(view["initial_balance_cents"])
        if start <= 0:
            continue
        gain = int(view["equity_cents"]) - start
        bp = int(gain * 10_000 / start) if gain >= 0 else -int(-gain * 10_000 / start)
        scored.append((bp, gain, account.acc, account, name, country))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [
        Row(i, display_name(name), country, a.product_id, a.size_cents, gain, bp)
        for i, (bp, gain, _, a, name, country) in enumerate(scored[:top], start=1)
    ]


class Leaderboard:
    def __init__(
        self,
        entrants: Callable[[], Awaitable[list[tuple[AccountRecord, str, str]]]],
        account: Callable[[str], Awaitable[dict[str, Any] | None]],
    ) -> None:
        self._entrants = entrants
        self._account = account
        self._cached: tuple[int, list[Row]] | None = None

    async def rows(self, now: int) -> list[Row]:
        if self._cached is not None and now - self._cached[0] < TTL_MS:
            return self._cached[1]
        entrants = await self._entrants()
        live: dict[str, dict[str, Any]] = {}
        for account, _, _ in entrants:
            view = await self._account(account.acc)
            if view is not None:
                live[account.acc] = view
        rows = rank(entrants, live)
        self._cached = (now, rows)
        return rows
