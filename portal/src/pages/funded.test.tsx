import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Contract, Eligibility, Onboarding, Payout } from "../api/types";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { FundedPage, OnboardingPage, sha256Hex } from "./FundedPage";
import { PayoutsPage, looksLikeTron } from "./PayoutsPage";

const ONB: Onboarding = {
  onb: "ONB00000001",
  cif: "CIF00000001",
  source: "progression",
  prg: "PRG00000002",
  ord: null,
  product_id: "lite",
  size_cents: 1_000_000,
  rsv_id: "lite-funded-v1",
  status: "verification",
  kyc: null,
  kyc_attempts: 0,
  acc: null,
  contract: null,
  failure: null,
  created_at: 0,
  verification_url: null,
};

const ME = {
  cif: "CIF00000001",
  email: "u1@example.com",
  full_name: "Ada Trader",
  date_of_birth: "1990-05-17",
  residence_country: "DE",
  status: "registered",
  created_at: 0,
  accounts: [
    {
      acc: "ACC00000003",
      ord: "ORD00000001",
      product_id: "lite",
      rsv_id: "lite-funded-v1",
      size_cents: 1_000_000,
      stage: "funded",
      progression: null,
      onboarding: "ONB00000001",
      created_at: 0,
    },
  ],
  orders: [],
  progressions: [],
  onboardings: [ONB],
};

function bodyOf(fetcher: ReturnType<typeof fakeFetch>, path: string): unknown {
  const call = fetcher.mock.calls.find(([url, init]) => url === `/api${path}` && init?.method === "POST");
  return call?.[1]?.body === undefined ? undefined : (JSON.parse(call[1].body as string) as unknown);
}

it("lists the funded onboardings with their status", async () => {
  renderWith(<FundedPage />, fakeAuth(), fakeFetch([["GET", "/me", 200, ME]]));
  expect(await screen.findByRole("link", { name: "ONB00000001" })).toHaveAttribute("href", "/funded/ONB00000001");
  expect(screen.getByText("Verify your identity")).toBeInTheDocument();
});

