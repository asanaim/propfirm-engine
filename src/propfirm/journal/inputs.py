"""Engine inputs: everything that can change engine state.

The engine's outputs are a function of its setup and the ordered list of
these inputs, so journaling the inputs in order is enough to rebuild every
event and every ledger entry (deterministic replay). Each input carries its
own time; nothing reads a clock.

`apply` hands an input to the rules supervisor. Inputs the engine cannot
accept without raising (a price for an unconfigured symbol, an account that
already exists, an unknown rule set or size, a transport message that did
not decode) are answered with an `InputRejected` event before the engine
sees them, so a bad input is recorded and never changes state. Any other
exception is a defect: the runner stops rather than guess (fail-stop).
"""

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum

from propfirm._checks import require_int, require_text
from propfirm.market.types import PriceUpdate
from propfirm.rules.news import RestrictedEvent, event_window
from propfirm.rules.products import RuleSet
from propfirm.rules.supervisor import Event, RuleSupervisor
from propfirm.trading.orders import OrderRequest


def _ts(value: int) -> None:
    require_int("ts", value, minimum=0)


def _level(name: str, value: int | None) -> None:
    if value is not None:
        require_int(name, value, minimum=1)


@dataclass(frozen=True, slots=True)
class OpenAccount:
    """Open and fund an account on a rule-set version (from billing)."""

    account_id: str
    rsv_id: str
    initial_balance_cents: int
    ts: int

    def __post_init__(self) -> None:
        require_text("account_id", self.account_id)
        require_text("rsv_id", self.rsv_id)
        require_int("initial_balance_cents", self.initial_balance_cents, minimum=1)
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    request: OrderRequest
    ts: int

    def __post_init__(self) -> None:
        if not isinstance(self.request, OrderRequest):
            raise TypeError("request must be an OrderRequest")
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class CancelOrder:
    account_id: str
    order_id: str
    ts: int

    def __post_init__(self) -> None:
        require_text("account_id", self.account_id)
        require_text("order_id", self.order_id)
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class ClosePosition:
    """Close fully (volume None) or partly; the engine rejects a volume
    outside 1..open volume."""

    account_id: str
    position_id: str
    ts: int
    volume: int | None = None

    def __post_init__(self) -> None:
        require_text("account_id", self.account_id)
        require_text("position_id", self.position_id)
        _ts(self.ts)
        if self.volume is not None:
            require_int("volume", self.volume)


@dataclass(frozen=True, slots=True)
class ModifyStops:
    account_id: str
    position_id: str
    stop_loss: int | None
    take_profit: int | None
    ts: int

    def __post_init__(self) -> None:
        require_text("account_id", self.account_id)
        require_text("position_id", self.position_id)
        _level("stop_loss", self.stop_loss)
        _level("take_profit", self.take_profit)
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class Advance:
    """Time passes with no other input (a clock tick): runs the gap guard
    and the 17:00 New York day roll."""

    ts: int

    def __post_init__(self) -> None:
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class Outage:
    """The feed connection is down: every symbol freezes."""

    ts: int

    def __post_init__(self) -> None:
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class Malformed:
    """A transport message that did not decode into an input. Recorded so the
    record is complete (nothing received is dropped silently); it never
    reaches the engine.

    Attributes:
        raw: The message fields as received (canonical JSON of a str mapping;
            bytes that are not UTF-8 are backslash-escaped). "{}" when the
            entry was empty, e.g. trimmed from the stream before it was read.
        error: Why decoding failed.
    """

    raw: str
    error: str

    def __post_init__(self) -> None:
        if not isinstance(self.raw, str) or not isinstance(self.error, str):
            raise TypeError("raw and error must be str")


