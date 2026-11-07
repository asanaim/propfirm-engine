"""The audit trail without a database: the chain, what is recorded, the
SIEM formats, and the auditor catching tampering [P2 step 7b]."""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from propfirm.audit.auditor import Auditor
from propfirm.audit.chain import CHAIN_ID, GENESIS, AuditEvent, AuditRecord, check, link
from propfirm.audit.http import audited, client_ip, target
from propfirm.audit.sinks import (
    FileSink,
    Sink,
    SinkError,
    StdoutSink,
    SyslogSink,
    ecs,
    sinks_from,
    syslog_frame,
)
from propfirm.audit.store import AuditStore, Status
from propfirm.journal.anchor import Anchor, FileAnchorStore

T0 = 1_794_000_000_000


def event(i: int, actor: str = "kc-admin", status: int = 200) -> AuditEvent:
    return AuditEvent(T0 + i, actor, "POST /staff/grants/{grt}/approve", f"grt=GRT{i:08d}",
                      status, "203.0.113.7", f"req{i}")  # fmt: skip


def chain(n: int) -> list[AuditRecord]:
    out, prev = [], GENESIS
    for seq in range(1, n + 1):
        e = event(seq)
        h = link(prev, seq, e)
        out.append(AuditRecord(seq, e, prev, h))
        prev = h
    return out


# --- the chain ------------------------------------------------------------------------


def test_link_is_deterministic_and_covers_every_field() -> None:
    e = event(1)
    assert link(GENESIS, 1, e) == link(GENESIS, 1, e)
    assert link(GENESIS, 1, e) != link(GENESIS, 2, e)
    for changed in (
        replace(e, actor="kc-other"),
        replace(e, status=403),
        replace(e, target=""),
        replace(e, ip=""),
        replace(e, request_id="other"),
    ):
        assert link(GENESIS, 1, changed) != link(GENESIS, 1, e)


def test_check_finds_the_first_defect() -> None:
    records = chain(5)
    assert check(records, 0, GENESIS) is None
    assert check(records[2:], 2, records[1].hash) is None
    edited = list(records)
    edited[2] = replace(records[2], event=replace(records[2].event, actor="kc-someone"))
    found = check(edited, 0, GENESIS)
    assert found is not None and found.seq == 3 and "content" in found.reason
    gap = records[:2] + records[3:]
    found = check(gap, 0, GENESIS)
    assert found is not None and found.seq == 3 and "missing" in found.reason
    # a record rehashed after an edit still breaks the next link
    forged_event = replace(records[1].event, actor="kc-someone")
    forged = replace(records[1], event=forged_event, hash=link(GENESIS, 2, forged_event))
    found = check([records[0], forged, *records[2:]], 0, GENESIS)
    assert found is not None and found.seq in (2, 3)


def test_events_reject_bad_values() -> None:
    with pytest.raises(ValueError, match="action"):
        replace(event(1), action="approve everything")
    with pytest.raises(ValueError, match="actor"):
        replace(event(1), actor="")
    with pytest.raises(ValueError, match="target"):
        replace(event(1), target="x\n")
    with pytest.raises(ValueError, match="status"):
        replace(event(1), status=600)
    assert AuditEvent(T0, "auditor", "audit.chain_broken", "seq=3", 0, "", "").status == 0


# --- what the API records -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "route", "status", "expected"),
    [
        ("GET", "/staff/members", 200, True),  # staff reads of personal data
        ("GET", "/staff", 200, True),
        ("POST", "/admin/settings/proposals/{pid}/approve", 200, True),
        ("GET", "/accounts/{account_id}", 403, True),  # every refusal
        ("GET", "/me", 401, True),
        ("POST", "/me/wallet", 200, True),  # a trader's writes
        ("POST", "/orders", 201, True),
        ("POST", "/accounts/{account_id}/payouts", 201, True),
        ("POST", "/webhooks/didit", 200, True),
        ("POST", "/accounts/{account_id}/orders", 200, False),  # journaled
        ("DELETE", "/accounts/{account_id}/orders/{order_id}", 200, False),
        ("PUT", "/accounts/{account_id}/positions/{position_id}/stops", 200, False),
        ("POST", "/accounts/{account_id}/trailing-orders", 200, False),
        ("GET", "/me", 200, False),  # traders' and public reads
        ("GET", "/catalogue", 200, False),
        ("GET", "/health", 401, False),
        ("GET", "/staffing", 200, False),  # a prefix is a path segment
    ],
)
def test_what_is_audited(method: str, route: str, status: int, expected: bool) -> None:
    assert audited(method, route, status) is expected


