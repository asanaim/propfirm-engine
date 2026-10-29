"""Journal records, the hash chain, and the machine that writes and replays them.

One record per input, numbered 1, 2, 3 ... with no gaps:

    seq        position in the journal
    source_id  transport id of the message that carried the input (a Valkey
               stream entry id "ms-n"); strictly increasing, which is how a
               restarted runner recognises messages it has already journaled
    kind       input class name
    ts         engine time after the input, ms UTC
    input      the input, canonical JSON; a price is stored as a PriceRef
               (symbol, seq, digest) and the price itself in the separate
               price store, which licensing requires to be purgeable
    events     every event the input caused, canonical JSON
    entries    every ledger entry the input posted, in order
    prev_hash  hash of the previous record; the first record links to the
               setup fingerprint
    hash       SHA-256 over all of the above

Live processing and replay share one code path (`Machine.apply`): replay
recomputes each record from its input and requires the result to equal the
stored record exactly, so any tampering, reordering, gap, or change in
engine behaviour is detected at the first record it affects.

Limit [DEFAULT, decide in P2]: a price digest is an unkeyed SHA-256 of the
price. For licensed prices it may count as derived data that must be purged
too; a keyed digest whose key is destroyed at purge would remove that doubt.
"""

import re
from dataclasses import dataclass, fields, replace

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import Codec, CodecError, digest
from propfirm.journal.inputs import (
    INPUT_TYPES,
    Advance,
    CancelOrder,
    ClosePosition,
    Input,
    InputRejected,
    InputRejectReason,
    Malformed,
    ModifyStops,
    OpenAccount,
    Outage,
    PlaceOrder,
    apply,
)
from propfirm.journal.setup import EngineSetup, SetupError, build
from propfirm.ledger import Entry, Ledger, Posting
from propfirm.market.guards import FreezeReason, GuardConfig
from propfirm.market.spread import NewsWindow, SpreadConfig
from propfirm.market.symbols import AssetClass, SymbolSpec
from propfirm.market.types import PriceUpdate
from propfirm.rules.events import AccountBreached, BreachReason, DayStarted, StagePassed
from propfirm.rules.products import MaxLossKind, RuleSet, Stage, TargetBasis, TieRule
from propfirm.rules.supervisor import RuleSupervisor
from propfirm.trading.events import (
    AccountOpened,
    CloseReason,
    DuplicateUpdate,
    FeedGap,
    OrderCancelled,
    OrderPlaced,
    OrderRejected,
    PositionClosed,
    PositionOpened,
    RejectReason,
    StaleUpdate,
    StopsModified,
    SymbolFrozen,
    SymbolResumed,
)
from propfirm.trading.instruments import InstrumentSpec
from propfirm.trading.orders import OrderRequest, OrderType, Side

CHAIN_VERSION = "propfirm-journal-v1"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SOURCE_ID = re.compile(r"(0|[1-9][0-9]*)-(0|[1-9][0-9]*)")


class JournalIntegrityError(Exception):
    """The journal is not a valid record: broken chain, gap, bad content."""


class ReplayDivergenceError(JournalIntegrityError):
    """Replaying an input gave a different record than the one stored."""


class PricesUnavailableError(Exception):
    """A journaled price is no longer in the price store (purged)."""


@dataclass(frozen=True, slots=True)
class PriceRef:
    """The journal's pointer to a price held in the price store."""

    symbol: str
    seq: int
    digest: str  # SHA-256 of the canonical PriceUpdate

    def __post_init__(self) -> None:
        require_text("symbol", self.symbol)
        require_int("seq", self.seq, minimum=1)
        if not isinstance(self.digest, str) or not _HEX64.fullmatch(self.digest):
            raise ValueError("digest must be 64 lowercase hex digits")


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
        Malformed,
        PriceRef,
        OrderRequest,
        InputRejected,
        InputRejectReason,
        # engine and rule events
        AccountOpened,
        OrderRejected,
        OrderPlaced,
        OrderCancelled,
        PositionOpened,
        PositionClosed,
        StopsModified,
        SymbolFrozen,
        SymbolResumed,
        FeedGap,
        DuplicateUpdate,
        StaleUpdate,
        DayStarted,
        AccountBreached,
        StagePassed,
        # ledger
        Entry,
        Posting,
        # setup
        EngineSetup,
        InstrumentSpec,
        SymbolSpec,
        SpreadConfig,
        GuardConfig,
        NewsWindow,
        RuleSet,
        # enums
        Side,
        OrderType,
        RejectReason,
        CloseReason,
        FreezeReason,
        BreachReason,
        Stage,
        MaxLossKind,
        TargetBasis,
        TieRule,
        AssetClass,
    )
)


