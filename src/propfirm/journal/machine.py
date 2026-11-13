"""Journal records, the hash chain, and the machine that writes and replays them.

One record per input, numbered 1, 2, 3 ... with no gaps:

    seq        position in the journal
    source_id  transport id of the message that carried the input (a Valkey
               stream entry id "ms-n"); strictly increasing, which is how a
               restarted runner recognises messages it has already journaled
    kind       input class name
    ts         engine time after the input, ms UTC
    input      the input, canonical JSON; a price is stored as a PriceRef
               (symbol, seq, key id, digest) and the price itself in the
               separate price store, which licensing requires to be purgeable
    events     every event the input caused, canonical JSON
    entries    every ledger entry the input posted, in order
    prev_hash  hash of the previous record; the first record links to the
               setup fingerprint
    hash       SHA-256 over all of the above

Live processing and replay share one code path (`Machine.apply`): replay
recomputes each record from its input and requires the result to equal the
stored record exactly, so any tampering, reordering, gap, or change in
engine behaviour is detected at the first record it affects.

Price digests are HMAC-SHA256 under a secret price key held in the price
store. Purging deletes the prices together with their keys, after which a
digest reveals nothing about the price it was made from.

A Checkpoint input records the SHA-256 of the canonical engine state in the
chain; the state itself is stored as a snapshot beside the record. Start-up
can resume from the latest snapshot (`Machine.resume`) and replay only the
records after it. Snapshots hold quotes, which come from licensed prices, so
they are purged with the prices.
"""

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import Codec, CodecError, digest
from propfirm.journal.inputs import (
    INPUT_TYPES,
    AddRuleSet,
    Advance,
    CancelNews,
    CancelOrder,
    Checkpoint,
    ClosePosition,
    DebitPayout,
    Input,
    InputRejected,
    InputRejectReason,
    Malformed,
    ModifyStops,
    OpenAccount,
    Outage,
    PlaceOrder,
    PlaceTrailingEntry,
    RuleSetAdded,
    ScheduleNews,
    SetTrailingStop,
    StateCheckpointed,
    UpdateTrading,
    VoidAccount,
    apply,
)
from propfirm.journal.setup import EngineSetup, SetupError, build
from propfirm.ledger import Entry, Ledger, Posting
from propfirm.market.guards import FreezeReason, GuardConfig, GuardState
from propfirm.market.spread import (
    MarketSpreadConfig,
    NewsWindow,
    Quote,
    SpreadConfig,
    SpreadState,
)
from propfirm.market.symbols import AssetClass, SymbolSpec
from propfirm.market.types import PriceUpdate
from propfirm.rules.events import (
    AccountBreached,
    AccountStatus,
    AccountVoided,
    BreachReason,
    DayStarted,
    NewsViolation,
    PayoutDebited,
    StagePassed,
)
from propfirm.rules.news import EventScope, RestrictedEvent
from propfirm.rules.products import MaxLossKind, RuleSet, Stage, TargetBasis, TieRule
from propfirm.rules.supervisor import AccountState, RuleSupervisor, SupervisorState
from propfirm.trading.engine import EngineState
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    CommissionCharged,
    DuplicateUpdate,
    FeedGap,
    NewsCancelled,
    NewsScheduled,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    PriceHeld,
    PriceReleased,
    RejectReason,
    StaleUpdate,
    StopsModified,
    SwapCharged,
    SymbolFrozen,
    SymbolResumed,
    TradingConfigChanged,
    TrailingMoved,
    TrailingPlaced,
)
from propfirm.trading.instruments import InstrumentSpec, SpreadAndCommission
from propfirm.trading.orders import (
    OrderRequest,
    OrderType,
    PendingOrder,
    Position,
    Side,
    TrailingKind,
    TrailingOrder,
)
from propfirm.trading.swaps import SwapTerms

CHAIN_VERSION = "propfirm-journal-v2"  # v2: keyed price digests, checkpoints
PRICE_KEY_BYTES = 32
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SOURCE_ID = re.compile(r"(0|[1-9][0-9]*)-(0|[1-9][0-9]*)")


class JournalIntegrityError(Exception):
    """The journal is not a valid record: broken chain, gap, bad content."""


class ReplayDivergenceError(JournalIntegrityError):
    """Replaying an input gave a different record than the one stored."""


class PricesUnavailableError(Exception):
    """A journaled price (or its key) is no longer in the price store (purged)."""


