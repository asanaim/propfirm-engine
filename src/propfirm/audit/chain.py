"""Audit events and their hash chain.

Each record's hash is SHA-256 over the previous record's hash and the
record's canonical JSON (its seq included), so changing, removing or
reordering any record changes every hash after it. The chain alone shows a
log is internally consistent; anchors (`propfirm.journal.anchor`) kept
outside the database show it is the log that was written.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from propfirm._checks import require_int

GENESIS = "0" * 64
# anchors of the audit chain are filed under this id (64 hex digits)
CHAIN_ID = hashlib.sha256(b"pipridge-audit-chain-v1").hexdigest()

_HEX64 = re.compile(r"[0-9a-f]{64}")
_ACTION = re.compile(r"[A-Z]{3,7} /[ -~]{0,200}|[a-z][a-z0-9_.]{0,63}")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One audited request or system event. Never a request body: the
    action names the route template, `target` its path parameters."""

    ts: int  # ms since the epoch, UTC
    actor: str  # the login's subject, or "anonymous"
    action: str  # "POST /staff/members/{sid}/screening/decide" or "audit.chain_broken"
    target: str  # "sid=STF00000001", or ""
    status: int  # HTTP status; 0 for a system event
    ip: str  # client address as the trusted proxy reported it, or ""
    request_id: str  # also returned to the client as X-Request-Id

    def __post_init__(self) -> None:
        require_int("ts", self.ts, minimum=0)
        require_int("status", self.status, minimum=0)
        if self.status > 599:
            raise ValueError("status must be at most 599")
        if not _ACTION.fullmatch(self.action):
            raise ValueError("action must be 'METHOD /route' or a dotted name")
        for name, limit in (("actor", 200), ("target", 500), ("ip", 64), ("request_id", 64)):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > limit or not value.isprintable():
                raise ValueError(f"{name} must be printable text of at most {limit} characters")
        if not self.actor:
            raise ValueError("actor must not be empty")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    seq: int
    event: AuditEvent
    prev_hash: str
    hash: str


def canonical(seq: int, event: AuditEvent) -> bytes:
    return json.dumps(
        {"seq": seq, **asdict(event)}, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def link(prev_hash: str, seq: int, event: AuditEvent) -> str:
    """The hash of record `seq` that follows `prev_hash`."""
    if not _HEX64.fullmatch(prev_hash):
        raise ValueError("prev_hash must be 64 lowercase hex digits")
    require_int("seq", seq, minimum=1)
    return hashlib.sha256(bytes.fromhex(prev_hash) + canonical(seq, event)).hexdigest()


@dataclass(frozen=True, slots=True)
class Break:
    """Where and how a chain stops being trustworthy."""

    seq: int
    reason: str


def check(records: list[AuditRecord], prev_seq: int, prev_hash: str) -> Break | None:
    """The first defect in `records`, which should continue the chain after
    (`prev_seq`, `prev_hash`) without gaps; None if they do."""
    for r in records:
        if r.seq != prev_seq + 1:
            return Break(prev_seq + 1, f"record {prev_seq + 1} is missing")
        if r.prev_hash != prev_hash:
            return Break(r.seq, "does not follow the record before it")
        if link(prev_hash, r.seq, r.event) != r.hash:
            return Break(r.seq, "its content does not match its hash")
        prev_seq, prev_hash = r.seq, r.hash
    return None
