import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StepUpRequired } from "../api/client";
import type { Product } from "../api/types";
import { Problem } from "../components/Problem";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { AccountsPage } from "./AccountsPage";
import { OverviewPage } from "./OverviewPage";
import { ShopPage } from "./ShopPage";

const LITE: Product = {
  id: "lite",
  name: "Lite",
  profit_split_percent: "80",
  code_discount_max_percent: "20",
  repeat_discount_percent: [],
  fee_refund_with_payout: null,
  sizes: [
    { size_cents: 500_000, price_cents: 2_400 },
    { size_cents: 1_000_000, price_cents: 4_400 },
  ],
  stages: [
    {
      stage: "s1",
      rsv_id: "lite-s1-v2",
      profit_target_percent: "8",
      max_loss_percent: "6",
      max_loss_kind: "static",
      daily_loss_percent: "3",
      min_trading_days: 3,
      consistency_percent: null,
      consistency_at_pass: false,
      target_basis: "closed_balance",
      flat_to_pass: true,
      tie_rule: "breach_wins",
      news_restricted: true,
    },
  ],
};

const ME = {
  cif: "CIF00000001",
  email: "u1@example.com",
  full_name: "Ada Trader",
  date_of_birth: "1990-05-17",
  residence_country: "DE",
  status: "registered",
  created_at: 0,
  accounts: [],
  orders: [],
  progressions: [],
  onboardings: [],
};

it("registers a new customer with the profile form", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 404, { detail: "not registered: POST /me first" }],
    ["POST", "/me", 201, ME],
  ]);
  renderWith(<OverviewPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Full name"), "  Ada Trader ");
  await user.type(screen.getByLabelText("Date of birth"), "1990-05-17");
  await user.selectOptions(screen.getByLabelText("Country of residence"), "DE");
  await user.click(screen.getByRole("button", { name: "Create profile" }));
  await waitFor(() => {
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
  });
  const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(post?.[1]?.body as string)).toEqual({
    full_name: "Ada Trader",
    date_of_birth: "1990-05-17",
    residence_country: "DE",
  });
});

