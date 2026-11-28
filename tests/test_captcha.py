"""Turnstile verification (rule AC22): a yes only on Cloudflare's success."""

import asyncio
from urllib.parse import parse_qs

import httpx

from propfirm.customers.captcha import VERIFY_URL, turnstile


def answer(status: int, body: object, seen: list[dict[str, list[str]]]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == VERIFY_URL
        seen.append(parse_qs(request.content.decode()))
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


def test_turnstile_says_yes_only_on_success() -> None:
    seen: list[dict[str, list[str]]] = []

    def ask(status: int, body: object, ip: str | None = "1.2.3.4") -> bool:
        return asyncio.run(turnstile("sk", "tok", ip, transport=answer(status, body, seen)))

    assert ask(200, {"success": True}) is True
    assert seen[0] == {"secret": ["sk"], "response": ["tok"], "remoteip": ["1.2.3.4"]}
    assert ask(200, {"success": True}, None) is True
    assert "remoteip" not in seen[1]
    assert ask(200, {"success": False, "error-codes": ["invalid-input-response"]}) is False
    assert ask(200, {"success": "true"}) is False  # only a JSON true
    assert ask(500, {"success": True}) is False
    assert ask(200, "not json at all") is False


def test_turnstile_says_no_when_unreachable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert asyncio.run(turnstile("sk", "tok", None, transport=httpx.MockTransport(down))) is False
