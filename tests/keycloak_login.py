"""A browser sign-in to the development realm without a browser: the
portal client's authorization code flow with PKCE, posting Keycloak's own
forms (password, then the one-time code when the user has one)."""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import struct
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

REDIRECT = "http://localhost:5173/callback"  # the portal client's redirect address
FRONT = "http://localhost:8080"  # Keycloak's public address (KC_HOSTNAME)


def totp(secret: bytes, at: float) -> str:
    """RFC 6238 (SHA-1, 6 digits, 30 s), as Keycloak's default OTP policy."""
    digest = hmac.new(secret, struct.pack(">Q", int(at // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return data


def browser_login(
    base: str,
    user: str,
    password: str,
    otp_secret: bytes | None = None,
    *,
    pause_s: float = 0.0,
) -> dict[str, Any]:
    """Sign in through the login pages; returns the token response.
    `pause_s`: wait before redeeming the code, as a browser may."""

    def local(url: str) -> str:  # front-channel links name the public address
        return url.replace(FRONT, base, 1)

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": "portal",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid",
        "code_challenge": challenge.rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "state": secrets.token_urlsafe(8),
    }
    with httpx.Client(trust_env=False, follow_redirects=False) as http:

        def keep_secure_cookies(response: httpx.Response) -> None:
            response.read()
            for cookie in http.cookies.jar:  # Keycloak marks them Secure; this is plain http
                cookie.secure = False

        http.event_hooks["response"] = [keep_secure_cookies]

        def submit(page: str, data: dict[str, str]) -> httpx.Response:
            found = re.search(r'<form[^>]*action="([^"]+)"', page)
            assert found is not None, page[:500]
            return http.post(local(html.unescape(found.group(1))), data=data)

        auth = f"{base}/realms/propfirm/protocol/openid-connect/auth?{urlencode(params)}"
        response = http.get(auth)
        response = submit(response.text, {"username": user, "password": password})
        if response.status_code == 200 and 'name="otp"' in response.text:
            assert otp_secret is not None, "the user has a one-time code"
            # the next period's code: Keycloak accepts it and has not seen it yet
            response = submit(response.text, {"otp": totp(otp_secret, time.time() + 30)})
        location = response.headers.get("location", "")
        assert location.startswith(REDIRECT), (response.status_code, response.text[:500])
        code = parse_qs(urlparse(location).query)["code"][0]
        time.sleep(pause_s)
        token = http.post(
            f"{base}/realms/propfirm/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "portal",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            },
        )
        token.raise_for_status()
        result: dict[str, Any] = token.json()
        return result
