"""Development only: an access token for a test user of the development
realm, signed in as a browser would (code flow with PKCE, then the
one-time code), so it carries `amr` and `auth_time` and passes the API's
staff rules (second factor at login, step-up on approvals).

    python scripts/dev_token.py staff1@example.com dev-staff-password dev-totp-secret-staff1
    TOKEN=$(python scripts/dev_token.py admin1@example.com dev-admin-password \
        dev-totp-secret-admin1)
    curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/staff/me

The third argument is the user's TOTP secret (the development realm stores
it as raw text: config/keycloak/README.md); leave it out for a user without
an authenticator. Standard library only. Never for real accounts: it types
a password into a script.
"""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import struct
import sys
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

ISSUER = "http://localhost:8080/realms/propfirm"
CLIENT = "dev-cli"
REDIRECT = "http://localhost/dev-cli/callback"


def totp(secret: str, now: float | None = None) -> str:
    """RFC 6238: SHA-1, 6 digits, 30 s; the secret is raw text."""
    counter = int((time.time() if now is None else now) // 30)
    digest = hmac.new(secret.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _form_action(page: str, form_id: str) -> str:
    found = re.search(rf'<form[^>]*id="{form_id}"[^>]*action="([^"]+)"', page)
    if found is None:
        found = re.search(rf'<form[^>]*action="([^"]+)"[^>]*id="{form_id}"', page)
    if found is None:
        raise SystemExit(f"no form {form_id}: wrong password, or not the development realm")
    return html.unescape(found.group(1))


class _LocalJar(CookieJar):
    """Keycloak's session cookies are Secure (SameSite=None); browsers send
    them to http://localhost, Python's jar does not. Development only."""

    def add_cookie_header(self, request: urllib.request.Request) -> None:
        for cookie in self:
            cookie.secure = False
        super().add_cookie_header(request)


def token(username: str, password: str, secret: str | None = None) -> str:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(_LocalJar()),
        _NoRedirect,
        urllib.request.ProxyHandler({}),
    )
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    query = urllib.parse.urlencode(
        {
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "openid",
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "state": secrets.token_urlsafe(16),
        }
    )
    page = opener.open(f"{ISSUER}/protocol/openid-connect/auth?{query}").read().decode()

    def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
        """POST a form; follow Keycloak's own redirects, stop at the callback."""
        request = urllib.request.Request(url, urllib.parse.urlencode(fields).encode())
        for _ in range(5):
            try:
                response = opener.open(request)
                return response.status, response.read().decode(), ""
            except urllib.error.HTTPError as redirect:
                location = redirect.headers.get("Location", "")
                if redirect.code not in (301, 302, 303) or location.startswith(REDIRECT):
                    return redirect.code, "", location
                request = urllib.request.Request(urllib.parse.urljoin(url, location))
        raise SystemExit("too many redirects")

    status, page, location = post(
        _form_action(page, "kc-form-login"), {"username": username, "password": password}
    )
    if not location:
        if 'id="kc-form-login"' in page:
            raise SystemExit("wrong username or password")
        if secret is None:
            raise SystemExit("this user has an authenticator: pass its TOTP secret")
        status, page, location = post(
            _form_action(page, "kc-otp-login-form"), {"otp": totp(secret)}
        )
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code")
    if not code:
        raise SystemExit(f"sign-in failed (HTTP {status})")
    exchange = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT,
            "code": code[0],
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        }
    ).encode()
    answer = json.loads(opener.open(f"{ISSUER}/protocol/openid-connect/token", exchange).read())
    access: str = answer["access_token"]
    return access


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    print(token(*sys.argv[1:]))
