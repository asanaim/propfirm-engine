"""Extensions through the API (rule AC22; D-15.3): a slot changes only by
a two person proposal; the public view shows switched-on slots and never a
secret; with the captcha on and its secret approved, the newsletter form
needs a good Turnstile token."""

import asyncio

import pytest

from tests.integration.test_customers import platform
from tests.integration.test_settings import ADMIN, OWNERS, two_admins, two_owners

pytestmark = pytest.mark.integration

SITE_KEY = "0x4AAAAAAAAAAAAAAA"
CHAT_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def test_extensions(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, owners=OWNERS) as p:
            off = (await p.api.get("/site/extensions")).json()
            assert off == {"chat": None, "captcha": None, "analytics": None}

            bad = await p.call(
                "PUT",
                "/admin/extensions/chat",
                "admin-1",
                {"enabled": True, "public_id": "not-a-uuid", "reason": "chat"},
                ADMIN,
            )
            assert bad.status_code == 422
            unknown = await p.call(
                "PUT",
                "/admin/extensions/ads",
                "admin-1",
                {"enabled": True, "public_id": "x", "reason": "ads"},
                ADMIN,
            )
            assert unknown.status_code == 422

            chat = {"enabled": True, "public_id": CHAT_ID, "reason": "support chat"}
            proposed = await p.call("PUT", "/admin/extensions/chat", "admin-1", chat, ADMIN)
            assert proposed.json()["status"] == "proposed"
            # not in force until a second person approves
            assert (await p.api.get("/site/extensions")).json()["chat"] is None
            pid = proposed.json()["proposal"]["id"]
            approved = await p.call(
                "POST",
                f"/admin/settings/proposals/{pid}/approve",
                "admin-2",
                {"reason": "ok"},
                ADMIN,
            )
            assert approved.status_code == 200, approved.text
            assert (await p.api.get("/site/extensions")).json()["chat"] == {
                "provider": "crisp",
                "public_id": CHAT_ID,
            }

            version = (await p.api.get("/subscribe")).json()["version"]
            body = {"email": "ada@example.com", "source": "footer", "version": version}
            # captcha on without its secret: the form still works, and the
            # back office warns
            captcha = {"enabled": True, "public_id": SITE_KEY, "reason": "bots"}
            done = await two_admins(p, "/admin/extensions/captcha", captcha)
            assert done.status_code == 200, done.text
            slots = (await p.call("GET", "/admin/extensions", "admin-1", None, ADMIN)).json()
            assert slots["captcha"] == {
                "provider": "turnstile",
                "enabled": True,
                "public_id": SITE_KEY,
                "secret": False,
            }
            assert slots["chat"]["secret"] is None
            assert (await p.api.post("/subscribers", json=body)).status_code == 202

            secret = {"value": "s3", "reason": "turnstile key"}
            # a secret, like a payment method's, is an owner's to approve (AC02)
            by_admins = await two_admins(p, "/admin/extensions/captcha/secret", secret)
            assert by_admins.status_code == 403
            done = await two_owners(p, "/admin/extensions/captcha/secret", secret)
            assert done.status_code == 200, done.text
            slots = (await p.call("GET", "/admin/extensions", "admin-1", None, ADMIN)).json()
            assert slots["captcha"]["secret"] is True
            site = (await p.api.get("/site/extensions")).json()
            assert site["captcha"] == {"provider": "turnstile", "public_id": SITE_KEY}
            assert "s3" not in str(site) and "s3" not in str(slots)

            later = {**body, "email": "bo@example.com"}
            missing = await p.api.post("/subscribers", json=later)
            assert missing.status_code == 400
            assert missing.json()["detail"]["code"] == "captcha"
            wrong = await p.api.post("/subscribers", json={**later, "captcha": "bad"})
            assert wrong.status_code == 400
            good = await p.api.post("/subscribers", json={**later, "captcha": "good"})
            assert good.status_code == 202, good.text

            # switched off: forms work without a token again
            off_body = {"enabled": False, "public_id": SITE_KEY, "reason": "off"}
            assert (await two_admins(p, "/admin/extensions/captcha", off_body)).status_code == 200
            cy = {**body, "email": "cy@example.com"}
            assert (await p.api.post("/subscribers", json=cy)).status_code == 202
            assert (await p.api.get("/site/extensions")).json()["captcha"] is None

    asyncio.run(scenario())