it("starts the identity check, then completes it in the sandbox", async () => {
  const started = { ...ONB, kyc: "KYC00000001", kyc_attempts: 1, verification_url: "/dev/kyc/sandbox-x/complete" };
  const fetcher = fakeFetch([
    ["GET", "/me/onboardings/ONB00000001", 200, ONB],
    ["POST", "/me/onboardings/ONB00000001/verification", 200, started],
    ["POST", "/dev/kyc/sandbox-x/complete", 200, { result: "approved" }],
  ]);
  renderWith(<OnboardingPage onb="ONB00000001" />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Start identity check" }));
  await user.click(await screen.findByRole("button", { name: "Complete identity check (sandbox)" }));
  await waitFor(() => {
    expect(bodyOf(fetcher, "/dev/kyc/sandbox-x/complete")).toEqual({ result: "approved" });
  });
});

it("links to the provider's identity check outside the sandbox", async () => {
  const live = { ...ONB, kyc: "KYC00000001", verification_url: "https://verify.didit.me/session/abc" };
  renderWith(
    <OnboardingPage onb="ONB00000001" />,
    fakeAuth(),
    fakeFetch([["GET", "/me/onboardings/ONB00000001", 200, live]]),
  );
  const link = await screen.findByRole("link", { name: "Continue identity check" });
  expect(link).toHaveAttribute("href", "https://verify.didit.me/session/abc");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

it("signs the agreement with the typed name and the text's fingerprint", async () => {
  const body = "FUNDED TRADER AGREEMENT\n\nThe account is simulated.";
  const contract: Contract = {
    contract: "CON00000001",
    template: "funded-agreement-v2",
    sha256: await sha256Hex(body),
    draft: true,
    body,
    created_at: 0,
  };
  const signing = { ...ONB, status: "signature" as const, contract: "CON00000001" };
  const fetcher = fakeFetch([
    ["GET", "/me/onboardings/ONB00000001", 200, signing],
    ["GET", "/me/onboardings/ONB00000001/contract", 200, contract],
    ["POST", "/me/onboardings/ONB00000001/sign", 200, { ...signing, status: "signed" }],
  ]);
  renderWith(<OnboardingPage onb="ONB00000001" />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  expect(await screen.findByText(/This agreement is a draft/)).toBeInTheDocument();
  const sign = screen.getByRole("button", { name: "Sign agreement" });
  expect(sign).toBeDisabled();
  await user.click(screen.getByLabelText("I have read the agreement and accept it"));
  await user.type(screen.getByLabelText("Your full name, as on your identity document"), " Ada Trader ");
  await user.click(sign);
  await waitFor(() => {
    expect(bodyOf(fetcher, "/me/onboardings/ONB00000001/sign")).toEqual({
      full_name: "Ada Trader",
      sha256: contract.sha256,
    });
  });
  expect(await screen.findByText(/your account is being opened/)).toBeInTheDocument();
});

it("refuses to sign an agreement whose text does not match its fingerprint", async () => {
  const contract: Contract = {
    contract: "CON00000001",
    template: "funded-agreement-v2",
    sha256: "0".repeat(64),
    draft: false,
    body: "changed in transit",
    created_at: 0,
  };
  const signing = { ...ONB, status: "signature" as const, contract: "CON00000001" };
  renderWith(
    <OnboardingPage onb="ONB00000001" />,
    fakeAuth(),
    fakeFetch([
      ["GET", "/me/onboardings/ONB00000001", 200, signing],
      ["GET", "/me/onboardings/ONB00000001/contract", 200, contract],
    ]),
  );
  expect(await screen.findByText(/did not arrive intact/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign agreement" })).toBeDisabled();
});

const ELIGIBLE: Eligibility = {
  acc: "ACC00000003",
  as_of: 0,
  eligible: true,
  basis: "closed_balance",
  payable_cents: 100_000,
  split_percent: "80",
  trader_share_cents: 80_000,
  firm_share_cents: 20_000,
  fee_refund_cents: 4_400,
  trader_amount_cents: 84_400,
  debit_cents: 100_000,
  trading_days: 4,
  opens_at: null,
  checks: [{ name: "profit", status: "pass", detail: "profit above the baseline" }],
};

const PAYOUT: Payout = {
  po: "PO00000001",
  acc: "ACC00000003",
  cif: "CIF00000001",
  wal: "WAL00000001",
  requested_at: 0,
  trader_amount_cents: 84_400,
  debit_cents: 100_000,
  status: "requested",
  approvals: 0,
  debited_at: null,
  paid_at: null,
  provider_ref: null,
  failure: null,
};

const ADDRESS = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8";

it("checks the shape of a TRON address", () => {
  expect(looksLikeTron(ADDRESS)).toBe(true);
  expect(looksLikeTron("0x1234")).toBe(false);
  expect(looksLikeTron(`${ADDRESS.slice(0, 33)}0`)).toBe(false); // 0 is not Base58
});

it("adds a payout wallet and asks for the second factor when needed", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 200, ME],
    ["GET", "/me/wallet", 404, { detail: "no payout wallet" }],
    ["GET", "/accounts/ACC00000003/payout", 200, ELIGIBLE],
    ["GET", "/accounts/ACC00000003/payouts", 200, []],
    [
      "POST",
      "/me/wallet",
      401,
      { detail: { code: "reauth_required", message: "confirm" } },
      { "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", max_age=300' },
    ],
  ]);
  const auth = fakeAuth();
  renderWith(<PayoutsPage />, auth, fetcher);
  const user = userEvent.setup();
  const save = await screen.findByRole("button", { name: "Save wallet" });
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText("USDT wallet address (TRON, TRC-20)"), ADDRESS);
  await user.click(save);
  await user.click(await screen.findByRole("button", { name: "Confirm it is you" }));
  expect(auth.stepUp).toHaveBeenCalled();
  expect(bodyOf(fetcher, "/me/wallet")).toEqual({ address: ADDRESS });
});

it("shows what is payable and requests the payout", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 200, ME],
    ["GET", "/me/wallet", 200, { wal: "WAL00000001", network: "tron", address: ADDRESS, added_at: 0 }],
    ["GET", "/accounts/ACC00000003/payout", 200, ELIGIBLE],
    ["GET", "/accounts/ACC00000003/payouts", 200, []],
    ["POST", "/accounts/ACC00000003/payouts", 201, PAYOUT],
  ]);
  renderWith(<PayoutsPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  expect(await screen.findByText(ADDRESS)).toBeInTheDocument();
  expect(await screen.findByText("$844.00")).toBeInTheDocument();
  expect(screen.getByText("$44.00")).toBeInTheDocument(); // the fee refund comes with it
  await user.click(screen.getByRole("button", { name: "Request payout of $844.00" }));
  await waitFor(() => {
    expect(
      fetcher.mock.calls.some(([url, init]) => url === "/api/accounts/ACC00000003/payouts" && init?.method === "POST"),
    ).toBe(true);
  });
});

