"""Security emails: the wallet notice, the mailers, the send loop and the
settings, without a database or a mail server."""

import asyncio
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import pytest

from propfirm.customers.notifications import (
    NOTICE_KINDS,
    RETRY_MS,
    Notice,
    Notifications,
    Pending,
    SandboxMailer,
    SmtpMailer,
    insert_notice,
    wallet_notice,
)
from propfirm.customers.store import _MIGRATIONS
from propfirm.service.config import ConfigError, ServiceConfig

USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
OTHER = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
TS = 1_793_000_000_000  # 2026-10-26 07:33:20 UTC


def test_the_wallet_notice_says_what_changed_and_what_to_do() -> None:
    added = wallet_notice("ada@example.com", "Ada Trader", USDT, None, TS)
    assert (added.kind, added.recipient, added.subject) == (
        "wallet_changed",
        "ada@example.com",
        "Your payout wallet was added",
    )
    assert "Hello Ada Trader," in added.body
    assert "was added on 2026-10-26 07:33 UTC" in added.body
    assert f"New wallet:      {USDT} (USDT, TRC-20)" in added.body
    assert "Previous wallet" not in added.body
    assert "If you did not make this change, reply to this email at once" in added.body
    changed = wallet_notice("ada@example.com", "Ada Trader", OTHER, USDT, TS)
    assert changed.subject == "Your payout wallet was changed"
    assert f"Previous wallet: {USDT}" in changed.body and OTHER in changed.body


def test_an_unknown_notice_kind_is_refused() -> None:
    notice = Notice("newsletter", "a@example.com", "s", "b")
    with pytest.raises(ValueError):
        asyncio.run(insert_notice(None, "customers", "CIF00000001", notice, TS, {}))  # type: ignore[arg-type]


def test_the_migration_allows_exactly_the_notice_kinds() -> None:
    sql = dict(_MIGRATIONS)[7]
    latest = re.search(r"kind IN \(('wallet_changed'[^)]*)\)", dict(_MIGRATIONS)[10])
    assert latest is not None  # migration 10 widened the check (market break emails)
    assert tuple(re.findall(r"'([a-z_]+)'", latest.group(1))) == NOTICE_KINDS
    assert "BEFORE UPDATE ON {s}.notification_events" in sql  # events: append-only
    triggers = re.findall(r"BEFORE [A-Z ]+ ON \{s\}\.(\w+)", sql)
    assert triggers == ["notification_events"]  # notifications: personal data, erasable


# --- the mailers --------------------------------------------------------------------


def test_the_sandbox_mailer_sends_nothing_and_keeps_it() -> None:
    mailer = SandboxMailer()
    assert asyncio.run(mailer.send("a@example.com", "s", "b")) == "<sandbox-1@propfirm.invalid>"
    assert mailer.sent == [("a@example.com", "s", "b")]


@dataclass
class FakeSmtp:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    messages: list[EmailMessage] = field(default_factory=list)

    def factory(self, kind: str) -> Any:
        fake = self

        class Client:
            def __init__(self, host: str, port: int, **kw: Any) -> None:
                fake.calls.append((kind, (host, port, kw.get("timeout"), kw.get("context"))))

            def __enter__(self) -> Client:
                return self

            def __exit__(self, *exc: object) -> None:
                fake.calls.append(("quit", None))

            def starttls(self, context: ssl.SSLContext) -> None:
                fake.calls.append(("starttls", context))

            def login(self, user: str, password: str) -> None:
                fake.calls.append(("login", (user, password)))

            def send_message(self, message: EmailMessage) -> None:
                fake.messages.append(message)

        return Client


def test_smtp_starttls_verifies_the_server_and_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSmtp()
    monkeypatch.setattr(smtplib, "SMTP", fake.factory("smtp"))
    mailer = SmtpMailer("smtp.example.com", 587, "security@firm.example", "user", "secret")
    message_id = asyncio.run(mailer.send("ada@example.com", "Subject", "Body"))
    kinds = [k for k, _ in fake.calls]
    assert kinds == ["smtp", "starttls", "login", "quit"]
    context = fake.calls[1][1]
    assert context.check_hostname and context.verify_mode is ssl.CERT_REQUIRED
    assert fake.calls[2][1] == ("user", "secret")
    (sent,) = fake.messages
    assert (sent["From"], sent["To"], sent["Subject"]) == (
        "security@firm.example",
        "ada@example.com",
        "Subject",
    )
    assert sent["Message-ID"] == message_id and message_id.endswith("@firm.example>")
    assert sent.get_content().strip() == "Body"
    assert "secret" not in repr(mailer)


