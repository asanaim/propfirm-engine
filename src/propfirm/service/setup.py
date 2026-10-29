"""The engine setup, and policy changes as journaled inputs.

A new journal starts from the policy file: its trading settings and every
rule set it lists (`setup_from_policy`). After that the setup is fixed, and
the engine service turns later edits of the file into inputs at start-up
(`policy_changes`):

- a rule-set version not yet known → AddRuleSet;
- a known version whose numbers differ → refused: versions never change, so
  the file must raise that stage's `version`;
- trading settings that differ from those in force → UpdateTrading, which
  needs a new `trading.config_version`.

Prices in development are synthetic only (CLAUDE.md).
"""

from propfirm.journal.inputs import AddRuleSet, Input, UpdateTrading
from propfirm.journal.machine import Machine
from propfirm.journal.setup import EngineSetup, installed_tzdata
from propfirm.policy import DEFAULT_POLICY, Policy, PolicyError, load_policy


def setup_from_policy(policy: Policy) -> EngineSetup:
    return EngineSetup(
        config_version=policy.trading.config_version,
        instruments=policy.trading.instruments,
        rule_sets=policy.rule_sets(),
        guard=policy.trading.guard,
        news_windows=(),
        trade_blocks=(),
        tzdata_version=installed_tzdata(),
    )


def development_setup() -> EngineSetup:
    """The setup of a new journal under the shipped policy file."""
    return setup_from_policy(load_policy(DEFAULT_POLICY))


def policy_changes(policy: Policy, machine: Machine, ts: int) -> list[Input]:
    """The inputs that bring the engine in line with `policy`; raise
    PolicyError for a change that needs a new version number first."""
    changes: list[Input] = []
    known = machine.rule_sets
    for rule_set in policy.rule_sets():
        current = known.get(rule_set.rsv_id)
        if current is None:
            changes.append(AddRuleSet(rule_set, ts))
        elif current != rule_set:
            raise PolicyError(
                f"rule set {rule_set.rsv_id} is already published with other numbers: "
                f"raise the stage's version in the policy file"
            )
    engine = machine.supervisor.engine
    trading = policy.trading
    in_force = engine.instruments
    changed = tuple(spec for spec in trading.instruments if in_force.get(spec.name) != spec)
    guard_changed = trading.guard != engine.guard_config
    if changed or guard_changed or trading.config_version != engine.config_version:
        if trading.config_version == engine.config_version:
            raise PolicyError(
                "trading settings changed: set a new trading.config_version in the policy file"
            )
        guard = trading.guard if guard_changed else None
        changes.append(UpdateTrading(trading.config_version, changed, guard, ts))
    return changes
