import pytest
import valkey

from propfirm.market.sequence import SeqStatus, SequenceTracker
from propfirm.market.stream import PriceStreamReader, PriceStreamWriter, StreamMessage
from propfirm.market.synthetic import SyntheticConfig, generate
from propfirm.market.types import PriceUpdate

pytestmark = pytest.mark.integration

T0 = 1_790_000_000_000


def feed(seconds: int = 20) -> list[PriceUpdate]:
    config = SyntheticConfig(seed="stream", symbols=("EUR/USD", "XAU/USD", "BTC/USD"))
    return list(generate(config, T0, T0 + seconds * 1_000))


def drain(reader: PriceStreamReader, *, ack: bool) -> list[StreamMessage]:
    out: list[StreamMessage] = []
    while batch := reader.read(count=50):
        out.extend(batch)
        if ack:
            reader.ack([m.entry_id for m in batch])
    return out


def test_round_trip_preserves_order_and_has_no_gaps(
    valkey_client: valkey.Valkey, stream_key: str
) -> None:
    updates = feed()
    PriceStreamWriter(valkey_client, stream_key).publish_many(updates)
    reader = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    reader.ensure_group()
    received = [m.update for m in drain(reader, ack=True)]
    assert received == updates
    tracker = SequenceTracker()
    assert all(tracker.observe(u.symbol, u.seq).status is SeqStatus.IN_ORDER for u in received)


def test_lost_update_is_detected(valkey_client: valkey.Valkey, stream_key: str) -> None:
    updates = [u for u in feed() if u.symbol == "EUR/USD"]
    lost = updates[5]
    PriceStreamWriter(valkey_client, stream_key).publish_many(u for u in updates if u is not lost)
    reader = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    reader.ensure_group()
    tracker = SequenceTracker()
    gaps = [
        r
        for m in drain(reader, ack=True)
        if (r := tracker.observe(m.update.symbol, m.update.seq)).status is SeqStatus.GAP
    ]
    assert [(g.missing_from, g.missing_to) for g in gaps] == [(lost.seq, lost.seq)]


def test_crash_before_ack_redelivers_pending_first(
    valkey_client: valkey.Valkey, stream_key: str
) -> None:
    updates = feed(5)
    writer = PriceStreamWriter(valkey_client, stream_key)
    writer.publish_many(updates[:10])

    first = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    first.ensure_group()
    taken = first.read(count=10)
    first.ack([m.entry_id for m in taken[:4]])  # crash after acknowledging 4 of 10

    writer.publish_many(updates[10:])
    restarted = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    restarted.ensure_group()  # group exists already: must not fail
    received = [m.update for m in drain(restarted, ack=True)]
    assert received == updates[4:]


def test_long_pending_list_is_paged_without_repeats(
    valkey_client: valkey.Valkey, stream_key: str
) -> None:
    updates = feed(5)[:25]
    PriceStreamWriter(valkey_client, stream_key).publish_many(updates)
    first = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    first.ensure_group()
    first.read(count=25)  # crash with all 25 unacknowledged

    restarted = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    pages = [restarted.read(count=10) for _ in range(4)]
    assert [len(p) for p in pages] == [10, 10, 5, 0]
    assert [m.update for p in pages for m in p] == updates


def test_republished_update_is_flagged_duplicate(
    valkey_client: valkey.Valkey, stream_key: str
) -> None:
    updates = feed(5)[:6]
    writer = PriceStreamWriter(valkey_client, stream_key)
    writer.publish_many(updates)
    writer.publish(updates[2])
    reader = PriceStreamReader(valkey_client, stream_key, "engine", "engine-1")
    reader.ensure_group()
    tracker = SequenceTracker()
    statuses = [tracker.observe(m.update.symbol, m.update.seq) for m in drain(reader, ack=True)]
    assert sum(r.status is SeqStatus.DUPLICATE for r in statuses) == 1
    assert sum(r.apply for r in statuses) == len(updates)


def test_two_groups_each_receive_every_update(
    valkey_client: valkey.Valkey, stream_key: str
) -> None:
    updates = feed(5)
    PriceStreamWriter(valkey_client, stream_key).publish_many(updates)
    for group in ("engine", "recorder"):
        reader = PriceStreamReader(valkey_client, stream_key, group, f"{group}-1")
        reader.ensure_group()
        assert [m.update for m in drain(reader, ack=True)] == updates