def test_smtp_implicit_tls_and_no_login(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSmtp()
    monkeypatch.setattr(smtplib, "SMTP_SSL", fake.factory("smtp_ssl"))
    mailer = SmtpMailer("smtp.example.com", 465, "security@firm.example", tls="implicit")
    asyncio.run(mailer.send("ada@example.com", "S", "B"))
    assert [k for k, _ in fake.calls] == ["smtp_ssl", "quit"]  # no STARTTLS, no login
    context = fake.calls[0][1][3]
    assert isinstance(context, ssl.SSLContext) and context.check_hostname
    with pytest.raises(ValueError):
        SmtpMailer("smtp.example.com", 25, "a@b.example", tls="none")


# --- the send loop ------------------------------------------------------------------


def pending(ntf: str, failed_at: int | None = None) -> Pending:
    return Pending(ntf, "CIF00000001", "wallet_changed", "a@example.com", "S", "B", TS, failed_at)


@dataclass
class FakeStore:
    waiting: list[Pending]
    recorded: list[tuple[str, str, int, dict[str, Any]]] = field(default_factory=list)

    async def pending(self, limit: int = 100) -> list[Pending]:
        return list(self.waiting)

    async def record(self, ntf: str, kind: str, ts: int, detail: dict[str, Any]) -> None:
        self.recorded.append((ntf, kind, ts, detail))


class DownMailer:
    name = "down"

    async def send(self, recipient: str, subject: str, body: str) -> str:
        raise ConnectionError("no route")


def test_sent_notices_are_recorded_and_failures_wait_before_a_retry() -> None:
    store = FakeStore([pending("NTF00000001"), pending("NTF00000002", failed_at=TS)])
    mailer = SandboxMailer()
    service = Notifications(store, mailer)  # type: ignore[arg-type]
    asyncio.run(service.advance(TS + RETRY_MS - 1))
    assert store.recorded == [
        (
            "NTF00000001",
            "sent",
            TS + RETRY_MS - 1,
            {"mailer": "sandbox", "message_id": "<sandbox-1@propfirm.invalid>"},
        ),
    ]  # the second failed less than a minute ago
    assert len(mailer.sent) == 1
    store.recorded.clear()
    asyncio.run(service.advance(TS + RETRY_MS))
    assert [(n, k) for n, k, _, _ in store.recorded] == [
        ("NTF00000001", "sent"),
        ("NTF00000002", "sent"),
    ]
    down = FakeStore([pending("NTF00000003")])
    asyncio.run(Notifications(down, DownMailer()).advance(TS))  # type: ignore[arg-type]
    assert down.recorded == [
        ("NTF00000003", "failed", TS, {"mailer": "down", "error": "ConnectionError"})
    ]


# --- settings ---------------------------------------------------------------------

ENV = {"DATABASE_URL": "postgresql://x", "VALKEY_URL": "redis://x"}


def test_smtp_settings() -> None:
    none = ServiceConfig.from_env(ENV)
    assert (none.smtp_host, none.smtp_port, none.smtp_tls, none.mail_from) == (
        None,
        587,
        "starttls",
        None,
    )
    full = ServiceConfig.from_env(
        {
            **ENV,
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_TLS": "implicit",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "hunter2",
            "MAIL_FROM": "security@firm.example",
        }
    )
    assert (full.smtp_host, full.smtp_port, full.smtp_tls, full.smtp_username) == (
        "smtp.example.com",
        465,
        "implicit",
        "user",
    )
    assert full.smtp_password == "hunter2" and "hunter2" not in repr(full)


@pytest.mark.parametrize(
    "change",
    [
        {"SMTP_HOST": "smtp.example.com"},  # no MAIL_FROM
        {"SMTP_TLS": "none"},
        {"SMTP_USERNAME": "user"},  # no password
        {"SMTP_PORT": "0"},
        {"SMTP_PORT": "587x"},
    ],
)
def test_invalid_smtp_settings_are_refused(change: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        ServiceConfig.from_env({**ENV, **change})
