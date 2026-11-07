"""Funded onboarding without a database: the history fold, the contract, the
Didit client and webhook check, and the policy's contract section."""

import hashlib
import json
import re
from dataclasses import replace
from datetime import date
from typing import Any

import httpx
import pytest

from propfirm.customers.kyc import (
    DiditKyc,
    KycDecision,
    KycError,
    alpha2,
    didit_decision,
    didit_signature,
    didit_webhook_valid,
)
from propfirm.customers.onboarding import (
    MAX_KYC_ATTEMPTS,
    ONBOARDING_KINDS,
    STATUSES,
    OnboardingError,
    OnboardingEvent,
    OnboardingState,
    funded_rule_set,
    onboarding_state,
    render_contract,
    same_name,
)
from propfirm.customers.store import _MIGRATIONS
from propfirm.policy import DEFAULT_POLICY, PolicyError, load_policy, parse_policy

ONB = "ONB00000001"
type Step = tuple[str, dict[str, Any]]


def fold(*steps: Step) -> OnboardingState:
    return onboarding_state(ONB, [OnboardingEvent(ONB, k, n, d) for n, (k, d) in enumerate(steps)])


CREATED: Step = ("created", {"source": "progression", "rsv_id": "lite-funded-v1"})
ASKED: Step = ("kyc_requested", {"kyc": "KYC00000001"})
VERIFIED: Step = ("kyc_approved", {"kyc": "KYC00000001"})
CLEARED: Step = ("cleared", {})
ACC: Step = ("account_assigned", {"acc": "ACC00000009"})
ISSUED: Step = ("contract_issued", {"contract": "CTR00000001", "sha256": "0" * 64})
SIGNED: Step = ("signed", {"contract": "CTR00000001"})
HAPPY = (CREATED, ASKED, VERIFIED, ("screened", {"result": "clear"}), CLEARED, ACC, ISSUED)


# --- the history ----------------------------------------------------------------------


def test_the_way_to_a_funded_account() -> None:
    assert fold(CREATED).status == "verification"
    assert fold(CREATED, ASKED) == OnboardingState(
        "verification", "KYC00000001", 1, None, None, None
    )
    assert fold(*HAPPY[:3]).status == "checks"
    assert fold(*HAPPY[:5]).status == "contract"
    assert fold(*HAPPY) == OnboardingState(
        "signature", "KYC00000001", 1, "ACC00000009", "CTR00000001", None
    )
    assert fold(*HAPPY, SIGNED).status == "signed"
    attempt: Step = ("provision_attempted", {"acc": "ACC00000009", "entry_id": "1-0"})
    assert fold(*HAPPY, SIGNED, attempt, attempt).status == "signed"
    assert fold(*HAPPY, SIGNED, attempt, ("provisioned", {})).status == "provisioned"
    failed = fold(*HAPPY, SIGNED, attempt, ("failed", {"reason": "unknown_rule_set"}))
    assert (failed.status, failed.failure) == ("failed", "unknown_rule_set")


def test_a_declined_check_may_be_tried_again_up_to_the_limit() -> None:
    declined: Step = ("kyc_declined", {"kyc": "KYC00000001"})
    assert fold(CREATED, ASKED, declined).status == "verification_declined"
    second: Step = ("kyc_requested", {"kyc": "KYC00000002"})
    again = fold(CREATED, ASKED, declined, second)
    assert (again.status, again.kyc, again.kyc_attempts) == ("verification", "KYC00000002", 2)
    steps: list[Step] = [CREATED]
    for n in range(1, MAX_KYC_ATTEMPTS + 1):
        steps += [("kyc_requested", {"kyc": f"K{n}"}), ("kyc_declined", {"kyc": f"K{n}"})]
    assert fold(*steps).kyc_attempts == MAX_KYC_ATTEMPTS
    with pytest.raises(OnboardingError):
        fold(*steps, ("kyc_requested", {"kyc": "K9"}))  # no attempts left
    rejected = fold(*steps, ("rejected", {"reason": "verification_attempts"}))
    assert (rejected.status, rejected.failure) == ("rejected", "verification_attempts")


def test_a_customer_verified_before_reuses_the_check() -> None:
    reused: Step = ("kyc_approved", {"kyc": "KYC00000007", "reused": True})
    assert fold(CREATED, reused).status == "checks"
    with pytest.raises(OnboardingError):
        fold(CREATED, ("kyc_approved", {"kyc": "KYC00000007"}))  # not requested, not reused
    with pytest.raises(OnboardingError):
        fold(CREATED, ASKED, ("kyc_approved", {"kyc": "KYC00000002"}))  # another case


