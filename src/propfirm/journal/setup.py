"""The engine setup: everything besides the inputs that decides outputs.

A journal is bound to one setup. The setup is stored canonically with the
journal, its fingerprint (SHA-256 of that text) is the first link of the
hash chain, and replay builds the engine from the stored setup, so a journal
always replays under the configuration it was written with.

`tzdata_version` pins the time-zone rules: a different tzdata release could
move a 17:00 New York boundary and change outcomes, so the engine refuses to
run a journal under another release.

Limits in P1 [DEFAULT]: the setup is fixed for the life of a journal. The
restricted-events calendar (trade blocks, news spreads) and new rule-set
versions will become inputs, so they can change without a new journal.
"""

from dataclasses import dataclass
from importlib.metadata import version

from propfirm._checks import require_text
from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import NewsWindow
from propfirm.rules.products import RuleSet
from propfirm.rules.supervisor import RuleSupervisor
from propfirm.trading.engine import TradingEngine
from propfirm.trading.instruments import InstrumentSpec


class SetupError(ValueError):
    """A setup that cannot run here, or that does not match the journal's."""


def installed_tzdata() -> str:
    return version("tzdata")


@dataclass(frozen=True, slots=True)
class EngineSetup:
    config_version: str
    instruments: tuple[InstrumentSpec, ...]
    rule_sets: tuple[RuleSet, ...]
    guard: GuardConfig
    news_windows: tuple[NewsWindow, ...]
    trade_blocks: tuple[NewsWindow, ...]
    tzdata_version: str

    def __post_init__(self) -> None:
        require_text("config_version", self.config_version)
        require_text("tzdata_version", self.tzdata_version)
        for name in ("instruments", "rule_sets", "news_windows", "trade_blocks"):
            if not isinstance(getattr(self, name), tuple):
                raise TypeError(f"{name} must be a tuple")
        names = [i.name for i in self.instruments]
        if not names or len(set(names)) != len(names):
            raise ValueError("instruments must be non-empty with unique symbols")
        ids = [r.rsv_id for r in self.rule_sets]
        if len(set(ids)) != len(ids):
            raise ValueError("rule-set ids must be unique")

    def rule_set_map(self) -> dict[str, RuleSet]:
        return {r.rsv_id: r for r in self.rule_sets}


def build(setup: EngineSetup) -> tuple[RuleSupervisor, Ledger]:
    """A fresh supervisor and empty ledger for `setup`."""
    installed = installed_tzdata()
    if setup.tzdata_version != installed:
        raise SetupError(f"setup needs tzdata {setup.tzdata_version}, installed {installed}")
    ledger = Ledger()
    engine = TradingEngine(
        {i.name: i for i in setup.instruments},
        ledger,
        config_version=setup.config_version,
        guard_config=setup.guard,
        news_windows=setup.news_windows,
        trade_blocks=setup.trade_blocks,
    )
    return RuleSupervisor(engine), ledger
