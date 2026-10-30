"""Double-entry, append-only ledger in integer US cents.

The ledger is the record of every balance; the per-account balances held here
are a cache that `verify()` proves equal to a rebuild from the journal
(acceptance test 2).

Sign convention: a posting amount is positive for a debit and negative for a
credit, and every entry's amounts sum to zero. A trader account's balance is
kept debit-positive: funding posts +initial balance to the trader account and
the same amount, negated, to a firm account.

Limits: every amount and every resulting balance must fit a signed 64-bit
integer (PostgreSQL BIGINT), checked before anything is changed.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from propfirm._checks import require_int, require_text

MAX_ABS_CENTS = 2**63 - 1


class LedgerError(Exception):
    """Base class for ledger failures. A failed post changes nothing."""


class UnbalancedEntryError(LedgerError, ValueError):
    pass


class DuplicateEntryError(LedgerError):
    """Same entry id, different content."""


class OutOfOrderEntryError(LedgerError):
    """Entry time earlier than the last entry in the journal."""


class CentsOverflowError(LedgerError):
    """An amount or resulting balance would not fit a signed 64-bit integer."""


class LedgerIntegrityError(LedgerError):
    """The cache disagrees with the journal, or the journal is unbalanced."""


@dataclass(frozen=True, slots=True)
class Posting:
    account: str
    amount: int  # cents; debit positive, credit negative

    def __post_init__(self) -> None:
        require_text("account", self.account)
        require_int("amount", self.amount)
        if self.amount == 0:
            raise ValueError("posting amount must not be zero")
        if abs(self.amount) > MAX_ABS_CENTS:
            raise CentsOverflowError(f"amount {self.amount} exceeds 64-bit range")


@dataclass(frozen=True, slots=True)
class Entry:
    """One balanced journal entry.

    Attributes:
        entry_id: Unique id; posting the identical entry again is a no-op.
        occurred_at: ms since epoch UTC; never earlier than the previous entry.
        kind: What the entry records, e.g. "account_funding", "trade_pnl".
        postings: At least two non-zero postings summing to zero.
        reference: Id of the source record (order, fill, payout ...), if any.
    """

    entry_id: str
    occurred_at: int
    kind: str
    postings: tuple[Posting, ...]
    reference: str = ""

    def __post_init__(self) -> None:
        require_text("entry_id", self.entry_id)
        require_int("occurred_at", self.occurred_at, minimum=0)
        require_text("kind", self.kind)
        if not isinstance(self.postings, tuple):
            raise TypeError("postings must be a tuple")
        if len(self.postings) < 2:
            raise UnbalancedEntryError("an entry needs at least two postings")
        for posting in self.postings:
            if not isinstance(posting, Posting):
                raise TypeError("postings must contain Posting objects")
        total = sum(p.amount for p in self.postings)
        if total != 0:
            raise UnbalancedEntryError(f"entry {self.entry_id} sums to {total}, not 0")
        if self.reference and self.reference != self.reference.strip():
            raise ValueError("reference must be unpadded")


class Ledger:
    """In-memory ledger. The event journal persists every entry with the
    input that caused it (`propfirm.journal`)."""

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self._entries: list[Entry] = []
        self._by_id: dict[str, Entry] = {}
        self._balances: dict[str, int] = {}
        for entry in entries:
            self.post(entry)

    def post(self, entry: Entry) -> bool:
        """Append `entry`. Return True if appended, False if the identical
        entry was already posted. Raise, changing nothing, otherwise."""
        if not isinstance(entry, Entry):
            raise TypeError("entry must be an Entry")
        existing = self._by_id.get(entry.entry_id)
        if existing is not None:
            if existing == entry:
                return False
            raise DuplicateEntryError(f"entry id {entry.entry_id} reused with other content")
        if self._entries and entry.occurred_at < self._entries[-1].occurred_at:
            raise OutOfOrderEntryError(
                f"entry {entry.entry_id} at {entry.occurred_at} is before "
                f"{self._entries[-1].occurred_at}"
            )
        updated: dict[str, int] = {}
        for posting in entry.postings:
            current = updated.get(posting.account, self._balances.get(posting.account, 0))
            new = current + posting.amount
            if abs(new) > MAX_ABS_CENTS:
                raise CentsOverflowError(f"balance of {posting.account} would exceed 64-bit range")
            updated[posting.account] = new
        self._entries.append(entry)
        self._by_id[entry.entry_id] = entry
        self._balances.update(updated)
        return True

    def balance(self, account: str) -> int:
        return self._balances.get(account, 0)

    def has(self, entry_id: str) -> bool:
        """Whether an entry with this id was posted."""
        return entry_id in self._by_id

    def balances(self) -> dict[str, int]:
        """A copy of the cached balances (accounts that were ever posted to)."""
        return dict(self._balances)

    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def entries_since(self, index: int) -> tuple[Entry, ...]:
        """Entries from position `index` on (0 is the first entry)."""
        require_int("index", index, minimum=0)
        return tuple(self._entries[index:])

    def __len__(self) -> int:
        return len(self._entries)

    def verify(self) -> None:
        """Raise LedgerIntegrityError unless every entry balances, all balances
        sum to zero, and the cache equals a rebuild from the journal."""
        for entry in self._entries:
            if sum(p.amount for p in entry.postings) != 0:
                raise LedgerIntegrityError(f"entry {entry.entry_id} is unbalanced")
        rebuilt = rebuild_balances(self._entries)
        if rebuilt != self._balances:
            raise LedgerIntegrityError("cached balances differ from the journal")
        if sum(rebuilt.values()) != 0:
            raise LedgerIntegrityError("balances do not sum to zero")


def rebuild_balances(entries: Iterable[Entry]) -> dict[str, int]:
    """Balances recomputed from scratch from a journal."""
    balances: dict[str, int] = {}
    for entry in entries:
        for posting in entry.postings:
            balances[posting.account] = balances.get(posting.account, 0) + posting.amount
    return balances


def transfer(
    entry_id: str,
    occurred_at: int,
    kind: str,
    *,
    debit: str,
    credit: str,
    amount: int,
    reference: str = "",
) -> Entry:
    """A two-posting entry moving `amount` cents: +amount to `debit`,
    -amount to `credit`. `amount` must be positive."""
    require_int("amount", amount, minimum=1)
    return Entry(
        entry_id=entry_id,
        occurred_at=occurred_at,
        kind=kind,
        postings=(Posting(debit, amount), Posting(credit, -amount)),
        reference=reference,
    )