def test_a_review_is_decided_by_an_employee() -> None:
    review = (*HAPPY[:4], ("review_required", {"differences": ["full_name"]}))
    assert fold(*review).status == "review"
    assert fold(*review, ("approved", {"by": "s"})).status == "contract"
    denied = fold(*review, ("denied", {"by": "s"}))
    assert (denied.status, denied.failure) == ("rejected", "denied")


@pytest.mark.parametrize(
    "steps",
    [
        (),
        (ASKED,),  # before created
        (CREATED, CREATED),
        (CREATED, VERIFIED),  # approved without the case requested
        (CREATED, ASKED, ("kyc_declined", {"kyc": "KYC00000002"})),  # another case
        (CREATED, ASKED, CLEARED),  # cleared before verified
        (*HAPPY[:3], ACC),  # account before cleared
        (*HAPPY[:5], ISSUED),  # contract without an account
        (*HAPPY[:6], ACC),  # two accounts
        (*HAPPY, ("signed", {"contract": "CTR00000002"})),  # another contract
        (*HAPPY, ("provisioned", {})),  # opened before signed
        (*HAPPY[:5], ("approved", {})),  # approved without a review
        (*HAPPY, SIGNED, ("rejected", {"reason": "x"})),  # rejected after signing
        (CREATED, ("refunded", {})),  # not an onboarding event
    ],
)
def test_histories_no_valid_onboarding_has_are_refused(steps: tuple[Step, ...]) -> None:
    with pytest.raises(OnboardingError):
        fold(*steps)


def test_the_migration_allows_exactly_the_kinds_and_statuses() -> None:
    sql = dict(_MIGRATIONS)[4]
    # the kinds as widened last (migration 9, rule AC00)
    kinds = re.search(
        r"onboarding_events_kind_check\s+CHECK \(kind IN \(([^)]*)\)", dict(_MIGRATIONS)[9]
    )
    statuses = re.search(r"status IN \(([^)]*)\)", sql)
    assert kinds is not None and statuses is not None
    assert tuple(re.findall(r"'([a-z_]+)'", kinds.group(1))) == ONBOARDING_KINDS
    assert tuple(re.findall(r"'([a-z_]+)'", statuses.group(1))) == STATUSES


def test_names_match_ignoring_case_and_spacing_only() -> None:
    assert same_name("Ada  TRADER", " ada trader")
    assert not same_name("Ada Trader", "Ada Traders")
    assert not same_name("Zoë Trader", "Zoe Trader")  # accents differ


# --- the contract ---------------------------------------------------------------------


def contract(policy: Any = None) -> Any:
    policy = policy or load_policy()
    product = policy.product("lite")
    rule_set = funded_rule_set(product)
    assert rule_set is not None
    return render_contract(
        policy,
        contract_id="CTR00000001",
        issued_ts=1_793_000_000_000,
        trader_name="Ada Trader",
        cif="CIF00000001",
        kyc_case="KYC00000001",
        acc="ACC00000009",
        product=product,
        rule_set=rule_set,
        size_cents=1_000_000,
    )


def test_the_contract_states_the_terms_in_force() -> None:
    text = contract()
    assert text.template == "funded-agreement-v2" and text.draft
    assert text.sha256 == hashlib.sha256(text.body.encode()).hexdigest()
    body = text.body
    assert "DRAFT" in body and "[NOT SET" in body  # firm name and law not decided
    for fragment in (
        "Agreement CTR00000001, issued 2026-10-26",
        "Ada Trader, customer CIF00000001, identity verified under case KYC00000001",
        "Account ACC00000009, product Lite, allocation 10,000.00 (USD)",
        "Rule set lite-funded-v1: maximum loss 6 % (static)",
        "daily loss limit 3 %",
        "receives 80 % of the profit",
        "from the trading day 14 days after that of the first trade on the account",
        "each later one 14 days after the previous payout",
        "once approved, it is taken from the account and sent to the Trader's own wallet",
        "minimum payout is 50.00 (USD)",
        "approval of 2 people",
        "up to 400,000.00",
        "All trading on this account is simulated",
    ):
        assert fragment in " ".join(body.split()), fragment
    assert "$" not in body.replace("$50", "")  # every placeholder filled


def test_a_complete_contract_is_not_a_draft() -> None:
    policy = load_policy()
    terms = replace(policy.contract, firm_name="Example Funding Ltd", governing_law="England")
    text = contract(replace(policy, contract=terms))
    assert not text.draft and "DRAFT" not in text.body and "[NOT SET" not in text.body
    assert "Example Funding Ltd" in text.body and text.sha256 != contract().sha256


def test_every_shipped_product_has_a_funded_rule_set() -> None:
    for product in load_policy().products:
        assert funded_rule_set(product) is not None


