"""Subscribers with a consent record through the API (rule AC25): double
opt-in by an emailed link, the wording shown kept with the consent, no
second email within 24 hours, unsubscribe by link, the staff list and CSV."""

import asyncio
import re

import pytest

from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def token(body: str, path: str) -> str:
    found = re.search(rf"{path}\?token=([\w-]+)", body)
    assert found is not None, body
    return found[1]


def test_subscribers(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.mailer is not None
            shown = (await p.api.get("/subscribe")).json()
            assert shown["on"] and "unsubscribes" in shown["wording"]
            body = {"email": " Ada@Example.com ", "source": "footer", "version": shown["version"]}

            stale = await p.api.post("/subscribers", json={**body, "version": "000000000000"})
            assert (stale.status_code, stale.json()["detail"]["code"]) == (400, "old_wording")
            bad = await p.api.post("/subscribers", json={**body, "email": "not-an-email"})
            assert bad.status_code == 400

            asked = await p.api.post("/subscribers", json=body, headers={"X-Country": "BD"})
            assert (asked.status_code, asked.json()) == (202, {"status": "check_email"})
            again = await p.api.post("/subscribers", json=body)
            assert again.status_code == 202
            assert len(p.mailer.sent) == 1  # no second email within 24 hours
            recipient, subject, text = p.mailer.sent[0]
            assert (recipient, subject) == ("ada@example.com", "Confirm your subscription")

            listed = (await p.call("GET", "/staff/subscribers", "admin-1", None, ADMIN)).json()
            (consent,) = listed
            assert consent["status"] == "pending" and consent["country"] == "BD"
            assert consent["wording"] == shown["wording"] and consent["source"] == "footer"

            confirm = token(text, "/subscribe/confirm")
            wrong = await p.api.post("/subscribers/confirm", json={"token": "x" * 43})
            assert wrong.json() == {"confirmed": False}
            done = await p.api.post("/subscribers/confirm", json={"token": confirm})
            assert done.json() == {"confirmed": True}
            twice = await p.api.post("/subscribers/confirm", json={"token": confirm})
            assert twice.json() == {"confirmed": False}

            csv = await p.call("GET", "/staff/subscribers.csv", "admin-1", None, ADMIN)
            lines = csv.text.strip().splitlines()
            assert lines[0].startswith("email,subscribed_at,confirmed_at")
            assert lines[1].startswith("ada@example.com,")

            # already on the list: asking again sends nothing and says nothing
            assert (await p.api.post("/subscribers", json=body)).status_code == 202
            assert len(p.mailer.sent) == 1

            leave = token(text, "/unsubscribe")
            left = await p.api.post("/subscribers/unsubscribe", json={"token": leave})
            assert left.json() == {"unsubscribed": True}
            state = (await p.call("GET", "/staff/subscribers", "admin-1", None, ADMIN)).json()
            assert state[0]["status"] == "unsubscribed"
            confirmed = await p.call(
                "GET", "/staff/subscribers?status=confirmed", "admin-1", None, ADMIN
            )
            assert confirmed.json() == []

    asyncio.run(scenario())
