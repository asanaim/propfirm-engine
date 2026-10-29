"""Ledger tests, including acceptance test 2: every transaction sums to zero
and recomputed balances equal cached ones."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.ledger import (
    MAX_ABS_CENTS,
    CentsOverflowError,
    DuplicateEntryError,
    Entry,
    Ledger,
    LedgerIntegrityError,
    OutOfOrderEntryError,
    Posting,
    UnbalancedEntryError,
    rebuild_balances,
    transfer,
)

ACCOUNTS = ["acc:1:balance", "acc:2:balance", "firm:capital", "firm:pnl", "firm:payouts", "x"]
T0 = 1_790_000_000_000


def snapshot(ledger: Ledger) -> tuple[tuple[Entry, ...], dict[str, int]]:
    return ledger.entries(), ledger.balances()


@st.composite
def entries(draw: st.DrawFn, max_entries: int = 30) -> list[Entry]:
    count = draw(st.integers(min_value=0, max_value=max_entries))
    out: list[Entry] = []
    t = T0
    for i in range(count):
        t += draw(st.integers(min_value=0, max_value=5_000))
        amounts = draw(
            st.lists(
                st.integers(min_value=-(10**12), max_value=10**12).filter(bool),
                min_size=1,
                max_size=5,
            )
        )
        balancing = -sum(amounts)
        if balancing:
            amounts.append(balancing)
        if len(amounts) < 2:
            continue
        accounts = draw(
            st.lists(st.sampled_from(ACCOUNTS), min_size=len(amounts), max_size=len(amounts))
        )
        postings = tuple(Posting(a, x) for a, x in zip(accounts, amounts, strict=True))
        out.append(Entry(f"e{i}", t, "test", postings))
    return out


# --- acceptance test 2 -------------------------------------------------------


@settings(max_examples=300)
@given(entries())
def test_every_entry_balances_and_cache_equals_rebuild(journal: list[Entry]) -> None:
    ledger = Ledger()
    for entry in journal:
        assert ledger.post(entry)
    ledger.verify()
    assert all(sum(p.amount for p in e.postings) == 0 for e in ledger.entries())
    assert ledger.balances() == rebuild_balances(journal)
    assert sum(ledger.balances().values()) == 0
    for account in ACCOUNTS:
        expected = sum(p.amount for e in journal for p in e.postings if p.account == account)
        assert ledger.balance(account) == expected


@given(entries())
def test_reposting_identical_entries_changes_nothing(journal: list[Entry]) -> None:
    ledger = Ledger(journal)
    before = snapshot(ledger)
    assert not any(ledger.post(entry) for entry in journal)
    assert snapshot(ledger) == before


@given(entries())
def test_replaying_the_journal_rebuilds_the_same_ledger(journal: list[Entry]) -> None:
    ledger = Ledger(journal)
    assert snapshot(Ledger(ledger.entries())) == snapshot(ledger)


@given(entries(), st.integers(min_value=1, max_value=10**6))
def test_rejected_post_changes_nothing(journal: list[Entry], extra: int) -> None:
    ledger = Ledger(journal)
    before = snapshot(ledger)
    last_t = journal[-1].occurred_at if journal else T0
    if journal:
        with pytest.raises(DuplicateEntryError):
            ledger.post(
                transfer(journal[0].entry_id, last_t, "x", debit="a", credit="b", amount=extra)
            )
        if last_t > 0:
            with pytest.raises(OutOfOrderEntryError):
                ledger.post(transfer("late", last_t - 1, "x", debit="a", credit="b", amount=extra))
    assert snapshot(ledger) == before


# --- entry validation ----------------------------------------------------------


def test_unbalanced_entry_rejected() -> None:
    with pytest.raises(UnbalancedEntryError):
        Entry("e", T0, "k", (Posting("a", 100), Posting("b", -99)))


def test_single_posting_rejected() -> None:
    with pytest.raises(UnbalancedEntryError):
        Entry("e", T0, "k", (Posting("a", 100),))


@pytest.mark.parametrize("amount", [1.0, 0.5, True, Decimal(1), "1", 0])
def test_non_integer_or_zero_amount_rejected(amount: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Posting("a", amount)  # type: ignore[arg-type]


def test_postings_must_be_a_tuple() -> None:
    with pytest.raises(TypeError):
        Entry("e", T0, "k", [Posting("a", 1), Posting("b", -1)])  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["entry_id", "kind"])
def test_blank_identifiers_rejected(field: str) -> None:
    kwargs = {"entry_id": "e", "kind": "k", field: " "}
    with pytest.raises(ValueError):
        Entry(occurred_at=T0, postings=(Posting("a", 1), Posting("b", -1)), **kwargs)


def test_transfer_requires_positive_amount() -> None:
    with pytest.raises(ValueError):
        transfer("e", T0, "k", debit="a", credit="b", amount=0)
    entry = transfer(
        "e", T0, "funding", debit="acc:1:balance", credit="firm:capital", amount=10_000_000
    )
    assert [(p.account, p.amount) for p in entry.postings] == [
        ("acc:1:balance", 10_000_000),
        ("firm:capital", -10_000_000),
    ]


# --- 64-bit limits ---------------------------------------------------------------


def test_amount_beyond_64_bits_rejected() -> None:
    with pytest.raises(CentsOverflowError):
        Posting("a", MAX_ABS_CENTS + 1)


def test_balance_overflow_rejected_without_change() -> None:
    ledger = Ledger([transfer("e1", T0, "k", debit="a", credit="b", amount=MAX_ABS_CENTS)])
    before = snapshot(ledger)
    with pytest.raises(CentsOverflowError):
        ledger.post(transfer("e2", T0, "k", debit="a", credit="c", amount=1))
    assert snapshot(ledger) == before
    ledger.verify()


def test_same_account_twice_in_one_entry_checked_cumulatively() -> None:
    ledger = Ledger([transfer("e1", T0, "k", debit="a", credit="b", amount=MAX_ABS_CENTS - 1)])
    entry = Entry("e2", T0, "k", (Posting("a", 1), Posting("a", 1), Posting("c", -2)))
    with pytest.raises(CentsOverflowError):
        ledger.post(entry)
    assert ledger.balance("a") == MAX_ABS_CENTS - 1


# --- verify detects corruption -------------------------------------------------------


def test_verify_detects_a_tampered_cache() -> None:
    ledger = Ledger([transfer("e1", T0, "k", debit="a", credit="b", amount=500)])
    ledger._balances["a"] += 1  # simulate corruption of the cache
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_entries_cannot_be_mutated_through_the_api() -> None:
    ledger = Ledger([transfer("e1", T0, "k", debit="a", credit="b", amount=500)])
    view = ledger.entries()
    assert isinstance(view, tuple)
    balances = ledger.balances()
    balances["a"] = 0
    assert ledger.balance("a") == 500
