"""Newsletter campaigns through the API (D-15.2): two people decide, the
confirmed list is fixed at approval, emails leave a batch a minute with a
footer, an own unsubscribe link and the firm's address; an address that
left before its turn is skipped; a failing send is tried again."""

import asyncio
import re

import asyncpg
import pytest

from propfirm.customers.newsletter import Newsletter
from propfirm.service.feeder import now_ms
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


class Flaky:
    """Fails the first send to each address, then delivers."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, recipient: str, subject: str, body: str) -> str:
        if recipient not in self.seen:
            self.seen.add(recipient)
            raise ConnectionError("try later")
        self.sent.append((recipient, subject, body))
        return f"<{len(self.sent)}@test>"


def link(body: str, path: str) -> str:
    found = re.search(rf"{path}\?token=([\w-]+)", body)
    assert found is not None, body
    return found[1]


def test_newsletter(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.mailer is not None and p.newsletter is not None
            version = (await p.api.get("/subscribe")).json()["version"]
            for name in ("ada", "bo", "cy"):
                body = {"email": f"{name}@example.com", "source": "footer", "version": version}
                assert (await p.api.post("/subscribers", json=body)).status_code == 202
            for recipient, _, text in list(p.mailer.sent):
                if recipient in ("ada@example.com", "bo@example.com"):
                    token = link(text, "/subscribe/confirm")
                    await p.api.post("/subscribers/confirm", json={"token": token})
            sign_up_mail = len(p.mailer.sent)

            draft = {"subject": "November news", "body": "Two new account sizes this month."}
            made = await p.call("POST", "/staff/campaigns", "admin-1", draft, ADMIN)
            assert made.status_code == 201, made.text
            cid = made.json()["id"]
            url = f"/staff/campaigns/{cid}/decision"
            own = await p.call("POST", url, "admin-1", {"decision": "approve"}, ADMIN)
            assert (own.status_code, own.json()["detail"]["code"]) == (409, "same_person")
            ok = await p.call("POST", url, "admin-2", {"decision": "approve"}, ADMIN)
            assert ok.json() == {
                "id": cid,
                "state": "approved",
                "audience": 2,
            }  # cy never confirmed
            again = await p.call("POST", url, "admin-2", {"decision": "reject"}, ADMIN)
            assert again.status_code == 409

            assert await p.newsletter.send_due(now_ms(), every_ms=0) == 2
            assert await p.newsletter.send_due(now_ms(), every_ms=0) == 0  # all done
            news = p.mailer.sent[sign_up_mail:]
            assert sorted(r for r, _, _ in news) == ["ada@example.com", "bo@example.com"]
            recipient, subject, text = news[0]
            assert subject == "November news" and text.startswith("Two new account sizes")
            assert "You get this because you subscribed" in text

            # the newsletter's own link unsubscribes
            leave = link(text, "/unsubscribe")
            left = await p.api.post("/subscribers/unsubscribe", json={"token": leave})
            assert left.json() == {"unsubscribed": True}

            listed = (await p.call("GET", "/staff/campaigns", "admin-1", None, ADMIN)).json()
            assert (listed[0]["state"], listed[0]["sent"], listed[0]["decided_by"]) == (
                "approved",
                2,
                "admin-2",
            )

            # a second campaign: the one who left is skipped; a failing send retries
            made = await p.call(
                "POST",
                "/staff/campaigns",
                "admin-1",
                {"subject": "December", "body": "Winter offers start on Monday."},
                ADMIN,
            )
            second = made.json()["id"]
            done = await p.call(
                "POST",
                f"/staff/campaigns/{second}/decision",
                "admin-2",
                {"decision": "approve"},
                ADMIN,
            )
            assert done.json()["audience"] == 1  # only the one still confirmed
            flaky = Flaky()
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
            try:
                sender = Newsletter(
                    pool, p.config.customer_schema, p.settings, flaky, "https://pipridge.test"
                )
                assert await sender.send_due(now_ms(), every_ms=0) == 0  # failed, recorded
                assert await sender.send_due(now_ms(), every_ms=0) == 1  # second try
            finally:
                await pool.close()
            listed = (await p.call("GET", "/staff/campaigns", "admin-1", None, ADMIN)).json()
            assert (listed[0]["sent"], listed[0]["failed"]) == (1, 0)

            # cancelling is for the maker only
            made = await p.call(
                "POST",
                "/staff/campaigns",
                "admin-1",
                {"subject": "Draft", "body": "Not sent after all."},
                ADMIN,
            )
            third = made.json()["id"]
            other = await p.call(
                "POST",
                f"/staff/campaigns/{third}/decision",
                "admin-2",
                {"decision": "cancel"},
                ADMIN,
            )
            assert other.status_code == 403
            mine = await p.call(
                "POST",
                f"/staff/campaigns/{third}/decision",
                "admin-1",
                {"decision": "cancel"},
                ADMIN,
            )
            assert mine.json()["state"] == "cancelled"

    asyncio.run(scenario())
