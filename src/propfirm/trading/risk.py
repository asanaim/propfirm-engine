"""Automatic risk limits on new exposure (build plan step 5, line 6;
D-14.34).

The platform decides them (trader classes, D-14.28; exposure desk cuts,
D-14.32) and sends the whole set to the engine as one journaled input with
the settings version it was decided under, so every refusal they cause
replays. The engine applies them to orders that add exposure only: a close
or a stop change is never limited, and open positions are untouched.

For an order of account a on symbol s and side d, the lot caps are scaled
to the smallest percent that applies (not a product: two reasons to cut
do not compound):
- the account's class percent (class B, the default, is 100);
- every cut on (s, d) that lists the account's class.

Percent 0 means close only. Caps scale down and round down to whole
hundredths of a lot (`limited_cap`).
"""

from dataclasses import dataclass

from propfirm._checks import require_int, require_text
from propfirm.trading.orders import Side

CLASSES = ("A", "B", "C", "D")
FULL = 100


@dataclass(frozen=True, slots=True)
class RiskCut:
    """Lot caps on one symbol and side cut to `percent` for `classes`."""

    symbol: str
    side: Side
    percent: int
    classes: tuple[str, ...]  # sorted

    def __post_init__(self) -> None:
        require_text("symbol", self.symbol)
        if not isinstance(self.side, Side):
            raise TypeError("side must be a Side")
        require_int("percent", self.percent, minimum=0)
        if self.percent >= FULL:
            raise ValueError("a cut is below 100 percent")
        if not self.classes or tuple(sorted(set(self.classes))) != self.classes:
            raise ValueError("classes must be sorted, unique and not empty")
        if not set(self.classes) <= set(CLASSES):
            raise ValueError("unknown class")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """The whole set in force; each input replaces the one before."""

    policy_version: int
    classes: tuple[tuple[str, str], ...]  # (account id, class), sorted; class B left out
    class_percent: tuple[tuple[str, int], ...]  # (class, percent), sorted; missing: 100
    cuts: tuple[RiskCut, ...]  # sorted by symbol, then side

    def __post_init__(self) -> None:
        require_int("policy_version", self.policy_version, minimum=0)
        accounts = [a for a, _ in self.classes]
        if accounts != sorted(set(accounts)):
            raise ValueError("classes must be sorted by account, one each")
        for account, cls in self.classes:
            require_text("account", account)
            if cls not in CLASSES or cls == "B":
                raise ValueError("an account's class is A, C or D (B is the default)")
        names = [c for c, _ in self.class_percent]
        if names != sorted(set(names)) or not set(names) <= set(CLASSES):
            raise ValueError("class_percent must be sorted, one per known class")
        for _, percent in self.class_percent:
            require_int("percent", percent, minimum=0)
            if percent > FULL:
                raise ValueError("a percent is at most 100")
        keys = [(c.symbol, c.side.value) for c in self.cuts]
        if keys != sorted(set(keys)):
            raise ValueError("cuts must be sorted, one per symbol and side")

    def class_of(self, account_id: str) -> str:
        for account, cls in self.classes:
            if account == account_id:
                return cls
        return "B"

    def percent(self, account_id: str, symbol: str, side: Side) -> int:
        """The percent of its lot caps the account may use for new exposure."""
        cls = self.class_of(account_id)
        out = dict(self.class_percent).get(cls, FULL)
        for cut in self.cuts:
            if cut.symbol == symbol and cut.side is side and cls in cut.classes:
                out = min(out, cut.percent)
        return out


NO_LIMITS = RiskLimits(0, (), (), ())


def limited_cap(cap: int, percent: int) -> int:
    """A lot cap scaled to `percent`, rounded down to whole hundredths of a
    lot (never above the stated share)."""
    require_int("cap", cap, minimum=0)
    require_int("percent", percent, minimum=0)
    return cap * percent // FULL