@dataclass(frozen=True, slots=True)
class PriceRef:
    """The journal's pointer to a price held in the price store."""

    symbol: str
    seq: int
    key_id: int
    digest: str  # HMAC-SHA256 of the canonical PriceUpdate under the price key

    def __post_init__(self) -> None:
        require_text("symbol", self.symbol)
        require_int("seq", self.seq, minimum=1)
        require_int("key_id", self.key_id, minimum=1)
        if not isinstance(self.digest, str) or not _HEX64.fullmatch(self.digest):
            raise ValueError("digest must be 64 lowercase hex digits")


@dataclass(frozen=True, slots=True)
class MachineState:
    """Everything a resumed machine needs besides the setup and the ledger
    entries, which are reloaded from the journal and checked against the
    count and balances here."""

    engine: EngineState
    supervisor: SupervisorState
    rule_sets: tuple[RuleSet, ...]  # the live catalogue, in the order added
    ledger_entries: int
    ledger_balances: tuple[tuple[str, int], ...]  # sorted by ledger account


CODEC = Codec(
    (
        # inputs and the journal's own records
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
        UpdateTrading,
        VoidAccount,
        DebitPayout,
        Checkpoint,
        Malformed,
        PriceRef,
        OrderRequest,
        RestrictedEvent,
        InputRejected,
        InputRejectReason,
        RuleSetAdded,
        StateCheckpointed,
        # engine and rule events
        AccountOpened,
        OrderRejected,
        OrderPlaced,
        OrderCancelled,
        PositionOpened,
        PositionClosed,
        CommissionCharged,
        StopsModified,
        SymbolFrozen,
        SymbolResumed,
        FeedGap,
        DuplicateUpdate,
        StaleUpdate,
        PriceHeld,
        PriceReleased,
        NewsScheduled,
        NewsCancelled,
        TradingConfigChanged,
        DayStarted,
        AccountBreached,
        StagePassed,
        AccountVoided,
        PayoutDebited,
        TrailingPlaced,
        TrailingMoved,
        # ledger
        Entry,
        Posting,
        # setup
        EngineSetup,
        InstrumentSpec,
        SymbolSpec,
        SpreadConfig,
        MarketSpreadConfig,
        SpreadAndCommission,
        GuardConfig,
        NewsWindow,
        RuleSet,
        # state
        MachineState,
        EngineState,
        SupervisorState,
        AccountState,
        GuardState,
        SpreadState,
        Quote,
        PendingOrder,
        Position,
        TrailingOrder,
        # enums
        Side,
        OrderType,
        RejectReason,
        CloseReason,
        FreezeReason,
        BreachReason,
        AccountStatus,
        Stage,
        MaxLossKind,
        TargetBasis,
        TieRule,
        AssetClass,
        EventScope,
        TrailingKind,
        NewsViolation,
        # inputs added later (registration order does not affect encodings)
        PlaceTrailingEntry,
        SetTrailingStop,
        # swaps (13 Nov 2026)
        SwapTerms,
        SwapCharged,
    )
)


def price_ref(update: PriceUpdate, key_id: int, secret: bytes) -> PriceRef:
    mac = hmac.new(secret, CODEC.encode(update).encode("ascii"), hashlib.sha256)
    return PriceRef(update.symbol, update.seq, key_id, mac.hexdigest())


def source_key(source_id: str) -> tuple[int, int]:
    """Order key of a stream entry id "ms-n"."""
    match = _SOURCE_ID.fullmatch(source_id) if isinstance(source_id, str) else None
    if match is None:
        raise ValueError(f"not a stream entry id: {source_id!r}")
    return int(match[1]), int(match[2])