def test_target_and_client_ip_are_cleaned() -> None:
    assert target({"sid": "STF00000001", "a": "b"}) == "a=b&sid=STF00000001"
    assert target({"x": "evil\n‮"}) == "x=evil??"
    assert len(target({"x": "y" * 900})) == 500
    # the last entry is the one the trusted proxy added; earlier ones are the caller's
    assert client_ip("6.6.6.6, 203.0.113.7", "10.0.0.2") == "203.0.113.7"
    assert client_ip(None, "10.0.0.2") == "10.0.0.2"
    assert client_ip(None, None) == ""


# --- SIEM formats ---------------------------------------------------------------------------


def test_ecs_document() -> None:
    record = chain(1)[0]
    doc = ecs(record)
    assert doc["@timestamp"] == "2026-11-06T21:20:00.001Z"
    assert doc["event"] == {
        "kind": "event",
        "category": ["web"],
        "action": "POST /staff/grants/{grt}/approve",
        "outcome": "success",
        "sequence": 1,
    }
    assert doc["http"]["request"] == {"method": "POST", "id": "req1"}
    assert doc["url"]["path"] == "/staff/grants/{grt}/approve"
    assert doc["source"]["ip"] == "203.0.113.7"
    assert doc["pipridge"]["hash"] == record.hash
    refused = ecs(replace(record, event=replace(record.event, status=403)))
    assert refused["event"]["outcome"] == "failure" and refused["event"]["category"] == ["iam"]


def test_syslog_frame_is_rfc5424_with_octet_counting() -> None:
    frame = syslog_frame(chain(1)[0], "api-1")
    length, _, msg = frame.partition(b" ")
    assert int(length) == len(msg)
    assert msg.startswith(b"<37>1 2026-11-06T21:20:00.001Z api-1 pipridge - audit - {")
    assert json.loads(msg.split(b" - audit - ", 1)[1])["event"]["sequence"] == 1


def test_sinks_from_config() -> None:
    sinks = sinks_from("stdout, file:/var/log/a.jsonl,syslog+tls://siem.example:6514", "h")
    assert [s.name for s in sinks] == [
        "stdout",
        "file:/var/log/a.jsonl",
        "syslog+tls://siem.example:6514",
    ]
    assert sinks_from("", "h") == []
    for bad in ("file:relative.jsonl", "http://siem", "syslog+udp://h:514"):
        with pytest.raises(ValueError):
            sinks_from(bad, "h")


def test_file_sink_appends_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    asyncio.run(FileSink(path).send(chain(2)))
    lines = path.read_text().splitlines()
    assert [json.loads(x)["event"]["sequence"] for x in lines] == [1, 2]
    with pytest.raises(SinkError):
        asyncio.run(FileSink(tmp_path / "missing" / "a.jsonl").send(chain(1)))


def test_syslog_sink_delivers_frames() -> None:
    async def scenario() -> bytes:
        got = bytearray()
        done = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            got.extend(await reader.read())
            writer.close()
            done.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        await SyslogSink(f"syslog+tcp://127.0.0.1:{port}", "api-1").send(chain(2))
        await asyncio.wait_for(done.wait(), 5)
        server.close()
        return bytes(got)

    data = asyncio.run(scenario())
    first_len, _, rest = data.partition(b" ")
    assert rest[int(first_len) :].split(b" ", 1)[1].startswith(b"<37>1 ")


def test_syslog_sink_failure_is_a_sink_error() -> None:
    with pytest.raises(SinkError):
        asyncio.run(SyslogSink("syslog+tcp://127.0.0.1:1", "h", timeout_s=2).send(chain(1)))


# --- the auditor --------------------------------------------------------------------------


class FakeStore:
    def __init__(self, records: list[AuditRecord]) -> None:
        self.rows = list(records)
        self.cursors: dict[str, int] = {}
        self.last: Status | None = None

    async def records(self, after: int = 0, limit: int = 1000) -> list[AuditRecord]:
        return [r for r in self.rows if r.seq > after][:limit]

    async def record(self, seq: int) -> AuditRecord | None:
        return next((r for r in self.rows if r.seq == seq), None)

    async def cursor(self, sink: str) -> int:
        return self.cursors.get(sink, 0)

    async def set_cursor(self, sink: str, seq: int) -> None:
        self.cursors[sink] = seq

    async def set_status(self, status: Status) -> None:
        self.last = status


