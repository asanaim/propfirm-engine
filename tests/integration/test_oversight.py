"""Oversight against PostgreSQL, through the API [staff spec 5d]: dormant
accounts, escalation, access reviews, the anomaly report."""

import asyncio
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from propfirm.staff.directory import Owner
from propfirm.staff.model import DAY_MS
from propfirm.staff.oversight import OversightPolicy
from tests.integration.test_customers import Platform, platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()
OK = {"reason": "ok"}
H = 3_600_000
# every hour counts as outside the office, so each approval is reported
CLOSED = OversightPolicy(office_days=(7,))


async def ok(p: Platform, who: str, method: str, url: str, body: object = None) -> Any:
    response = await p.call(method, url, who, body, NONE)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_dormant_accounts_escalation_reviews_and_the_anomaly_report(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False,
                            clock=clock, owners=OWNERS) as p:  # fmt: skip
            assert p.oversight is not None and p.desk is not None
            # an admin and three staff led by a lead
            admin = await ok(p, "kc-owner-a", "POST", "/staff/members", {
                "subject": "kc-admin", "name": "Admin", "kind": "staff", "tier": 4,
                "reason": "hired"})  # fmt: skip
            await ok(p, "kc-owner-b", "POST",
                     f"/staff/grants/{admin['grants'][0]['grt']}/approve", OK)  # fmt: skip
            lead = await ok(p, "kc-admin", "POST", "/staff/members", {
                "subject": "kc-lead", "name": "Lead", "kind": "staff", "tier": 3,
                "reason": "hired"})  # fmt: skip
            await ok(p, "kc-owner-a", "POST", f"/staff/grants/{lead['grants'][0]['grt']}/approve",
                     OK)  # fmt: skip
            team = []
            for n in range(3):
                joined = await ok(p, "kc-lead", "POST", "/staff/members", {
                    "subject": f"kc-s{n}", "name": f"S{n}", "kind": "staff", "tier": 1,
                    "lead": lead["sid"], "reason": "hired"})  # fmt: skip
                await ok(p, "kc-admin", "POST",
                         f"/staff/grants/{joined['grants'][0]['grt']}/approve", OK)  # fmt: skip
                team.append(joined["sid"])

            # the anomaly report: the same maker and checker three times (grants)
            report = await ok(p, "kc-owner-a", "GET", "/staff/reports/anomalies?days=1")
            pairs = {(x["what"], x["maker"], x["checker"]): x["times"]
                     for x in report["repeated_pairs"]}  # fmt: skip
            assert pairs[("grant", "kc-lead", "kc-admin")] == 3
            closed = await p.oversight.anomalies(clock() - DAY_MS, CLOSED)
            assert len(closed["out_of_hours"]) == closed["approvals"] == 5
            refused = await p.call("GET", "/staff/reports/anomalies", "kc-lead", None, NONE)
            assert refused.status_code == 403

            # access reviews: the lead's team is due after 90 days
            offset["ms"] = 91 * DAY_MS
            for who in ("kc-lead", "kc-admin", "kc-owner-a", "kc-owner-b", "kc-s0"):
                await p.call("GET", "/staff/me", who, None, NONE)  # signed in today
            todo = await ok(p, "kc-lead", "GET", "/staff/me/todo")
            due = {t["ref"] for t in todo if t["kind"] == "access_review"}
            assert due == set(team)
            owner = await ok(p, "kc-owner-a", "GET", "/staff/me/todo")
            assert admin["sid"] in {t["ref"] for t in owner if t["kind"] == "access_review"}
            await ok(p, "kc-lead", "POST", f"/staff/members/{team[0]}/review",
                     {"decision": "keep", "note": "still needed"})  # fmt: skip
            not_theirs = await p.call("POST", f"/staff/members/{admin['sid']}/review", "kc-lead",
                                      {"decision": "keep", "note": "x"}, NONE)  # fmt: skip
            assert not_theirs.status_code == 403
            todo = await ok(p, "kc-lead", "GET", "/staff/me/todo")
            assert {t["ref"] for t in todo if t["kind"] == "access_review"} == set(team[1:])

            # dormant: no sign-in for 30 days; owners are exempt; an admin reinstates
            policy = OversightPolicy()
            suspended = await p.oversight.dormant(clock(), policy)
            assert set(suspended) == {team[1], team[2]}  # s0 signed in; the others never
            assert (await p.call("GET", "/staff/me", "kc-s1", None, NONE)).status_code == 403
            back = await ok(p, "kc-admin", "POST", f"/staff/members/{team[1]}/reinstate", OK)
            assert back["status"] == "active"
            assert (await p.call("GET", "/staff/me", "kc-s1", None, NONE)).status_code == 200
            assert await p.oversight.dormant(clock(), policy) == []  # reinstated today

            # escalation: an urgent alert nobody acknowledged for 4 hours
            first = await p.desk.notify("news.desk", "news_source", "news-source:bls",
                                        "Not read: bls", "add by hand", clock(), True)  # fmt: skip
            assert await p.oversight.escalate(clock() + 3 * H, policy) == []
            assert await p.oversight.escalate(clock() + 4 * H, policy) == [first]
            assert await p.oversight.escalate(clock() + 5 * H, policy) == []  # once
            leads = await ok(p, "kc-lead", "GET", "/staff/notifications")
            kinds = sorted(n["kind"] for n in leads)
            assert kinds == ["dormant", "escalation"]  # s2 still dormant; s1's closed

    asyncio.run(scenario())