def price_ref(update: PriceUpdate) -> PriceRef:
    return PriceRef(update.symbol, update.seq, digest(CODEC.encode(update)))


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
    try:
        expected = record_hash(record)
    except CodecError as exc:
        raise JournalIntegrityError(f"record {record.seq}: {exc}") from exc
    if expected != record.hash:
        raise JournalIntegrityError(f"record {record.seq}: hash does not match its content")


def check_price(stored: StoredRecord) -> None:
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
    if price_ref(price) != ref:
        raise JournalIntegrityError(f"record {r.seq}: price store differs from the journal")


def decode_setup(text: str) -> EngineSetup:
    try:
        setup = CODEC.decode(text)
    except CodecError as exc:
        raise SetupError(f"stored setup does not decode: {exc}") from exc
    if not isinstance(setup, EngineSetup):
        raise SetupError("stored setup is not an EngineSetup")
    return setup


def _as_input(value: object) -> Input | None:
    if isinstance(value, PriceUpdate) or not isinstance(value, INPUT_TYPES):
        return None  # prices are journaled by reference only
    return value


class Machine:
    """The engine plus the head of its journal."""

    def __init__(self, setup_text: str) -> None:
        self._setup = decode_setup(setup_text)
        self._supervisor, self._ledger = build(self._setup)
        self._rule_sets = self._setup.rule_set_map()
        self._seq = 0
        self._head = digest(setup_text)
        self._source_id: str | None = None

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
    def seq(self) -> int:
        return self._seq

    @property
    def head(self) -> str:
        """Hash of the last record (the setup fingerprint before the first)."""
        return self._head

    @property
    def source_id(self) -> str | None:
        return self._source_id

    def is_journaled(self, source_id: str) -> bool:
        """Whether a message with this id is at or before the journal head."""
        last = self._source_id
        return last is not None and source_key(source_id) <= source_key(last)

    def apply(self, source_id: str, item: Input) -> StoredRecord:
        """Apply one input and return its record (the caller persists it)."""
        if self.is_journaled(source_id):
            raise ValueError(f"source id {source_id} is not after {self._source_id}")
        before = len(self._ledger)
        events = apply(self._supervisor, self._rule_sets, item)
        price = item if isinstance(item, PriceUpdate) else None
        stored_input = price_ref(price) if price is not None else item
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
        sealed = _seal(record)
        self._seq, self._head, self._source_id = sealed.seq, sealed.hash, source_id
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
        if again != r:
            changed = [
                f.name for f in fields(Record) if getattr(again, f.name) != getattr(r, f.name)
            ]
            raise ReplayDivergenceError(f"record {r.seq}: replay differs in {', '.join(changed)}")

    @staticmethod
    def _input_of(seq: int, decoded: object, price: PriceUpdate | None) -> Input:
        if isinstance(decoded, PriceRef):
            if price is None:
                raise PricesUnavailableError(f"record {seq}: price {decoded.symbol}#{decoded.seq}")
            if price_ref(price) != decoded:
                raise JournalIntegrityError(f"record {seq}: price store differs from the journal")
            return price
        item = _as_input(decoded)
        if item is None:
            raise JournalIntegrityError(f"record {seq}: not an input")
        if price is not None:
            raise JournalIntegrityError(f"record {seq}: a price is stored for a non-price input")
        return item


def _seal(record: Record) -> Record:
    return replace(record, hash=record_hash(record))