@dataclass(frozen=True, slots=True)
class Record:
    seq: int
    source_id: str
    kind: str
    ts: int
    input: str
    events: str
    entries: tuple[Entry, ...]
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """A record with the price it references (None for non-price inputs, or
    when the price has been purged)."""

    record: Record
    price: PriceUpdate | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Canonical MachineState text after record `seq` (a Checkpoint)."""

    seq: int
    state: str


def record_hash(record: Record) -> str:
    return digest(
        CODEC.encode(
            (
                CHAIN_VERSION,
                record.seq,
                record.source_id,
                record.kind,
                record.ts,
                record.input,
                record.events,
                record.entries,
                record.prev_hash,
            )
        )
    )


def check_link(record: Record, seq: int, head: str) -> None:
    """Raise unless `record` follows record `seq` (hash `head`) and its own
    hash matches its content."""
    if record.seq != seq + 1:
        raise JournalIntegrityError(f"expected record {seq + 1}, found {record.seq}")
    if record.prev_hash != head:
        raise JournalIntegrityError(f"record {record.seq}: chain broken")
    check_hash(record)


def check_hash(record: Record) -> None:
    try:
        expected = record_hash(record)
    except CodecError as exc:
        raise JournalIntegrityError(f"record {record.seq}: {exc}") from exc
    if expected != record.hash:
        raise JournalIntegrityError(f"record {record.seq}: hash does not match its content")


def check_price(stored: StoredRecord, keys: Mapping[int, bytes]) -> None:
    """Raise unless a stored price matches the digest its record holds."""
    price, r = stored.price, stored.record
    if price is None:
        return
    try:
        ref = CODEC.decode(r.input)
    except CodecError as exc:
        raise JournalIntegrityError(f"record {r.seq}: {exc}") from exc
    if not isinstance(ref, PriceRef):
        raise JournalIntegrityError(f"record {r.seq}: a price is stored for a non-price input")
    secret = keys.get(ref.key_id)
    if secret is None:
        raise JournalIntegrityError(f"record {r.seq}: price stored without its key")
    if price_ref(price, ref.key_id, secret) != ref:
        raise JournalIntegrityError(f"record {r.seq}: price store differs from the journal")


def decode_setup(text: str) -> EngineSetup:
    try:
        setup = CODEC.decode(text)
    except CodecError as exc:
        raise SetupError(f"stored setup does not decode: {exc}") from exc
    if not isinstance(setup, EngineSetup):
        raise SetupError("stored setup is not an EngineSetup")
    return setup


def checkpoint_digest(record: Record) -> str:
    """The state digest a Checkpoint record carries."""
    try:
        events = CODEC.decode(record.events)
    except CodecError as exc:
        raise JournalIntegrityError(f"record {record.seq}: {exc}") from exc
    match events:
        case (StateCheckpointed(state_digest=found),) if record.kind == "Checkpoint":
            return found
    raise JournalIntegrityError(f"record {record.seq} is not a checkpoint")


def _as_input(value: object) -> Input | None:
    if isinstance(value, PriceUpdate) or not isinstance(value, INPUT_TYPES):
        return None  # prices are journaled by reference only
    return value


class Machine:
    """The engine plus the head of its journal."""

    def __init__(self, setup_text: str, price_keys: Mapping[int, bytes] | None = None) -> None:
        self._setup = decode_setup(setup_text)
        self._supervisor, self._ledger = build(self._setup)
        self._rule_sets = self._setup.rule_set_map()
        self._fingerprint = digest(setup_text)
        self._seq = 0
        self._head = self._fingerprint
        self._source_id: str | None = None
        self._keys: dict[int, bytes] = {}
        self._key_id: int | None = None
        self._snapshot: Snapshot | None = None
        for key_id, secret in (price_keys or {}).items():
            self.add_price_key(key_id, secret)

    @classmethod
    def resume(
        cls,
        setup_text: str,
        price_keys: Mapping[int, bytes],
        snapshot: Snapshot,
        record: Record,
        entries: Sequence[Entry],
    ) -> Machine:
        """A machine at checkpoint `record`, from its snapshot and the ledger
        entries up to it. Raise unless the snapshot matches the digest the
        record holds and the entries match the snapshot's ledger summary."""
        if snapshot.seq != record.seq:
            raise JournalIntegrityError("snapshot and record differ in seq")
        check_hash(record)
        if digest(snapshot.state) != checkpoint_digest(record):
            raise JournalIntegrityError(f"snapshot {snapshot.seq} does not match its record")
        try:
            state = CODEC.decode(snapshot.state)
        except CodecError as exc:
            raise JournalIntegrityError(f"snapshot {snapshot.seq}: {exc}") from exc
        if not isinstance(state, MachineState):
            raise JournalIntegrityError(f"snapshot {snapshot.seq} is not a MachineState")
        machine = cls(setup_text, price_keys)
        machine._supervisor.engine.restore(state.engine)
        machine._supervisor.restore(state.supervisor)
        machine._rule_sets = {r.rsv_id: r for r in state.rule_sets}
        for entry in entries:
            machine._ledger.post(entry)
        ledger = machine._ledger
        if (len(ledger), tuple(sorted(ledger.balances().items()))) != (
            state.ledger_entries,
            state.ledger_balances,
        ):
            raise JournalIntegrityError(f"ledger differs from snapshot {snapshot.seq}")
        machine._seq, machine._head, machine._source_id = record.seq, record.hash, record.source_id
        return machine

    @property
    def supervisor(self) -> RuleSupervisor:
        return self._supervisor

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def setup(self) -> EngineSetup:
        return self._setup

    @property
    def rule_sets(self) -> dict[str, RuleSet]:
        return dict(self._rule_sets)

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the setup text: identifies the journal."""
        return self._fingerprint

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def head(self) -> str:
        """Hash of the last record (the setup fingerprint before the first)."""
        return self._head

    @property
    def source_id(self) -> str | None:
        return self._source_id

    @property
    def price_key_id(self) -> int | None:
        return self._key_id

    def add_price_key(self, key_id: int, secret: bytes) -> None:
        require_int("key_id", key_id, minimum=1)
        if not isinstance(secret, bytes) or len(secret) != PRICE_KEY_BYTES:
            raise ValueError(f"a price key is {PRICE_KEY_BYTES} bytes")
        if self._keys.setdefault(key_id, secret) != secret:
            raise ValueError(f"price key {key_id} already holds another secret")

    def use_price_key(self, key_id: int) -> None:
        """Digest new prices under this key from now on."""
        if key_id not in self._keys:
            raise KeyError(f"unknown price key {key_id}")
        self._key_id = key_id

    def state(self) -> MachineState:
        return MachineState(
            engine=self._supervisor.engine.state(),
            supervisor=self._supervisor.state(),
            rule_sets=tuple(self._rule_sets.values()),
            ledger_entries=len(self._ledger),
            ledger_balances=tuple(sorted(self._ledger.balances().items())),
        )

    def take_snapshot(self) -> Snapshot | None:
        """The snapshot made by the last Checkpoint applied, once."""
        snapshot, self._snapshot = self._snapshot, None
        return snapshot

    def is_journaled(self, source_id: str) -> bool:
        """Whether a message with this id is at or before the journal head."""
        last = self._source_id
        return last is not None and source_key(source_id) <= source_key(last)

    def apply(self, source_id: str, item: Input) -> StoredRecord:
        """Apply one input and return its record (the caller persists it)."""
        if self.is_journaled(source_id):
            raise ValueError(f"source id {source_id} is not after {self._source_id}")
        price = item if isinstance(item, PriceUpdate) else None
        stored_input: object = item
        if price is not None:
            if self._key_id is None:
                raise RuntimeError("no price key in use")
            stored_input = price_ref(price, self._key_id, self._keys[self._key_id])
        before = len(self._ledger)
        snapshot_text: str | None = None
        if isinstance(item, Checkpoint):
            snapshot_text = CODEC.encode(self.state())
            events: list[object] = [
                StateCheckpointed(self._supervisor.engine.now, digest(snapshot_text))
            ]
        else:
            events = list(apply(self._supervisor, self._rule_sets, item))
        record = Record(
            seq=self._seq + 1,
            source_id=source_id,
            kind=type(item).__name__,
            ts=self._supervisor.engine.now,
            input=CODEC.encode(stored_input),
            events=CODEC.encode(tuple(events)),
            entries=self._ledger.entries_since(before),
            prev_hash=self._head,
            hash="",
        )
        sealed = replace(record, hash=record_hash(record))
        self._seq, self._head, self._source_id = sealed.seq, sealed.hash, source_id
        if snapshot_text is not None:
            self._snapshot = Snapshot(sealed.seq, snapshot_text)
        return StoredRecord(sealed, price)

    def replay(self, stored: StoredRecord) -> None:
        """Verify one stored record against the chain and re-apply its input;
        raise unless recomputation gives the identical record."""
        r = stored.record
        check_link(r, self._seq, self._head)
        try:
            if self.is_journaled(r.source_id):
                raise JournalIntegrityError(f"record {r.seq}: source id not increasing")
            decoded = CODEC.decode(r.input)
        except (CodecError, ValueError) as exc:
            raise JournalIntegrityError(f"record {r.seq}: {exc}") from exc
        item = self._input_of(r.seq, decoded, stored.price)
        again = self.apply(r.source_id, item).record
        self._snapshot = None
        if again != r:
            changed = [
                f.name for f in fields(Record) if getattr(again, f.name) != getattr(r, f.name)
            ]
            raise ReplayDivergenceError(f"record {r.seq}: replay differs in {', '.join(changed)}")

    def _input_of(self, seq: int, decoded: object, price: PriceUpdate | None) -> Input:
        if isinstance(decoded, PriceRef):
            secret = self._keys.get(decoded.key_id)
            if price is None:
                raise PricesUnavailableError(f"record {seq}: price {decoded.symbol}#{decoded.seq}")
            if secret is None:
                raise JournalIntegrityError(f"record {seq}: price stored without its key")
            if price_ref(price, decoded.key_id, secret) != decoded:
                raise JournalIntegrityError(f"record {seq}: price store differs from the journal")
            self._key_id = decoded.key_id  # recompute under the key used live
            return price
        item = _as_input(decoded)
        if item is None:
            raise JournalIntegrityError(f"record {seq}: not an input")
        if price is not None:
            raise JournalIntegrityError(f"record {seq}: a price is stored for a non-price input")
        return item
