"""Notification templates (rule AC12, 9 Nov 2026).

Each email the firm sends for an event has a template: a subject and a
body with variables in braces (`{full_name}`), filled in when the email is
written. One wrapper per channel holds the text around every body (a
greeting line, a footer); it must contain `{body}` once. Templates and the
wrapper are settings: a change is proposed and a second admin or owner
approves it (rule AC00); until one is approved, the built-in wording
below is used, which is exactly the wording sent before templates existed.

Checks on every template, so an approved one can always be sent: only the
event's own variables, every variable the event needs present (an email
about a new payout wallet that omitted the wallet would defeat its
purpose), no other braces, plain text within length limits. Email is the
only channel the firm sends today; SMS and push join here when they do.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from propfirm.policy import PolicyError

NOTICE_TEMPLATE = "notice_template"
NOTICE_WRAPPER = "notice_wrapper"
CHANNELS = ("email",)
SUBJECT_MAX = 120
BODY_MAX = 4000
WRAPPER_MAX = 2000
_VAR = re.compile(r"\{([a-z_]{1,40})\}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # tabs and newlines are allowed


@dataclass(frozen=True, slots=True)
class Event:
    """An event the firm writes to a customer about."""

    label: str
    variables: Mapping[str, str]  # name: what it holds
    required: frozenset[str]
    subject: str  # the built-in wording
    body: str
    sample: Mapping[str, str] = field(default_factory=dict)


EVENTS: Mapping[str, Event] = {
    "wallet_changed": Event(
        label="Payout wallet added or changed (security email)",
        variables={
            "full_name": "the customer's registered name",
            "action": "added or changed",
            "time": "when it happened, in UTC",
            "new_wallet": "the new wallet address",
            "previous_wallet_line": (
                "a line naming the previous wallet, starting on a new line; "
                "empty when the first wallet is added"
            ),
        },
        required=frozenset({"action", "time", "new_wallet"}),
        subject="Your payout wallet was {action}",
        body="\n".join(
            [
                "Hello {full_name},",
                "",
                "The payout wallet of your account was {action} on {time}.",
                "",
                "  New wallet:      {new_wallet} (USDT, TRC-20){previous_wallet_line}",
                "",
                "Your payouts will be sent to the new wallet.",
                "",
                "If you did not make this change, reply to this email at once and do not",
                "sign in until we have answered: someone else may have access to your",
                "account. We will never ask for your password or your wallet's keys.",
            ]
        ),
        sample={
            "full_name": "Ada Trader",
            "action": "changed",
            "time": "2026-11-09 14:05 UTC",
            "new_wallet": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
            "previous_wallet_line": "\n  Previous wallet: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        },
    ),
    "market_break_close": Event(
        label="Trades closed before a market break (funded accounts)",
        variables={
            "full_name": "the customer's registered name",
            "acc": "the trading account number",
            "time": "when the trades were closed, in UTC",
            "closed_lines": (
                "one line per position closed and per pending order cancelled, "
                "each starting on a new line"
            ),
        },
        required=frozenset({"acc", "time", "closed_lines"}),
        subject="Account {acc}: trades closed before the market break",
        body="\n".join(
            [
                "Hello {full_name},",
                "",
                "Your funded account {acc} had trades open shortly before a market",
                "closed for 2 hours or more (a weekend or a holiday). As the rules of",
                "your account set out, they were closed automatically on {time}:",
                "{closed_lines}",
                "",
                "This is not a rule breach and your account stays active. You can trade",
                "again on these markets when they reopen. The trading history in your",
                "account shows each closing price.",
            ]
        ),
        sample={
            "full_name": "Ada Trader",
            "acc": "ACC00000001",
            "time": "2026-11-20 21:55 UTC",
            "closed_lines": (
                "\n  EUR/USD buy 1.00 lot: closed, result -10.00 USD"
                "\n  XAU/USD pending order o7: cancelled"
            ),
        },
    ),
}
DEFAULT_WRAPPER = "{body}"


def _check_text(what: str, text: str, longest: int, allowed: Iterable[str]) -> set[str]:
    """The variables a text uses; refuses anything that could not be sent."""
    if not isinstance(text, str) or not text.strip():
        raise PolicyError(f"{what}: required")
    if len(text) > longest:
        raise PolicyError(f"{what}: at most {longest} characters")
    if _CONTROL.search(text):
        raise PolicyError(f"{what}: control characters are not allowed")
    used = set(_VAR.findall(text))
    unknown = used - set(allowed)
    if unknown:
        raise PolicyError(f"{what}: unknown variable {{{sorted(unknown)[0]}}}")
    if "{" in _VAR.sub("", text) or "}" in _VAR.sub("", text):
        raise PolicyError(f"{what}: braces are only for variables, such as {{full_name}}")
    return used


@dataclass(frozen=True, slots=True)
class NoticeTemplate:
    """The wording of one event's email."""

    event: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        spec = EVENTS.get(self.event)
        if spec is None:
            raise PolicyError(f"unknown event {self.event!r}")
        if "\n" in self.subject:
            raise PolicyError("subject: one line")
        used = _check_text("subject", self.subject, SUBJECT_MAX, spec.variables)
        used |= _check_text("body", self.body, BODY_MAX, spec.variables)
        missing = spec.required - used
        if missing:
            raise PolicyError(f"the email must include {{{sorted(missing)[0]}}}")


@dataclass(frozen=True, slots=True)
class NoticeWrapper:
    """The text around every email body; `{body}` once."""

    channel: str
    text: str

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise PolicyError(f"unknown channel {self.channel!r}")
        _check_text("wrapper", self.text, WRAPPER_MAX, ("body",))
        if self.text.count("{body}") != 1:
            raise PolicyError("wrapper: include {body} exactly once")


def _fill(text: str, values: Mapping[str, str]) -> str:
    return _VAR.sub(lambda m: values[m.group(1)], text)


@dataclass(frozen=True, slots=True)
class Notices:
    """The templates in force: approved ones, else the built-in wording."""

    templates: Mapping[str, NoticeTemplate] = field(default_factory=dict)
    wrapper: str = DEFAULT_WRAPPER

    def template(self, event: str) -> NoticeTemplate:
        found = self.templates.get(event)
        if found is not None:
            return found
        spec = EVENTS[event]
        return NoticeTemplate(event, spec.subject, spec.body)

    def render(self, event: str, values: Mapping[str, str]) -> tuple[str, str]:
        """(subject, body) for an event; `values` gives every variable."""
        spec = EVENTS[event]
        if set(values) != set(spec.variables):
            raise ValueError(f"{event}: values for {sorted(spec.variables)} expected")
        t = self.template(event)
        body = _fill(self.wrapper, {"body": _fill(t.body, values)})
        return _fill(t.subject, values), body


def utc_text(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def template_json(t: NoticeTemplate) -> dict[str, Any]:
    return {"event": t.event, "subject": t.subject, "body": t.body}


def template_from_json(value: Mapping[str, Any]) -> NoticeTemplate:
    return NoticeTemplate(value["event"], value["subject"], value["body"])


def wrapper_json(w: NoticeWrapper) -> dict[str, Any]:
    return {"channel": w.channel, "text": w.text}


def wrapper_from_json(value: Mapping[str, Any]) -> NoticeWrapper:
    return NoticeWrapper(value["channel"], value["text"])
