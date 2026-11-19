import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Me, Payout } from "../api/types";
import { captureRef, clearRef, storedRef } from "../lib/referral";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { BillingPage, CertificatesPage, certificates, gainText, identityState, InvoicePage, LeaderboardPage, ProfilePage } from "./TraderPages";

const ME: Me = {
  cif: "CIF00000001",
  email: "ada@example.com",
  full_name: "Ada Trader",
  date_of_birth: "1990-05-17",
  residence_country: "DE",
  status: "active",
  created_at: Date.UTC(2026, 9, 1),
  accounts: [{ acc: "ACC00000002", ord: "ORD00000001", product_id: "lite", rsv_id: "lite-funded-v4", size_cents: 10_000_000, stage: "funded", progression: "PRG00000001", onboarding: "ONB00000001", created_at: 0 }],
  orders: [
    { ord: "ORD00000001", product_id: "lite", size_cents: 10_000_000, list_price_cents: 38_900, discount_kind: "code", discount_percent: "10", discount_code: "SAVE10", price_cents: 35_010, rsv_id: "lite-s1-v3", status: "provisioned", acc: "ACC00000001", failure: null, created_at: Date.UTC(2026, 9, 2), paid_at: Date.UTC(2026, 9, 2) },
    { ord: "ORD00000002", product_id: "lite", size_cents: 1_000_000, list_price_cents: 5_500, discount_kind: null, discount_percent: null, discount_code: null, price_cents: 5_500, rsv_id: "lite-s1-v3", status: "pending", acc: null, failure: null, created_at: Date.UTC(2026, 9, 3), paid_at: null },
  ],
  progressions: [{ prg: "PRG00000001", ord: "ORD00000001", product_id: "lite", size_cents: 10_000_000, from_acc: "ACC00000001", from_stage: "s1", to_stage: "s2", status: "provisioned", to_acc: "ACC00000003", passed_at: Date.UTC(2026, 9, 10) }],
  onboardings: [],
};

const PAID: Payout = { po: "PO00000001", acc: "ACC00000002", cif: "CIF00000001", wal: "WAL00000001", requested_at: 0, trader_amount_cents: 120_000, debit_cents: 150_000, status: "paid", approvals: 2, debited_at: 1, paid_at: Date.UTC(2026, 10, 1), provider_ref: "x", failure: null };

