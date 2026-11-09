"""Notification templates (rule AC12)."""

from typing import Any

import pytest

from propfirm.customers.notifications import wallet_notice
from propfirm.policy import PolicyError
from propfirm.settings.notices import EVENTS, Notices, NoticeTemplate, NoticeWrapper
from propfirm.settings.overlay import from_json, to_json
from tests.test_settings import OWNERS, MemoryProposals, MemoryRecords, api

USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
OTHER = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
TS = 1_793_000_000_000  # 2026-10-26 07:33:20 UTC

# the wording sent before templates existed, word for word
BEFORE = """Hello Ada Trader,

The payout wallet of your account was changed on 2026-10-26 07:33 UTC.

  New wallet:      T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb (USDT, TRC-20)
  Previous wallet: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t

Your payouts will be sent to the new wallet.

If you did not make this change, reply to this email at once and do not
sign in until we have answered: someone else may have access to your
account. We will never ask for your password or your wallet's keys."""


def test_until_one_is_approved_the_email_is_word_for_word_what_it_was() -> None:
    changed = wallet_notice("ada@example.com", "Ada Trader", OTHER, USDT, TS)
    assert (changed.subject, changed.body) == ("Your payout wallet was changed", BEFORE)
    added = wallet_notice("ada@example.com", "Ada Trader", USDT, None, TS)
    assert "Previous wallet" not in added.body
    assert f"New wallet:      {USDT} (USDT, TRC-20)\n\nYour payouts" in added.body


def test_an_approved_template_and_wrapper_write_the_email() -> None:
    t = NoticeTemplate(
        "wallet_changed",
        "Security: wallet {action}",
        "Hi {full_name}. Wallet {action} at {time}: {new_wallet}.{previous_wallet_line}",
    )
    wording = Notices({"wallet_changed": t}, "pipridge\n\n{body}\n\nSupport: reply to this email")
    n = wallet_notice("ada@example.com", "Ada Trader", OTHER, USDT, TS, wording)
    assert n.subject == "Security: wallet changed"
    assert n.body == (
        "pipridge\n\nHi Ada Trader. Wallet changed at 2026-10-26 07:33 UTC: "
        f"{OTHER}.\n  Previous wallet: {USDT}\n\nSupport: reply to this email"
    )


def test_a_value_is_never_read_as_a_variable() -> None:
    n = wallet_notice("a@example.com", "Eve {new_wallet} {body}", OTHER, None, TS)
    assert "Hello Eve {new_wallet} {body}," in n.body


@pytest.mark.parametrize(
    ("subject", "body", "error"),
    [
        ("Wallet {action}", "At {time}", "{new_wallet}"),  # a required variable missing
        ("Wallet {action}", "{new_wallet} {time} {password}", "unknown variable"),
        ("Wallet {action}", "{new_wallet} {time} {Action}", "braces"),
        ("Wallet {action}", "{new_wallet} {time} {", "braces"),
        ("Wallet\n{action}", "{new_wallet} {time}", "one line"),
        ("", "{new_wallet} {time} {action}", "required"),
        ("Wallet {action}", "{new_wallet} {time}\x07", "control"),
        ("x" * 121, "{new_wallet} {time} {action}", "at most 120"),
    ],
)
def test_a_template_that_could_not_be_sent_is_refused(subject: str, body: str, error: str) -> None:
    with pytest.raises(PolicyError, match=error.replace("{", r"\{").replace("}", r"\}")):
        NoticeTemplate("wallet_changed", subject, body)


def test_unknown_events_and_wrappers_without_one_body_are_refused() -> None:
    with pytest.raises(PolicyError, match="unknown event"):
        NoticeTemplate("newsletter", "Hi", "Hi")
    for text in ("no body here", "{body}{body}", "{body} {name}"):
        with pytest.raises(PolicyError):
            NoticeWrapper("email", text)
    with pytest.raises(PolicyError, match="channel"):
        NoticeWrapper("sms", "{body}")


def test_the_built_in_wording_passes_its_own_checks_and_round_trips() -> None:
    for name, e in EVENTS.items():
        t = NoticeTemplate(name, e.subject, e.body)
        assert from_json(*to_json(t)) == t
        assert set(e.sample) == set(e.variables)
    w = NoticeWrapper("email", "{body}\n-- pipridge")
    assert from_json(*to_json(w)) == w


def _kw() -> dict[str, Any]:
    records = MemoryRecords()
    return {"records": records, "proposals": MemoryProposals(records), "tiers": OWNERS}


def test_staff_propose_and_a_second_person_approves_new_wording() -> None:
    kw = _kw()
    view = api("GET", "/admin/notices", **kw).json()
    assert view["templates"]["wallet_changed"]["approved"] is False
    assert "new_wallet" in view["events"]["wallet_changed"]["required"]
    body = {"subject": "Wallet {action}", "body": "{new_wallet} at {time}", "reason": "shorter"}
    put = api("PUT", "/admin/notices/wallet_changed", body, user="admin-1", **kw)
    assert put.status_code == 200, put.text
    pid = put.json()["proposal"]["id"]
    own = api("POST", f"/admin/settings/proposals/{pid}/approve", {"reason": "x"},
              user="admin-1", **kw)  # fmt: skip
    assert own.json()["detail"]["code"] == "four_eyes"
    ok = api("POST", f"/admin/settings/proposals/{pid}/approve", {"reason": "ok"},
             user="admin-2", **kw)  # fmt: skip
    assert ok.status_code == 200, ok.text
    view = api("GET", "/admin/notices", **kw).json()
    assert view["templates"]["wallet_changed"] == {
        "subject": "Wallet {action}",
        "body": "{new_wallet} at {time}",
        "approved": True,
    }


def test_a_bad_template_or_wrapper_is_refused_before_it_is_proposed() -> None:
    kw = _kw()
    bad = {"subject": "Wallet {action}", "body": "no wallet", "reason": "x"}
    refused = api("PUT", "/admin/notices/wallet_changed", bad, user="admin-1", **kw)
    assert refused.status_code == 422 and "{new_wallet}" in refused.json()["detail"]
    wrapper = api("PUT", "/admin/notice-wrapper/email", {"text": "hello", "reason": "x"},
                  user="admin-1", **kw)  # fmt: skip
    assert wrapper.status_code == 422
    good = api("PUT", "/admin/notice-wrapper/email", {"text": "{body}\n\npipridge", "reason": "x"},
               user="admin-1", **kw)  # fmt: skip
    assert good.status_code == 200
    trader = api("PUT", "/admin/notice-wrapper/email", {"text": "{body}", "reason": "x"},
                 roles=("trader",), user="u9", **kw)  # fmt: skip
    assert trader.status_code in (401, 403)