class ListSink:
    def __init__(self, name: str = "list", fail: bool = False) -> None:
        self.name = name
        self.got: list[AuditRecord] = []
        self.fail = fail

    async def send(self, records: Sequence[AuditRecord]) -> None:
        if self.fail:
            raise SinkError("down")
        self.got.extend(records)


def auditor(
    store: FakeStore, anchors: FileAnchorStore | None, sinks: Sequence[Sink], now: list[int]
) -> Auditor:
    return Auditor(cast(AuditStore, store), anchors, sinks, lambda: now[0], anchor_ms=300_000)


def test_auditor_checks_anchors_and_ships(tmp_path: Path) -> None:
    store = FakeStore(chain(3))
    anchors = FileAnchorStore(tmp_path / "anchors.jsonl")
    sink, down = ListSink(), ListSink("down", fail=True)
    now = [T0]
    a = auditor(store, anchors, [sink, down], now)
    status = asyncio.run(a.run_once())
    assert (status.verified_seq, status.anchored_seq, status.broken_seq) == (3, 3, None)
    assert [r.seq for r in sink.got] == [1, 2, 3]
    assert store.cursors == {"list": 3}  # the failed sink keeps its place
    assert asyncio.run(anchors.anchors(CHAIN_ID)) == [Anchor(CHAIN_ID, 3, store.rows[2].hash)]
    # more records: shipped at once, anchored only after anchor_ms
    store.rows = chain(5)
    now[0] += 60_000
    status = asyncio.run(a.run_once())
    assert (status.verified_seq, status.anchored_seq) == (5, 3)
    assert [r.seq for r in sink.got] == [1, 2, 3, 4, 5]
    now[0] += 300_000
    assert asyncio.run(a.run_once()).anchored_seq == 5
    down.fail = False
    asyncio.run(a.run_once())
    assert [r.seq for r in down.got] == [1, 2, 3, 4, 5]
    # a fresh auditor (restart) checks everything again and agrees
    status = asyncio.run(auditor(store, anchors, [], now).run_once())
    assert status.broken_seq is None and status.verified_seq == 5


@pytest.mark.parametrize("tamper", ["edit", "remove_tail", "replace_all"])
def test_auditor_catches_tampering(tmp_path: Path, tamper: str) -> None:
    store = FakeStore(chain(4))
    anchors = FileAnchorStore(tmp_path / "anchors.jsonl")
    now = [T0]
    asyncio.run(auditor(store, anchors, [], now).run_once())  # anchors record 4
    if tamper == "edit":  # one record edited and every later hash recomputed
        rows, prev = [], GENESIS
        for r in store.rows:
            e = replace(r.event, actor="kc-nobody") if r.seq == 2 else r.event
            h = link(prev, r.seq, e)
            rows.append(AuditRecord(r.seq, e, prev, h))
            prev = h
        store.rows = rows
    elif tamper == "remove_tail":  # rolled back to record 2
        store.rows = store.rows[:2]
    else:  # a different, internally consistent log
        store.rows = [replace(r, event=replace(r.event, ip="")) for r in store.rows]
        prev = GENESIS
        for i, r in enumerate(store.rows):
            store.rows[i] = AuditRecord(r.seq, r.event, prev, link(prev, r.seq, r.event))
            prev = store.rows[i].hash
    sink = ListSink()
    status = asyncio.run(auditor(store, anchors, [sink], now).run_once())  # after a restart
    assert status.broken_seq is not None, tamper
    assert sink.got[-1].event.action == "audit.chain_broken"
    before = asyncio.run(anchors.anchors(CHAIN_ID))
    now[0] += 10 * 300_000
    asyncio.run(auditor(store, anchors, [], now).run_once())
    assert asyncio.run(anchors.anchors(CHAIN_ID)) == before  # never anchors a broken log


def test_auditor_catches_tampering_while_running(tmp_path: Path) -> None:
    store = FakeStore(chain(3))
    now = [T0]
    a = auditor(store, None, [], now)
    asyncio.run(a.run_once())
    store.rows = store.rows[:2]
    status = asyncio.run(a.run_once())
    assert status.broken_seq == 3 and "removed" in (status.broken_reason or "")
    store2 = FakeStore(chain(3))
    b = auditor(store2, None, [], now)
    asyncio.run(b.run_once())
    store2.rows[2] = replace(store2.rows[2], hash="f" * 64)
    assert asyncio.run(b.run_once()).broken_seq == 3


def test_stdout_sink(capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(StdoutSink().send(chain(1)))
    assert json.loads(capsys.readouterr().out)["event"]["sequence"] == 1