describe("the client area pages", () => {
  it("formats a gain in basis points", () => {
    expect([gainText(812), gainText(-5), gainText(0)]).toEqual(["+8.12%", "-0.05%", "0.00%"]);
  });

  it("issues a certificate for each stage passed and each payout paid, newest first", () => {
    const list = certificates(ME, [PAID, { ...PAID, po: "PO00000002", status: "requested", paid_at: null }]);
    expect(list.map((c) => [c.id, c.title])).toEqual([
      ["PO00000001", "Payout"],
      ["PRG00000001", "Stage 1 passed"],
    ]);
  });

  it("says the identity check comes with the first funded account", () => {
    expect(identityState(ME)).toMatch(/Not needed yet/);
  });

  it("shows the profile and lets the trader hide from the leaderboard", async () => {
    const fetcher = fakeFetch([
      ["GET", "/me", 200, ME],
      ["GET", "/me/leaderboard", 200, { visible: true }],
      ["PUT", "/me/leaderboard", 200, { visible: false }],
    ]);
    renderWith(<ProfilePage />, fakeAuth(), fetcher);
    expect(await screen.findByText("Ada Trader")).toBeInTheDocument();
    expect(screen.getByText("Germany")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Hide me" }));
    expect(await screen.findByText(/You are hidden from the public leaderboard/)).toBeInTheDocument();
  });

  it("lists orders with an invoice only for paid ones", async () => {
    renderWith(<BillingPage />, fakeAuth(), fakeFetch([["GET", "/me", 200, ME]]));
    expect(await screen.findByText("ORD00000001")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Invoice" })).toHaveLength(1);
    expect(screen.getByText(/1 paid order, \$350.10 in all/)).toBeInTheDocument();
  });

  it("prints an invoice with the seller's details and the discount", async () => {
    const fetcher = fakeFetch([
      ["GET", "/me", 200, ME],
      ["GET", "/site", 200, { contact: { section: "contact", fields: { company_name: "Pipridge Ltd", support_email: "help@pipridge.example", address: "1 Market St" }, items: [] } }],
    ]);
    renderWith(<InvoicePage ord="ORD00000001" />, fakeAuth(), fetcher);
    expect(await screen.findByRole("heading", { name: "Invoice ORD00000001" })).toBeInTheDocument();
    expect(await screen.findByText("Pipridge Ltd")).toBeInTheDocument();
    expect(screen.getByText("Discount (code SAVE10)")).toBeInTheDocument();
    expect(screen.getByText("-$38.90")).toBeInTheDocument();
  });

  it("refuses an invoice for an unpaid order", async () => {
    renderWith(<InvoicePage ord="ORD00000002" />, fakeAuth(), fakeFetch([["GET", "/me", 200, ME], ["GET", "/site", 200, {}]]));
    expect(await screen.findByText("No invoice for this order.")).toBeInTheDocument();
  });

  it("lists certificates from passes and paid payouts", async () => {
    const fetcher = fakeFetch([
      ["GET", "/me", 200, ME],
      ["GET", "/accounts/ACC00000002/payouts", 200, [PAID]],
    ]);
    renderWith(<CertificatesPage />, fakeAuth(), fetcher);
    expect(await screen.findByText("Stage 1 passed")).toBeInTheDocument();
    expect(screen.getByText(/\$1,200.00/)).toBeInTheDocument();
  });

  it("shows the public leaderboard", async () => {
    const rows = [{ rank: 1, name: "Bo C.", country: "SG", product_id: "lite", size_cents: 10_000_000, gain_cents: 812_000, gain_basis_points: 812 }];
    renderWith(<LeaderboardPage />, fakeAuth({ status: "anonymous", session: null }), fakeFetch([["GET", "/leaderboard", 200, { rows }]]));
    expect(await screen.findByText("Bo C.")).toBeInTheDocument();
    expect(screen.getByText("+8.12%")).toBeInTheDocument();
    expect(screen.getByText("Singapore")).toBeInTheDocument();
  });
});

describe("the split ladder in the shop", () => {
  it("says the next rungs, and nothing for a product without a ladder", async () => {
    const { ladderText } = await import("./ShopPage");
    expect(ladderText({ split_after_payouts: 3, split_percent_after_payouts: "85" })).toBe("85 % after 3 paid payouts, more with scaling");
    expect(ladderText({ split_after_payouts: null, split_percent_after_payouts: null })).toBeNull();
  });
});

describe("referrals (D-14.15)", () => {
  it("shows the link and what it earned", async () => {
    const referrals = { on: true, code: "ABCD2345", commission_percent: "10", holdback_days: 30, referred: 2, accrued_cents: 1_100, available_cents: 550, entries: [] };
    const fetcher = fakeFetch([
      ["GET", "/me", 200, ME],
      ["GET", "/me/referrals", 200, referrals],
    ]);
    renderWith(<ProfilePage />, fakeAuth(), fetcher);
    expect(await screen.findByText(`${window.location.origin}/?ref=ABCD2345`)).toBeInTheDocument();
    expect(screen.getByText(/earn 10 % of what they pay for their first account, available 30 days/)).toBeInTheDocument();
    expect(screen.getByText("$11.00")).toBeInTheDocument();
    expect(screen.getByText("$5.50")).toBeInTheDocument();
  });

  it("keeps a code from a link and drops a malformed one", () => {
    clearRef();
    captureRef("?ref=bad");
    expect(storedRef()).toBeNull();
    captureRef("?x=1&ref=abcd2345");
    expect(storedRef()).toBe("ABCD2345");
    clearRef();
    expect(storedRef()).toBeNull();
  });
});
