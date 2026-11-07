"""Onboardings, identity checks and contracts in PostgreSQL (the customers
schema, migration 4).

`onboarding_events` is the record (append-only, trigger-enforced); the
status columns of `onboardings` are a cache of `onboarding_state`, written
in the same transaction as each event, like orders and progressions.
`kyc_cases` and `kyc_results` hold personal data: the code only inserts
them, and they carry no trigger so they stay erasable on a lawful request.
`contracts` are kept whole (trigger-enforced).
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import asyncpg

from propfirm.customers.four_eyes import SameProposer, next_event, open_proposal
from propfirm.customers.kyc import KycDecision
from propfirm.customers.onboarding import (
    ONBOARDING_KINDS,
    OnboardingEvent,
    onboarding_state,
)
from propfirm.customers.store import OrderRecord
from propfirm.pgschema import Conn, check_schema


@dataclass(frozen=True, slots=True)
class OnboardingRecord:
    onb: str
    cif: str
    source: str  # progression or order
    prg: str | None
    ord: str
    product_id: str
    size_cents: int
    rsv_id: str
    created_at: int
    status: str
    kyc: str | None
    kyc_attempts: int
    acc: str | None
    contract: str | None
    failure: str | None


@dataclass(frozen=True, slots=True)
class KycCase:
    kyc: str
    cif: str
    provider: str
    session_id: str
    url: str
    created_at: int


@dataclass(frozen=True, slots=True)
class KycResult:
    kyc: str
    result: str  # approved or declined
    full_name: str | None
    date_of_birth: date | None
    document_country: str | None
    ts: int


@dataclass(frozen=True, slots=True)
class Contract:
    contract: str
    onb: str
    template: str
    sha256: str
    body: str
    draft: bool
    created_at: int


class OnboardingStateError(Exception):
    """A request the onboarding's state refuses (the code says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _record(row: asyncpg.Record) -> OnboardingRecord:
    return OnboardingRecord(**dict(row))


class OnboardingStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        """The customers schema must be migrated (CustomerStore.open)."""
        self._pool = pool
        self._s = check_schema(schema)

    # --- creating ---------------------------------------------------------------------

    async def from_progressions(self, rsv_for: dict[str, str], ts: int) -> list[str]:
        """An onboarding for every approved funded review without one;
        `rsv_for` maps product id to its funded rule set in force (products
        missing from it wait). Return the new ONB ids."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT p.* FROM {s}.progressions p LEFT JOIN {s}.onboardings o ON o.prg = p.prg "
            f"WHERE p.status = 'approved' AND p.to_stage = 'funded' AND o.onb IS NULL "
            f"ORDER BY p.prg"
        )
        created = []
        for row in rows:
            rsv_id = rsv_for.get(row["product_id"])
            if rsv_id is None:
                continue
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    f"SELECT 1 FROM {s}.progressions WHERE prg = $1 FOR UPDATE", row["prg"]
                )
                if await conn.fetchval(f"SELECT 1 FROM {s}.onboardings WHERE prg = $1", row["prg"]):
                    continue  # another process got here first
                onb = await self._create(
                    conn,
                    "progression",
                    row["cif"],
                    row["prg"],
                    row["ord"],
                    row["product_id"],
                    row["size_cents"],
                    rsv_id,
                    ts,
                )
                created.append(onb)
        return created

    async def for_order(self, order: OrderRecord, ts: int) -> OnboardingRecord:
        """The onboarding of a paid Instant order, created once."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SELECT 1 FROM {s}.orders WHERE ord = $1 FOR UPDATE", order.ord)
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.onboardings WHERE ord = $1 AND source = 'order'", order.ord
            )
            if row is not None:
                return _record(row)
            onb = await self._create(
                conn,
                "order",
                order.cif,
                None,
                order.ord,
                order.product_id,
                order.size_cents,
                order.rsv_id,
                ts,
            )
            return await self._locked(conn, onb)

    async def order_ready(self, ord_id: str) -> bool:
        """Whether an Instant order's account may open: its agreement is signed."""
        status = await self._pool.fetchval(
            f"SELECT status FROM {self._s}.onboardings WHERE ord = $1 AND source = 'order'",
            ord_id,
        )
        return status in ("signed", "provisioned")

    async def _create(
        self,
        conn: Conn,
        source: str,
        cif: str,
        prg: str | None,
        ord_id: str,
        product_id: str,
        size_cents: int,
        rsv_id: str,
        ts: int,
    ) -> str:
        s = self._s
        number = await conn.fetchval(f"SELECT nextval('{s}.onb_seq')")
        onb = f"ONB{number:08d}"
        await conn.execute(
            f"INSERT INTO {s}.onboardings (onb, cif, source, prg, ord, product_id, size_cents, "
            f"rsv_id, created_at, status) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'verification')",
            onb,
            cif,
            source,
            prg,
            ord_id,
            product_id,
            size_cents,
            rsv_id,
            ts,
        )
        detail = {"source": source, "prg": prg, "ord": ord_id, "rsv_id": rsv_id}
        await self._append(conn, onb, "created", ts, detail)
        return onb

    # --- reading ----------------------------------------------------------------------

    async def onboarding(self, onb: str) -> OnboardingRecord | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.onboardings WHERE onb = $1", onb)
        return None if row is None else _record(row)

    async def onboardings_for(self, cif: str) -> list[OnboardingRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.onboardings WHERE cif = $1 ORDER BY onb", cif
        )
        return [_record(r) for r in rows]

    async def with_status(self, status: str, limit: int = 200) -> list[OnboardingRecord]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.onboardings WHERE status = $1 ORDER BY onb LIMIT $2",
            status,
            limit,
        )
        return [_record(r) for r in rows]

    async def history(self, onb: str) -> list[OnboardingEvent]:
        async with self._pool.acquire() as conn:
            return await self._events(conn, onb)

    # --- identity checks --------------------------------------------------------------

    async def new_case(
        self, onb: str, provider: str, session_id: str, url: str, ts: int
    ) -> OnboardingRecord:
        """Record a provider session for the onboarding's customer."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, onb)
            number = await conn.fetchval(f"SELECT nextval('{s}.kyc_seq')")
            kyc = f"KYC{number:08d}"
            await conn.execute(
                f"INSERT INTO {s}.kyc_cases (kyc, cif, provider, session_id, url, created_at) "
                f"VALUES ($1, $2, $3, $4, $5, $6)",
                kyc,
                record.cif,
                provider,
                session_id,
                url,
                ts,
            )
            detail = {"kyc": kyc, "provider": provider, "session_id": session_id}
            return await self._append(conn, onb, "kyc_requested", ts, detail)

    async def case(self, kyc: str) -> KycCase | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._s}.kyc_cases WHERE kyc = $1", kyc)
        return None if row is None else KycCase(**dict(row))

    async def case_by_session(self, provider: str, session_id: str) -> KycCase | None:
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.kyc_cases WHERE provider = $1 AND session_id = $2",
            provider,
            session_id,
        )
        return None if row is None else KycCase(**dict(row))

    async def open_cases(self, older_than: int, limit: int = 100) -> list[KycCase]:
        """Cases still waiting for a result, created before `older_than`."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT c.* FROM {s}.kyc_cases c LEFT JOIN {s}.kyc_results r ON r.kyc = c.kyc "
            f"WHERE r.kyc IS NULL AND c.created_at < $1 ORDER BY c.kyc LIMIT $2",
            older_than,
            limit,
        )
        return [KycCase(**dict(r)) for r in rows]

    async def record_result(self, kyc: str, decision: KycDecision, ts: int) -> bool:
        """Store a final outcome once; False when the case already has one."""
        assert decision.result in ("approved", "declined")
        status: str = await self._pool.execute(
            f"INSERT INTO {self._s}.kyc_results (kyc, result, full_name, date_of_birth, "
            f"document_country, ts, detail) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) "
            f"ON CONFLICT (kyc) DO NOTHING",
            kyc,
            decision.result,
            decision.full_name,
            decision.date_of_birth,
            decision.document_country,
            ts,
            json.dumps(decision.detail, sort_keys=True),
        )
        return status.endswith(" 1")

    async def result(self, kyc: str) -> KycResult | None:
        row = await self._pool.fetchrow(
            f"SELECT kyc, result, full_name, date_of_birth, document_country, ts "
            f"FROM {self._s}.kyc_results WHERE kyc = $1",
            kyc,
        )
        return None if row is None else KycResult(**dict(row))

    async def approved_case(self, cif: str) -> KycResult | None:
        """The customer's latest approved identity check (Tier 2 reuse)."""
        s = self._s
        row = await self._pool.fetchrow(
            f"SELECT r.kyc, r.result, r.full_name, r.date_of_birth, r.document_country, r.ts "
            f"FROM {s}.kyc_results r JOIN {s}.kyc_cases c ON c.kyc = r.kyc "
            f"WHERE c.cif = $1 AND r.result = 'approved' ORDER BY r.ts DESC, r.kyc DESC LIMIT 1",
            cif,
        )
        return None if row is None else KycResult(**dict(row))

    async def verified_elsewhere(self, cif: str, date_of_birth: date) -> list[tuple[str, str]]:
        """(CIF, verified name) of other customers verified with this date of birth."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT DISTINCT c.cif, r.full_name FROM {s}.kyc_results r "
            f"JOIN {s}.kyc_cases c ON c.kyc = r.kyc WHERE r.result = 'approved' "
            f"AND r.date_of_birth = $1 AND c.cif <> $2 ORDER BY c.cif",
            date_of_birth,
            cif,
        )
        return [(r["cif"], r["full_name"]) for r in rows]

    async def waiting_on(self, kyc: str) -> list[str]:
        """Onboardings whose current case is `kyc` and wait for its result."""
        rows = await self._pool.fetch(
            f"SELECT onb FROM {self._s}.onboardings WHERE kyc = $1 AND status = 'verification' "
            f"ORDER BY onb",
            kyc,
        )
        return [r["onb"] for r in rows]

    # --- steps ------------------------------------------------------------------------

    async def append(
        self, onb: str, kind: str, ts: int, detail: dict[str, Any], *, when: tuple[str, ...]
    ) -> OnboardingRecord | None:
        """Record `kind` if the onboarding's status is in `when` (checked
        under its lock); None when it is not (someone else moved it on)."""
        async with self._pool.acquire() as conn, conn.transaction():
            if (await self._locked(conn, onb)).status not in when:
                return None
            return await self._append(conn, onb, kind, ts, detail)

    async def assign_account(self, onb: str, ts: int) -> str:
        """A new ACC id for a progression's funded account, allocated once."""
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, onb)
            if record.acc is not None:
                return record.acc
            number = await conn.fetchval(f"SELECT nextval('{self._s}.acc_seq')")
            acc = f"ACC{number:08d}"
            await self._append(conn, onb, "account_assigned", ts, {"acc": acc})
            return acc

    async def record_account(self, onb: str, acc: str, ts: int) -> None:
        """An Instant order's ACC id (allocated by the order), recorded once."""
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, onb)
            if record.acc is None and record.status == "contract":
                await self._append(conn, onb, "account_assigned", ts, {"acc": acc})

    async def next_contract_id(self) -> str:
        number = await self._pool.fetchval(f"SELECT nextval('{self._s}.ctr_seq')")
        return f"CTR{number:08d}"

    async def issue_contract(
        self,
        onb: str,
        contract_id: str,
        template: str,
        body: str,
        sha256: str,
        draft: bool,
        ts: int,
    ) -> OnboardingRecord | None:
        """Store the agreement and record it issued; None unless the
        onboarding is waiting for its contract."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, onb)
            if record.status != "contract" or record.acc is None:
                return None
            await conn.execute(
                f"INSERT INTO {s}.contracts (contract, onb, template, sha256, body, draft, "
                f"created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                contract_id,
                onb,
                template,
                sha256,
                body,
                draft,
                ts,
            )
            detail: dict[str, Any] = {
                "contract": contract_id,
                "sha256": sha256,
                "template": template,
                "draft": draft,
            }
            return await self._append(conn, onb, "contract_issued", ts, detail)

    async def contract(self, contract_id: str) -> Contract | None:
        row = await self._pool.fetchrow(
            f"SELECT * FROM {self._s}.contracts WHERE contract = $1", contract_id
        )
        return None if row is None else Contract(**dict(row))

    async def mark_provisioned(
        self, onb: str, ts: int, journal_seq: int | None
    ) -> OnboardingRecord | None:
        """signed -> provisioned. For a progression, with the funded
        account's owner record; for an order, its account is the order's."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            record = await self._locked(conn, onb)
            if record.status != "signed" or record.acc is None:
                return None
            if record.source == "progression":
                await conn.execute(
                    f"INSERT INTO {s}.accounts (acc, cif, ord, product_id, rsv_id, size_cents, "
                    f"created_at, stage, progression, onboarding) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, $7, 'funded', $8, $9)",
                    record.acc,
                    record.cif,
                    record.ord,
                    record.product_id,
                    record.rsv_id,
                    record.size_cents,
                    ts,
                    record.prg,
                    onb,
                )
            detail = {"acc": record.acc, "rsv_id": record.rsv_id, "journal_seq": journal_seq}
            return await self._append(conn, onb, "provisioned", ts, detail)

    async def decide(
        self, onb: str, approve: bool, subject: str, email: str | None, reason: str, ts: int
    ) -> OnboardingRecord:
        """An employee clears or refuses an identity review; never their own."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.onboardings WHERE onb = $1 FOR UPDATE", onb
            )
            if row is None:
                raise OnboardingStateError("unknown", f"no onboarding {onb}")
            record = _record(row)
            if record.status != "review":
                raise OnboardingStateError("not_in_review", f"{onb} is {record.status}")
            own = await conn.fetchval(f"SELECT cif FROM {s}.cif WHERE subject = $1", subject)
            if own == record.cif:
                raise OnboardingStateError("own_onboarding", "nobody decides their own review")
            # rule AC00: the first decision is a proposal; a second person decides
            proposal = open_proposal(await self._events(conn, onb))
            try:
                kind, detail = next_event(proposal, approve, subject, email, reason)
            except SameProposer as exc:
                raise OnboardingStateError("four_eyes", str(exc)) from exc
            return await self._append(conn, onb, kind, ts, detail)

    # --- cache --------------------------------------------------------------------

    async def rebuild_status(self) -> list[str]:
        """Rewrite every cached status from the records; return the ONB ids
        whose cache was wrong."""
        fixed: list[str] = []
        onbs = [
            r["onb"]
            for r in await self._pool.fetch(f"SELECT onb FROM {self._s}.onboardings ORDER BY onb")
        ]
        for onb in onbs:
            async with self._pool.acquire() as conn, conn.transaction():
                before = await self._locked(conn, onb)
                if await self._write_cache(conn, onb) != before:
                    fixed.append(onb)
        return fixed

    async def _locked(self, conn: Conn, onb: str) -> OnboardingRecord:
        row = await conn.fetchrow(
            f"SELECT * FROM {self._s}.onboardings WHERE onb = $1 FOR UPDATE", onb
        )
        if row is None:
            raise KeyError(f"unknown onboarding {onb!r}")
        return _record(row)

    async def _events(self, conn: Conn, onb: str) -> list[OnboardingEvent]:
        rows = await conn.fetch(
            f"SELECT kind, ts, detail::text AS detail FROM {self._s}.onboarding_events "
            f"WHERE onb = $1 ORDER BY id",
            onb,
        )
        return [OnboardingEvent(onb, r["kind"], r["ts"], json.loads(r["detail"])) for r in rows]

    async def _append(
        self, conn: Conn, onb: str, kind: str, ts: int, detail: dict[str, Any]
    ) -> OnboardingRecord:
        """Record an event, then write the cache from the whole history; an
        event the history refuses raises, and the transaction rolls back."""
        assert kind in ONBOARDING_KINDS
        await conn.execute(
            f"INSERT INTO {self._s}.onboarding_events (onb, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            onb,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )
        return await self._write_cache(conn, onb)

    async def _write_cache(self, conn: Conn, onb: str) -> OnboardingRecord:
        state = onboarding_state(onb, await self._events(conn, onb))
        row = await conn.fetchrow(
            f"UPDATE {self._s}.onboardings SET status = $2, kyc = $3, kyc_attempts = $4, "
            f"acc = $5, contract = $6, failure = $7 WHERE onb = $1 RETURNING *",
            onb,
            state.status,
            state.kyc,
            state.kyc_attempts,
            state.acc,
            state.contract,
            state.failure,
        )
        assert row is not None
        return _record(row)