@dataclass(frozen=True, slots=True)
class AddRuleSet:
    """Publish a new rule-set version. Versions are immutable: an id that
    exists is refused, and accounts keep the version they opened on."""

    rule_set: RuleSet
    ts: int

    def __post_init__(self) -> None:
        if not isinstance(self.rule_set, RuleSet):
            raise TypeError("rule_set must be a RuleSet")
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class ScheduleNews:
    """Add a restricted event to the calendar (risk desk)."""

    event: RestrictedEvent
    ts: int

    def __post_init__(self) -> None:
        if not isinstance(self.event, RestrictedEvent):
            raise TypeError("event must be a RestrictedEvent")
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class CancelNews:
    """Remove a scheduled event before its window starts."""

    event_id: str
    ts: int

    def __post_init__(self) -> None:
        require_text("event_id", self.event_id)
        _ts(self.ts)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Record a digest of the whole engine state in the chain and store a
    snapshot of it, so start-up can resume from here (sent by the scheduler)."""

    ts: int

    def __post_init__(self) -> None:
        _ts(self.ts)


type Input = (
    PriceUpdate
    | OpenAccount
    | PlaceOrder
    | CancelOrder
    | ClosePosition
    | ModifyStops
    | Advance
    | Outage
    | AddRuleSet
    | ScheduleNews
    | CancelNews
    | Checkpoint
    | Malformed
)

INPUT_TYPES = (
    PriceUpdate,
    OpenAccount,
    PlaceOrder,
    CancelOrder,
    ClosePosition,
    ModifyStops,
    Advance,
    Outage,
    AddRuleSet,
    ScheduleNews,
    CancelNews,
    Checkpoint,
    Malformed,
)


class InputRejectReason(StrEnum):
    MALFORMED = "malformed"
    UNKNOWN_SYMBOL = "unknown_symbol"
    UNKNOWN_RULE_SET = "unknown_rule_set"
    INVALID_SIZE = "invalid_size"
    DUPLICATE_ACCOUNT = "duplicate_account"
    DUPLICATE_RULE_SET = "duplicate_rule_set"
    DUPLICATE_NEWS_EVENT = "duplicate_news_event"
    UNKNOWN_NEWS_EVENT = "unknown_news_event"
    NEWS_STARTED = "news_started"  # a window that started or ended cannot be cancelled
    NO_AFFECTED_SYMBOLS = "no_affected_symbols"


@dataclass(frozen=True, slots=True)
class InputRejected:
    """An input refused before the engine saw it; engine state is unchanged.

    `ts` is engine time (the input did not advance it); `ref` is the account
    or symbol the input named ("" for a malformed message).
    """

    ts: int
    input_kind: str
    ref: str
    reason: InputRejectReason


@dataclass(frozen=True, slots=True)
class RuleSetAdded:
    ts: int  # engine time; the input does not advance it
    rsv_id: str


@dataclass(frozen=True, slots=True)
class StateCheckpointed:
    """The SHA-256 of the canonical engine state after record `ts` was reached;
    the snapshot stored with this record must have this digest."""

    ts: int
    state_digest: str


type JournalEvent = Event | InputRejected | RuleSetAdded | StateCheckpointed


def apply(
    supervisor: RuleSupervisor, rule_sets: MutableMapping[str, RuleSet], item: Input
) -> list[JournalEvent]:
    """Hand one input to the supervisor and return the events it caused.
    `rule_sets` is the live catalogue; AddRuleSet extends it. Checkpoint is
    handled by the journal machine, which owns the state digest."""
    sup = supervisor

    def reject(ref: str, reason: InputRejectReason) -> list[JournalEvent]:
        return [InputRejected(sup.engine.now, type(item).__name__, ref, reason)]

    match item:
        case PriceUpdate():
            if item.symbol not in sup.engine.symbols:
                return reject(item.symbol, InputRejectReason.UNKNOWN_SYMBOL)
            return list(sup.on_price(item))
        case OpenAccount():
            rules = rule_sets.get(item.rsv_id)
            if rules is None:
                return reject(item.account_id, InputRejectReason.UNKNOWN_RULE_SET)
            if item.initial_balance_cents not in rules.sizes_cents:
                return reject(item.account_id, InputRejectReason.INVALID_SIZE)
            if sup.has_account(item.account_id):
                return reject(item.account_id, InputRejectReason.DUPLICATE_ACCOUNT)
            return list(
                sup.open_account(item.account_id, rules, item.initial_balance_cents, item.ts)
            )
        case PlaceOrder():
            return list(sup.place_order(item.request, item.ts))
        case CancelOrder():
            return list(sup.cancel_order(item.account_id, item.order_id, item.ts))
        case ClosePosition():
            return list(sup.close_position(item.account_id, item.position_id, item.ts, item.volume))
        case ModifyStops():
            return list(
                sup.modify_stops(
                    item.account_id, item.position_id, item.stop_loss, item.take_profit, item.ts
                )
            )
        case Advance():
            return list(sup.advance(item.ts))
        case Outage():
            return list(sup.on_outage(item.ts))
        case AddRuleSet():
            rsv_id = item.rule_set.rsv_id
            if rsv_id in rule_sets:
                return reject(rsv_id, InputRejectReason.DUPLICATE_RULE_SET)
            rule_sets[rsv_id] = item.rule_set
            return [RuleSetAdded(sup.engine.now, rsv_id)]
        case ScheduleNews():
            event_id = item.event.event_id
            if sup.engine.knows_news(event_id):
                return reject(event_id, InputRejectReason.DUPLICATE_NEWS_EVENT)
            window = event_window(item.event, sup.engine.symbol_specs.values())
            if window is None:
                return reject(event_id, InputRejectReason.NO_AFFECTED_SYMBOLS)
            return list(sup.schedule_news(event_id, window, item.ts))
        case CancelNews():
            if not sup.engine.knows_news(item.event_id):
                return reject(item.event_id, InputRejectReason.UNKNOWN_NEWS_EVENT)
            scheduled = sup.engine.news_event(item.event_id)
            if scheduled is None or scheduled.start_ts <= max(sup.engine.now, item.ts):
                return reject(item.event_id, InputRejectReason.NEWS_STARTED)
            return list(sup.cancel_news(item.event_id, item.ts))
        case Checkpoint():
            raise TypeError("Checkpoint is applied by the journal machine")
        case Malformed():
            return reject("", InputRejectReason.MALFORMED)
