"""Captcha verification (rule AC22; D-15.3): Cloudflare Turnstile.

The portal shows Turnstile's widget where a public form can be abused
(the newsletter form). The widget gives a one-time token; the API asks
Turnstile whether it is good, with the secret key, before acting. Without
a captcha switched on, forms work as before.
"""

from collections.abc import Awaitable, Callable

import httpx

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

type Verifier = Callable[[str, str, str | None], Awaitable[bool]]


async def turnstile(
    secret: str,
    token: str,
    remote_ip: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Ask Turnstile whether `token` is good. A network failure is a no:
    a form never passes unchecked while the captcha is on."""
    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            answer = await client.post(VERIFY_URL, data=data)
            if answer.status_code != 200:
                return False
            found = answer.json()
            return isinstance(found, dict) and found.get("success") is True
    except httpx.HTTPError, ValueError:
        return False
