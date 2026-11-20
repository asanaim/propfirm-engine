import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { dayMs, type Detail, problems, ReconciliationPage } from "./Reconciliation";

const DETAIL: Detail = {
  run: "REC00000001",
  provider: "sandbox",
  covers: "in",
  period_from: Date.UTC(2026, 10, 1),
  period_to: Date.UTC(2026, 11, 1),
  filename: "nov.csv",
  uploaded_by: "admin-1",
  uploaded_at: 0,
  lines: 2,
  in_cents: 6_500,
  out_cents: 0,
  fee_cents: 110,
  counts: { matched: 1, amount_differs: 0, not_settled: 0, unmatched: 1, repeated: 0 },
  items: [
    { line: 1, occurred_at: Date.UTC(2026, 10, 20), reference: "sandbox-ORD00000001", direction: "in", amount_cents: 5_500, fee_cents: 110, currency: "USD", status: "matched", record: "ORD00000001", expected_cents: 5_500 },
    { line: 2, occurred_at: Date.UTC(2026, 10, 21), reference: "unknown-7", direction: "in", amount_cents: 1_000, fee_cents: 0, currency: "USD", status: "unmatched", record: null, expected_cents: null },
  ],
  missing: [{ kind: "order", record: "ORD00000003", reference: "sandbox-ORD00000003", amount_cents: 5_500, at: Date.UTC(2026, 10, 22) }],
};

describe("reconciliation (D-14.21)", () => {
  it("reads UTC days and keeps only the lines to look at", () => {
    expect(dayMs("2026-11-01")).toBe(Date.UTC(2026, 10, 1));
    expect(dayMs("2026-11")).toBeNull();
    expect(problems(DETAIL).map((i) => i.line)).toEqual([2]);
  });

  it("uploads a statement for a whole UTC period and shows what did not match", async () => {
    const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve(DETAIL));
    const api: Api = {
      get: <T,>(path: string) => Promise.resolve((path === "/staff/reconciliations" ? [] : DETAIL) as T),
      post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ReconciliationPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("No statement yet.")).toBeInTheDocument();
    const reconcile = screen.getByRole("button", { name: "Reconcile" });
    expect(reconcile).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Provider"), "Sandbox");
    await userEvent.type(screen.getByLabelText("From (UTC)"), "2026-11-01");
    await userEvent.type(screen.getByLabelText("To, included (UTC)"), "2026-11-30");
    const csv = "date,reference,direction,amount,currency\n2026-11-20T00:00:00Z,a,in,55.00,USD\n";
    await userEvent.upload(screen.getByLabelText("Statement file"), new File([csv], "nov.csv", { type: "text/csv" }));
    expect(await screen.findByRole("button", { name: "nov.csv" })).toBeInTheDocument();
    await userEvent.click(reconcile);
    expect(post).toHaveBeenCalledWith("/staff/reconciliations", {
      provider: "sandbox",
      covers: "both",
      period_from: Date.UTC(2026, 10, 1),
      period_to: Date.UTC(2026, 11, 1),
      filename: "nov.csv",
      csv,
    });
    expect(await screen.findByText("REC00000001: 2 lines read, 1 matched.")).toBeInTheDocument();
    expect(await screen.findByText("unknown-7")).toBeInTheDocument();
    expect(screen.queryByText("sandbox-ORD00000001")).toBeNull(); // matched: hidden until asked
    expect(screen.getByText("ORD00000003")).toBeInTheDocument(); // missing from the statement
  });
});