@pytest.mark.parametrize(
    "change",
    [
        {"template": "../etc/passwd"},
        {"template": ""},
        {"account_disclosure": "real"},
        {"firm_name": " padded "},
        {"unexpected": "x"},
    ],
)
def test_contract_settings_are_validated(change: dict[str, str]) -> None:
    import tomllib

    data = tomllib.loads(DEFAULT_POLICY.read_text())
    data["contract"].update(change)
    with pytest.raises(PolicyError):
        parse_policy(data)


# --- Didit ----------------------------------------------------------------------------

SECRET = "whsec_test"
NOW = 1_793_000_000


def test_a_signed_webhook_is_accepted_and_anything_else_refused() -> None:
    body = json.dumps({"session_id": "s1", "status": "Approved", "score": 1.0}).encode()
    signature = didit_signature(body, SECRET)
    assert didit_webhook_valid(body, signature, str(NOW), SECRET, NOW)
    assert didit_webhook_valid(body, signature.upper(), str(NOW - 300), SECRET, NOW)
    assert not didit_webhook_valid(body, signature, str(NOW - 301), SECRET, NOW)  # too old
    assert not didit_webhook_valid(body, signature, str(NOW), "other", NOW)
    assert not didit_webhook_valid(body + b" ", signature + "0", str(NOW), SECRET, NOW)
    assert not didit_webhook_valid(body, None, str(NOW), SECRET, NOW)
    assert not didit_webhook_valid(body, signature, "soon", SECRET, NOW)
    assert not didit_webhook_valid(b"not json", signature, str(NOW), SECRET, NOW)
    assert not didit_webhook_valid(body, signature, str(NOW), "", NOW)


def test_the_signature_covers_the_canonical_body() -> None:
    """Key order, spacing and 1.0 vs 1 do not change it; a value does."""
    a = b'{"status": "Approved", "score": 1.0, "session_id": "s1"}'
    b = b'{"session_id":"s1","score":1,"status":"Approved"}'
    c = b'{"session_id":"s1","score":2,"status":"Approved"}'
    assert didit_signature(a, SECRET) == didit_signature(b, SECRET) != didit_signature(c, SECRET)
    canonical = '{"score":1,"session_id":"s1","status":"Approved"}'
    expected = hashlib.sha256  # HMAC-SHA256 of the canonical text
    import hmac

    assert (
        didit_signature(a, SECRET)
        == hmac.new(SECRET.encode(), canonical.encode(), expected).hexdigest()
    )


def test_didit_decisions() -> None:
    approved = didit_decision(
        {
            "status": "Approved",
            "id_verifications": [
                {
                    "full_name": "Ada Trader",
                    "date_of_birth": "1990-05-17",
                    "issuing_state": "DEU",
                    "document_type": "Passport",
                }
            ],
        }
    )
    assert approved == KycDecision(
        "approved",
        "Ada Trader",
        date(1990, 5, 17),
        "DE",
        {"status": "Approved", "document_type": "Passport", "issuing_state": "DEU"},
    )
    split = didit_decision(
        {"status": "Approved", "id_verifications": [{"first_name": "Ada", "last_name": "Trader"}]}
    )
    assert (split.full_name, split.date_of_birth) == ("Ada Trader", None)
    assert didit_decision({"status": "Declined"}).result == "declined"
    for waiting in ("Not Started", "In Progress", "In Review", "Resubmitted", "Expired"):
        assert didit_decision({"status": waiting}).result is None
    with pytest.raises(KycError):
        didit_decision({"status": "Approved", "id_verifications": []})
    with pytest.raises(KycError):
        didit_decision({"status": "Approved", "id_verifications": [{"date_of_birth": "17/05"}]})


def test_country_codes() -> None:
    assert (alpha2("BGD"), alpha2("ind"), alpha2("DE"), alpha2("XXX"), alpha2(None)) == (
        "BD",
        "IN",
        "DE",
        None,
        None,
    )


def test_the_didit_client() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"session_id": "abc", "url": "https://v/abc"})
        if request.url.path.endswith("/broken/decision/"):
            return httpx.Response(500)
        return httpx.Response(200, json={"status": "In Review"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            didit = DiditKyc(http, "key-1", "wf-1", "https://didit.test")
            session = await didit.start("ONB00000001", "https://portal.test")
            assert (session.provider, session.session_id, session.url) == (
                "didit",
                "abc",
                "https://v/abc",
            )
            assert (await didit.decision("abc")).result is None
            with pytest.raises(KycError):
                await didit.decision("broken")

    import asyncio

    asyncio.run(scenario())
    create, decision, _ = seen
    assert (create.method, str(create.url)) == ("POST", "https://didit.test/v3/session/")
    assert create.headers["x-api-key"] == "key-1"
    assert json.loads(create.content) == {
        "workflow_id": "wf-1",
        "vendor_data": "ONB00000001",
        "callback": "https://portal.test",
    }
    assert str(decision.url) == "https://didit.test/v3/session/abc/decision/"
