from hypothesis import given
from hypothesis import strategies as st

from propfirm.market.sequence import SeqStatus, SequenceTracker


def test_in_order_gap_and_duplicate() -> None:
    t = SequenceTracker()
    assert t.observe("EUR/USD", 1).status is SeqStatus.IN_ORDER
    assert t.observe("EUR/USD", 2).status is SeqStatus.IN_ORDER
    gap = t.observe("EUR/USD", 5)
    assert gap.status is SeqStatus.GAP
    assert (gap.missing_from, gap.missing_to) == (3, 4)
    assert gap.apply
    dup = t.observe("EUR/USD", 4)
    assert dup.status is SeqStatus.DUPLICATE and not dup.apply
    assert t.last_seen("EUR/USD") == 5


def test_symbols_are_independent() -> None:
    t = SequenceTracker()
    t.observe("EUR/USD", 1)
    assert t.observe("XAU/USD", 1).status is SeqStatus.IN_ORDER


def test_first_update_after_fresh_start_above_one_is_a_gap() -> None:
    result = SequenceTracker().observe("EUR/USD", 3)
    assert (result.status, result.missing_from, result.missing_to) == (SeqStatus.GAP, 1, 2)


def test_restored_checkpoint_continues_without_gap() -> None:
    t = SequenceTracker()
    for seq in (1, 2, 3):
        t.observe("EUR/USD", seq)
    restored = SequenceTracker(t.snapshot())
    assert restored.observe("EUR/USD", 4).status is SeqStatus.IN_ORDER
    assert restored.observe("EUR/USD", 3).status is SeqStatus.DUPLICATE


@given(st.lists(st.integers(min_value=1, max_value=60), max_size=80))
def test_reported_missing_ranges_cover_exactly_the_unseen_numbers(seqs: list[int]) -> None:
    t = SequenceTracker()
    applied: list[int] = []
    missing: set[int] = set()
    for seq in seqs:
        r = t.observe("S", seq)
        if r.apply:
            applied.append(seq)
        if r.status is SeqStatus.GAP:
            assert r.missing_from is not None and r.missing_to is not None
            missing |= set(range(r.missing_from, r.missing_to + 1))
    assert applied == sorted(set(applied)) == sorted(applied)  # strictly increasing
    top = t.last_seen("S")
    assert missing == set(range(1, top + 1)) - set(applied)
