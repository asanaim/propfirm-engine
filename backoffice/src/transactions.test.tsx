import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider, type AuthProvider } from "react-admin";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { screensFor } from "./screens";
import { ReportsPage, dayMs, txParams, type EmailRow, type TxLog } from "./Transactions";

const LOG: TxLog = {
  rows: [
    { ref: "PO00000003", type: "payout", cif: "CIF00000001", acc: "ACC00000001", amount_cents: 25_050, direction: "out", status: "paid", remark: "", at: Date.UTC(2026, 10, 10), settled_at: Date.UTC(2026, 10, 11) },
    { ref: "ORD00000001", type: "purchase", cif: "CIF00000001", acc: "ACC00000001", amount_cents: 9_900, direction: "in", status: "provisioned", remark: "LITE_10K", at: Date.UTC(2026, 10, 1), settled_at: Date.UTC(2026, 10, 1) },
  ],
  totals: { count: 2, in_cents: 9_900, out_cents: 25_050 },
};
const EMAILS: EmailRow[] = [{ ntf: "NTF00000001", cif: "CIF00000001", kind: "wallet_changed", subject: "Your payout wallet was added", created_at: Date.UTC(2026, 10, 9), delivery: "sent" }];

function mount(asked: string[], permissions: string[], exported: string[] = []) {
  const api: Api = {
    get: <T,>(path: string) => {
      asked.push(path);
      return Promise.resolve((path.startsWith("/staff/reports/emails") ? EMAILS : LOG) as T);
    },
    post: () => Promise.reject(new Error("read only")),
    put: () => Promise.reject(new Error("read only")),
    text: (path: string) => {
      exported.push(path);
      return Promise.resolve("ref\r\n");
    },
  };
  const auth: AuthProvider = {
    login: () => Promise.resolve(),
    logout: () => Promise.resolve(),
    checkAuth: () => Promise.resolve(),
    checkError: () => Promise.resolve(),
    getPermissions: () => Promise.resolve(permissions),
  };
  render(
    <MemoryRouter>
      <AdminContext dataProvider={testDataProvider()} authProvider={auth}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ReportsPage />
        </BackOfficeContext.Provider>
      </AdminContext>
    </MemoryRouter>,
  );
}

describe("the transaction log (rule AC20)", () => {
  it("shows the rows, signed amounts and settled totals", async () => {
    const asked: string[] = [];
    mount(asked, ["staff.view"]);
    expect(await screen.findByText("PO00000003")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "transactions" });
    expect(within(table).getByText("-250.50 USD")).toBeInTheDocument();
    expect(within(table).getByText("+99.00 USD")).toBeInTheDocument();
    expect(screen.getByText("Out (settled): 250.50 USD")).toBeInTheDocument();
    expect(asked[0]).toBe("/staff/reports/transactions?limit=200");
  });

  it("exports the filtered log for staff with full view", async () => {
    const asked: string[] = [];
    const exported: string[] = [];
    URL.createObjectURL = () => "blob:x";
    URL.revokeObjectURL = () => undefined;
    mount(asked, ["staff.view"], exported);
    await screen.findByText("PO00000003");
    await userEvent.type(screen.getByLabelText("Remark"), "lite");
    await waitFor(() => {
      expect(asked).toContain("/staff/reports/transactions?remark=lite&limit=200");
    });
    await userEvent.click(await screen.findByRole("button", { name: "Export CSV" }));
    expect(exported).toEqual(["/staff/reports/transactions/export?remark=lite"]);
  });

  it("offers no export to an apprentice", async () => {
    mount([], ["staff.view_masked"]);
    await screen.findByText("PO00000003");
    expect(screen.queryByRole("button", { name: "Export CSV" })).toBeNull();
  });

  it("lists emails with their delivery", async () => {
    const asked: string[] = [];
    mount(asked, ["staff.view"]);
    await screen.findByText("PO00000003");
    await userEvent.click(screen.getByRole("tab", { name: "Email log" }));
    expect(await screen.findByText("Your payout wallet was added")).toBeInTheDocument();
    expect(asked).toContain("/staff/reports/emails?limit=200");
  });
});

describe("transaction filters", () => {
  it("turns dates into UTC bounds with the last day included", () => {
    expect(dayMs("2026-11-12")).toBe(Date.UTC(2026, 10, 12));
    expect(dayMs("12/11/2026")).toBeNull();
    const p = new URLSearchParams(txParams({ type: "payout", status: "paid", cif: " cif00000001 ", remark: "x", from: "2026-11-01", to: "2026-11-01" }));
    expect(Object.fromEntries(p)).toEqual({ type: "payout", status: "paid", cif: "CIF00000001", since: String(Date.UTC(2026, 10, 1)), until: String(Date.UTC(2026, 10, 2)) });
  });

  it("is offered to staff who read traders", () => {
    expect(screensFor(["staff.view"]).has("transactions")).toBe(true);
    expect(screensFor(["staff.view_masked"]).has("transactions")).toBe(true);
    expect(screensFor(["news.desk"]).has("transactions")).toBe(false);
  });
});