it("explains an age refusal", async () => {
  const fetcher = fakeFetch([
    ["GET", "/me", 404, { detail: "not registered" }],
    ["POST", "/me", 403, { detail: { code: "refused", message: "registration refused: age:17<18" } }],
  ]);
  renderWith(<OverviewPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Full name"), "Young Trader");
  await user.type(screen.getByLabelText("Date of birth"), "2010-01-01");
  await user.selectOptions(screen.getByLabelText("Country of residence"), "DE");
  await user.click(screen.getByRole("button", { name: "Create profile" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("at least 18 years old");
});

it("shows the catalogue to anyone and asks a visitor to sign in to buy", async () => {
  const auth = fakeAuth({ status: "anonymous", session: null });
  renderWith(<ShopPage />, auth, fakeFetch([["GET", "/catalogue", 200, [LITE]]]));
  expect(await screen.findByRole("heading", { name: "Lite" })).toBeInTheDocument();
  expect(screen.getByText("80 % profit split")).toBeInTheDocument();
  expect(screen.getByText("Profit target").nextSibling).toHaveTextContent("8 %");
  const user = userEvent.setup();
  await user.click(screen.getByRole("radio", { name: "$10K" }));
  await user.click(screen.getByRole("button", { name: "Buy $10K for $44.00" }));
  expect(auth.signIn).toHaveBeenCalledWith("/shop");
});

it("offers the retake price after a breach", async () => {
  const offer = { of_ord: "ORD00000001", product_id: "lite", size_cents: 1_000_000, list_price_cents: 4_400, discount_percent: "10", price_cents: 3_960 };
  const fetcher = fakeFetch([
    ["GET", "/catalogue", 200, [{ ...LITE, retake_discount_percent: "10" }]],
    ["GET", "/me", 200, ME],
    ["GET", "/me/retakes", 200, [offer]],
    [
      "POST",
      "/orders",
      201,
      { ...offer, ord: "ORD00000002", discount_kind: "retake", discount_code: null, rsv_id: "lite-s1-v3", status: "pending", acc: null, failure: null, created_at: 0, paid_at: null, pay_url: "/dev/payments/ORD00000002/complete" },
    ],
  ]);
  renderWith(<ShopPage />, fakeAuth(), fetcher);
  expect(await screen.findByText(/one retake of the same size at 10 % off/)).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Retake for $39.60" }));
  const order = fetcher.mock.calls.find(([url]) => url === "/api/orders");
  expect(JSON.parse(order?.[1]?.body as string)).toEqual({ product_id: "lite", size_cents: 1_000_000 });
  expect(await screen.findByText("Order ORD00000002")).toBeInTheDocument();
});

it("places an order and completes a sandbox payment", async () => {
  const placed = {
    ord: "ORD00000001",
    product_id: "lite",
    size_cents: 500_000,
    list_price_cents: 2_400,
    discount_kind: null,
    discount_percent: null,
    discount_code: null,
    price_cents: 2_400,
    rsv_id: "lite-s1-v2",
    status: "pending",
    acc: null,
    failure: null,
    created_at: 0,
    paid_at: null,
    pay_url: "/dev/payments/ORD00000001/complete",
  };
  const fetcher = fakeFetch([
    ["GET", "/catalogue", 200, [LITE]],
    ["GET", "/me", 200, ME],
    ["POST", "/orders", 201, placed],
    ["POST", "/dev/payments/ORD00000001/complete", 200, { ...placed, status: "paid" }],
  ]);
  renderWith(<ShopPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Buy $5K for $24.00" }));
  const order = fetcher.mock.calls.find(([url]) => url === "/api/orders");
  expect(JSON.parse(order?.[1]?.body as string)).toEqual({ product_id: "lite", size_cents: 500_000 });
  await user.click(await screen.findByRole("button", { name: "Complete payment (sandbox)" }));
  expect(await screen.findByText(/Payment received/)).toBeInTheDocument();
});

it("lists accounts and orders", async () => {
  const me = {
    ...ME,
    accounts: [
      {
        acc: "ACC00000001",
        ord: "ORD00000001",
        product_id: "lite",
        rsv_id: "lite-s1-v2",
        size_cents: 1_000_000,
        stage: "s1",
        progression: null,
        onboarding: null,
        created_at: 1_793_000_000_000,
      },
    ],
  };
  renderWith(<AccountsPage />, fakeAuth(), fakeFetch([["GET", "/me", 200, me]]));
  const row = (await screen.findByRole("rowheader", { name: "ACC00000001" })).closest("tr");
  expect(row).toHaveTextContent("$10K");
  expect(row).toHaveTextContent("Stage 1");
});

it("offers the way through a step-up refusal", async () => {
  const auth = fakeAuth();
  const user = userEvent.setup();
  const { unmount } = renderWith(<Problem error={new StepUpRequired("reauth_required", "x", 300)} />, auth, fakeFetch([]));
  await user.click(screen.getByRole("button", { name: "Confirm it is you" }));
  expect(auth.stepUp).toHaveBeenCalled();
  unmount();
  renderWith(<Problem error={new StepUpRequired("mfa_required", "x", null)} />, auth, fakeFetch([]));
  await user.click(screen.getByRole("button", { name: "Set up authenticator" }));
  expect(auth.setUpAuthenticator).toHaveBeenCalled();
});

it("shows a visitor what a challenge is, what it costs and how it works", async () => {
  const auth = fakeAuth({ status: "anonymous", session: null });
  renderWith(<OverviewPage />, auth, fakeFetch([["GET", "/catalogue", 200, [LITE]]]));
  expect(await screen.findByRole("heading", { level: 1, name: /Keep 80 % of the profit/ })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { level: 3, name: "Lite" })).toBeInTheDocument();
  expect(screen.getByText("From $24.00")).toBeInTheDocument();
  expect(screen.getByText(/Profit target \+8 %/)).toBeInTheDocument();
  const steps = screen.getByRole("heading", { name: "How it works" }).nextElementSibling;
  expect(steps?.querySelectorAll("li")).toHaveLength(4);
  expect(screen.getByRole("link", { name: "Choose a challenge" })).toHaveAttribute("href", "/shop");
});

it("lets a trader choose how to pay when the firm has attached methods (rule AC13)", async () => {
  const methods = [
    { method_id: "card", name: "Card", min_cents: 0, max_cents: 0, fixed_charge_cents: 0, percent_charge: "0", instructions: "" },
    { method_id: "usdt", name: "USDT (TRC-20)", min_cents: 0, max_cents: 0, fixed_charge_cents: 0, percent_charge: "0", instructions: "" },
  ];
  const placed = { ord: "ORD00000001", product_id: "lite", size_cents: 500_000, price_cents: 2_400, status: "awaiting_payment", pay_url: "/dev/payments/ORD00000001/complete" };
  const fetcher = fakeFetch([
    ["GET", "/catalogue", 200, [LITE]],
    ["GET", "/payment-methods", 200, methods],
    ["GET", "/me", 200, { cif: "CIF00000001", email: "a@example.com", full_name: "Ada", date_of_birth: "1990-01-01", residence_country: "DE", status: "registered", created_at: 0, accounts: [], orders: [], progressions: [], onboardings: [] }],
    ["POST", "/orders", 200, placed],
  ]);
  renderWith(<ShopPage />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "USDT (TRC-20)" }));
  await user.click((await screen.findAllByRole("button", { name: /buy/i }))[0] as HTMLElement);
  await waitFor(() => {
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
  });
  const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(post?.[1]?.body as string)).toMatchObject({ method: "usdt" });
});
