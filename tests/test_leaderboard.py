"""The public leaderboard (14 Nov 2026): ranking and names."""

from typing import Any

from propfirm.customers.leaderboard import display_name, rank
from propfirm.customers.store import AccountRecord


def account(acc: str, size: int = 10_000_000) -> AccountRecord:
    return AccountRecord(
        acc, "CIF00000001", "ORD00000001", "lite", "lite-funded-v4", size, 0, "funded"
    )


def live(equity: int, start: int = 10_000_000, status: str = "active") -> dict[str, Any]:
    return {"status": status, "equity_cents": equity, "initial_balance_cents": start}


def test_names_show_the_first_name_and_an_initial() -> None:
    assert display_name("Ada Lovelace") == "Ada L."
    assert display_name("Md. Abu Sayed naim") == "Md. N."
    assert display_name("Cher") == "Cher"
    assert display_name("   ") == "Trader"


def test_active_funded_accounts_are_ranked_by_gain() -> None:
    entrants = [
        (account("ACC1"), "Ada Lovelace", "GB"),
        (account("ACC2", 2_500_000), "Bo Chen", "SG"),
        (account("ACC3"), "Cy Dee", "US"),
        (account("ACC4"), "Di Eve", "DE"),  # breached: left off
        (account("ACC5"), "No View", "FR"),  # no live view: left off
    ]
    views = {
        "ACC1": live(10_500_000),  # +5.00 %
        "ACC2": live(2_700_000, 2_500_000),  # +8.00 %
        "ACC3": live(9_900_000),  # -1.00 %
        "ACC4": live(12_000_000, status="breached"),
    }
    rows = rank(entrants, views)
    assert [(r.rank, r.name, r.gain_basis_points, r.gain_cents) for r in rows] == [
        (1, "Bo C.", 800, 200_000),
        (2, "Ada L.", 500, 500_000),
        (3, "Cy D.", -100, -100_000),
    ]
    assert rank(entrants, views, top=1)[0].name == "Bo C."


def test_basis_points_round_toward_zero_and_ties_go_by_amount() -> None:
    entrants = [(account("ACC1"), "A B", "GB"), (account("ACC2", 20_000_000), "C D", "GB")]
    views = {"ACC1": live(10_000_999), "ACC2": live(20_001_998, 20_000_000)}
    rows = rank(entrants, views)
    assert [r.gain_basis_points for r in rows] == [0, 0]  # 0.99 bp each, toward zero
    assert [r.name for r in rows] == ["C D.", "A B."]
    assert rows[0].gain_cents == 1_998  # the larger amount first on a tie
