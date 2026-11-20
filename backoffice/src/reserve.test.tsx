import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { LiabilitiesPage } from "./Liabilities";

const DESK = {
  as_of: 0,
  certain_cents: 0,
  payouts_certain_cents: 0,
  commissions_cents: 0,
  open_cents: 0,
  expected_cents: 0,
  total_cents: 0,
  funded_accounts: 0,
  largest: null,
  claims: [],
  unread: [],
  reserve: {
    reserve_cents: 100,
    certain_cents: 0,
    open_profit_cents: 0,
    expected_cents: 200,
    evaluations: 1,
    largest_customer_cents: 0,
    required_cents: 300,
    k: "0.33",
    state: "breach",
    releasable_cents: 0,
    thresholds: { amber_below: "1.30", red_below: "1.10", breach_below: "1.00", release_above: "1.30" },
    weights: { open: "0.50", expected: "1.50" },
  },
};
const MOVE = { rsm: "RSM00000001", kind: "topup", amount_cents: 500_000, reason: "capital", proposed_by: "admin-1", proposed_at: 0, status: "proposed", decided_by: null };

describe("reserve top-ups and releases (D-14.23)", () => {
  it("shows what the state puts in force, proposes a top-up and decides with a reason", async () => {
    const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    const api: Api = {
      get: <T,>(path: string) => Promise.resolve((path === "/staff/liabilities" ? DESK : [MOVE]) as T),
      post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <LiabilitiesPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText(/payout delay notice on the trader site/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Amount (USD)"), "5000");
    await userEvent.type(screen.getByLabelText("Reason"), "owners' capital");
    await userEvent.click(screen.getByRole("button", { name: "Propose" }));
    expect(post).toHaveBeenCalledWith("/staff/reserve/moves", { kind: "topup", amount_cents: 500_000, reason: "owners' capital" });
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason for a decision"), "seen the transfer");
    await userEvent.click(approve);
    expect(post).toHaveBeenCalledWith("/staff/reserve/moves/RSM00000001/decision", { approve: true, reason: "seen the transfer" });
  });
});
