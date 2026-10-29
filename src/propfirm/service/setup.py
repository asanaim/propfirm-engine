"""The engine setup each environment runs.

`development_setup` is the P2 setup: all 30 launch symbols with the engine's
default instruments, every product rule set, the default feed guards, and
an empty news calendar (events arrive as ScheduleNews inputs). Prices in
development are synthetic only (CLAUDE.md).

`config_version` names the setup; changing anything in it starts a new
journal (the engine refuses a journal written under another setup).
"""

from propfirm.journal.setup import EngineSetup, installed_tzdata
from propfirm.market.guards import GuardConfig
from propfirm.rules.products import RULESETS
from propfirm.trading.instruments import default_instruments

DEVELOPMENT_VERSION = "dev-p2-1"


def development_setup() -> EngineSetup:
    instruments = default_instruments()
    return EngineSetup(
        config_version=DEVELOPMENT_VERSION,
        instruments=tuple(instruments.values()),
        rule_sets=tuple(RULESETS.values()),
        guard=GuardConfig(),
        news_windows=(),
        trade_blocks=(),
        tzdata_version=installed_tzdata(),
    )