it("lets the trader cancel a payout still being approved", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 200, ME],
    ["GET", "/me/wallet", 200, { wal: "WAL00000001", network: "tron", address: ADDRESS, added_at: 0 }],
    ["GET", "/accounts/ACC00000003/payout", 200, { ...ELIGIBLE, eligible: false }],
    ["GET", "/accounts/ACC00000003/payouts", 200, [PAYOUT]],
    ["POST", "/me/payouts/PO00000001/cancel", 200, { ...PAYOUT, status: "cancelled" }],
  ]);
  renderWith(<PayoutsPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  expect(await screen.findByText("Being approved")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Request payout/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => {
    expect(
      fetcher.mock.calls.some(([url, init]) => url === "/api/me/payouts/PO00000001/cancel" && init?.method === "POST"),
    ).toBe(true);
  });
});

it("checks the payout again once the wallet is saved", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 200, ME],
    ["GET", "/me/wallet", 404, { detail: "no payout wallet" }],
    ["GET", "/accounts/ACC00000003/payout", 200, { ...ELIGIBLE, eligible: false }],
    ["GET", "/accounts/ACC00000003/payouts", 200, []],
    ["POST", "/me/wallet", 201, { wal: "WAL00000001", network: "tron", address: ADDRESS, added_at: 0 }],
  ]);
  renderWith(<PayoutsPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("USDT wallet address (TRON, TRC-20)"), ADDRESS);
  await user.click(screen.getByRole("button", { name: "Save wallet" }));
  expect(await screen.findByText("USDT on TRON (TRC-20)")).toBeInTheDocument();
  const reads = () => fetcher.mock.calls.filter(([url]) => url === "/api/accounts/ACC00000003/payout").length;
  await waitFor(() => {
    expect(reads()).toBe(2);
  });
});

it("shows the approved identity-check words (rule AC15), keeping the attempts count", async () => {
  const declined = { ...ONB, status: "verification_declined" as const, kyc_attempts: 1 };
  const kyc = {
    fields: {
      required_title: "Prove it is you",
      required_text: "A photo ID and a selfie, checked by our provider.",
      pending_title: "Almost there",
      pending_text: "We review within a day.",
      declined_text: "Declined: use an ID in your own name.",
    },
    items: [],
  };
  renderWith(
    <OnboardingPage onb="ONB00000001" />,
    fakeAuth(),
    fakeFetch([
      ["GET", "/me/onboardings/ONB00000001", 200, declined],
      ["GET", "/site", 200, { kyc }],
    ]),
  );
  expect(await screen.findByRole("heading", { name: "Prove it is you" })).toBeInTheDocument();
  expect(screen.getByText("Declined: use an ID in your own name.")).toBeInTheDocument();
  expect(screen.getByText(/A photo ID and a selfie, checked by our provider\.\s+Attempts used: 1 of 3\./)).toBeInTheDocument();
});
