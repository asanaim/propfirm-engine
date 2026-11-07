"""Where the audit trail is shipped for a SIEM to collect.

Which SIEM the firm runs is an open decision [DEFAULT: none chosen; Wazuh,
Elastic or Splunk all ingest these]: the sinks are configured, not coded
(`PROPFIRM_SIEM_SINKS`, comma-separated):

- `stdout`: JSON lines on standard output (the container log; the default)
- `file:/path/audit.jsonl`: JSON lines appended and synced to disk
- `syslog+tls://host:6514` (RFC 5425) or `syslog+tcp://host:514`
  (RFC 6587 octet counting): RFC 5424 messages carrying the JSON

Every line is one record in Elastic Common Schema field names, with the
record's seq and hash so the SIEM side can check the chain too. Times are
converted to ISO 8601 here, at the edge.
"""

import asyncio
import json
import os
import re
import ssl
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from propfirm.audit.chain import AuditRecord

APP = "pipridge"


class SinkError(Exception):
    """A sink could not take the records; the cursor stays put."""


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ecs(record: AuditRecord) -> dict[str, Any]:
    e = record.event
    system = e.status == 0
    doc: dict[str, Any] = {
        "@timestamp": iso(e.ts),
        "event": {
            "kind": "alert" if e.action.startswith("audit.") else "event",
            "category": ["iam"] if e.status in (401, 403) else ["web"],
            "action": e.action,
            "outcome": "unknown" if system else "success" if e.status < 400 else "failure",
            "sequence": record.seq,
        },
        "user": {"id": e.actor},
        "service": {"name": APP},
        "pipridge": {"target": e.target, "hash": record.hash, "prev_hash": record.prev_hash},
    }
    if not system:
        method, _, route = e.action.partition(" ")
        doc["http"] = {
            "request": {"method": method, "id": e.request_id},
            "response": {"status_code": e.status},
        }
        doc["url"] = {"path": route}
    if e.ip:
        doc["source"] = {"ip": e.ip}
    return doc


def line(record: AuditRecord) -> str:
    return json.dumps(ecs(record), sort_keys=True, separators=(",", ":"))


class Sink(Protocol):
    name: str  # the export cursor's key: no secrets in it

    async def send(self, records: Sequence[AuditRecord]) -> None: ...


class StdoutSink:
    name = "stdout"

    async def send(self, records: Sequence[AuditRecord]) -> None:
        for r in records:
            sys.stdout.write(line(r) + "\n")
        sys.stdout.flush()


class FileSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.name = f"file:{path}"

    async def send(self, records: Sequence[AuditRecord]) -> None:
        try:
            with self._path.open("a", encoding="ascii") as f:
                for r in records:
                    f.write(line(r) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            raise SinkError(f"{self.name}: {exc}") from exc


def syslog_frame(record: AuditRecord, hostname: str) -> bytes:
    """RFC 5424 (facility auth = 4; notice, or warning for a failure),
    framed by octet counting (RFC 6587 3.4.1)."""
    e = record.event
    severity = 4 if e.status >= 400 or e.action.startswith("audit.") else 5
    msg = (f"<{4 * 8 + severity}>1 {iso(e.ts)} {hostname} {APP} - audit - {line(record)}").encode()
    return str(len(msg)).encode("ascii") + b" " + msg


_SYSLOG = re.compile(r"syslog\+(tcp|tls)://([A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]):([0-9]{1,5})")


class SyslogSink:
    def __init__(self, url: str, hostname: str, timeout_s: float = 10.0) -> None:
        m = _SYSLOG.fullmatch(url)
        if m is None:
            raise ValueError(f"not a syslog sink: {url}")
        self.name = url
        self._tls = m.group(1) == "tls"
        self._host = m.group(2).strip("[]")
        self._port = int(m.group(3))
        self._hostname = re.sub(r"[^!-~]", "-", hostname)[:255] or "-"
        self._timeout = timeout_s

    async def send(self, records: Sequence[AuditRecord]) -> None:
        context = ssl.create_default_context() if self._tls else None
        try:
            async with asyncio.timeout(self._timeout):
                _, writer = await asyncio.open_connection(self._host, self._port, ssl=context)
                try:
                    for r in records:
                        writer.write(syslog_frame(r, self._hostname))
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            raise SinkError(f"{self.name}: {exc}") from exc


def sinks_from(spec: str, hostname: str) -> list[Sink]:
    out: list[Sink] = []
    for part in (p.strip() for p in spec.split(",")):
        if part == "":
            continue
        if part == "stdout":
            out.append(StdoutSink())
        elif part.startswith("file:") and part[5:].startswith("/"):
            out.append(FileSink(Path(part[5:])))
        elif part.startswith("syslog+"):
            out.append(SyslogSink(part, hostname))
        else:
            raise ValueError(f"unknown SIEM sink {part!r}")
    return out
