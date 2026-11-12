import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { screensFor } from "./screens";
import { SEGMENTS, TradersPage, cell, listPath, type TraderFile, type TraderList, type TraderRow } from "./Traders";

const ADA: TraderRow = {
  cif: "CIF00000001",
  full_name: "Ada Trader",
  email: "ada@example.com",
  residence_country: "GB",
  status: "active",
  created_at: Date.UTC(2026, 10, 1, 9, 30),
  accounts: 2,
  funded: 1,
  onboarding: "approved",
  open_case: "CAS00000001",
  paid_cents: 123456,
};
const COUNTS = { all: 2, trading: 1, funded: 1, no_account: 1, kyc_pending: 0, kyc_declined: 0, open_case: 1, blocked: 0 };
const FILE: TraderFile = {
  trader: ADA,
  accounts: [{ acc: "ACC00000001", stage: "funded", size_cents: 1000000, created_at: Date.UTC(2026, 10, 2) }],
  orders: [],
  onboardings: [],
  cases: [{ cas: "CAS00000001", status: "open" }],
  payouts: [],
  notifications: [],
  events: [{ kind: "registered", ts: Date.UTC(2026, 10, 1) }],
};

function mount(entry: string, asked: string[]) {
  const api: Api = {
    get: <T,>(path: string) => {
      asked.push(path);
      const body: TraderList | TraderFile = path.startsWith("/staff/traders/") ? FILE : { counts: COUNTS, traders: [ADA] };
      return Promise.resolve(body as T);
    },
    post: () => Promise.reject(new Error("read only")),
    put: () => Promise.reject(new Error("read only")),
  };
  render(
    <MemoryRouter initialEntries={[entry]}>
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <TradersPage />
        </BackOfficeContext.Provider>
      </AdminContext>
    </MemoryRouter>,
  );
}

describe("the trader lists (rule AC19)", () => {
  it("shows every segment with its count, and the traders in it", async () => {
    const asked: string[] = [];
    mount("/traders", asked);
    expect(await screen.findByText("Ada Trader")).toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "Trader segments" });
    expect(within(tabs).getAllByRole("tab")).toHaveLength(SEGMENTS.length);
    expect(within(tabs).getByRole("tab", { name: "Open case (1)" })).toBeInTheDocument();
    expect(screen.getByText("1,234.56 USD")).toBeInTheDocument();
    expect(screen.getByText("2 (1 funded)")).toBeInTheDocument();
    expect(asked[0]).toBe("/staff/traders?segment=all&limit=200");
  });

  it("asks for the chosen segment and the typed search", async () => {
    const asked: string[] = [];
    mount("/traders", asked);
    await screen.findByText("Ada Trader");
    await userEvent.click(screen.getByRole("tab", { name: "Funded (1)" }));
    await waitFor(() => {
      expect(asked).toContain("/staff/traders?segment=funded&limit=200");
    });
    await userEvent.type(screen.getByLabelText("Search"), "ada");
    await waitFor(() => {
      expect(asked).toContain("/staff/traders?segment=funded&limit=200&q=ada");
    });
  });

  it("opens one trader's file with each section", async () => {
    const asked: string[] = [];
    mount("/traders?cif=CIF00000001", asked);
    expect(await screen.findByText("Ada Trader · CIF00000001")).toBeInTheDocument();
    expect(asked).toEqual(["/staff/traders/CIF00000001"]);
    for (const title of ["Accounts (1)", "Purchases (0)", "Cases (1)", "Emails sent (0)", "Registration events (1)"]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
    expect(screen.getByText("10,000.00 USD")).toBeInTheDocument();
    expect(screen.getByText("Nothing bought.")).toBeInTheDocument();
  });

  it("does not ask the engine for a malformed CIF", async () => {
    const asked: string[] = [];
    mount("/traders?cif=../admin", asked);
    await screen.findByText("Ada Trader");
    expect(asked.every((p) => p.startsWith("/staff/traders?"))).toBe(true);
  });
});

describe("trader list helpers", () => {
  it("sends a search only from 2 characters and at most 80", () => {
    expect(listPath("all", " a ")).toBe("/staff/traders?segment=all&limit=200");
    expect(listPath("blocked", "x".repeat(90))).toBe(`/staff/traders?segment=blocked&limit=200&q=${"x".repeat(80)}`);
  });

  it("shows cents as money and epoch fields as UTC time", () => {
    expect(cell("size_cents", 500)).toBe("5.00 USD");
    expect(cell("ts", Date.UTC(2026, 10, 12, 8, 5))).toBe("2026-11-12 08:05 UTC");
    expect(cell("kyc_attempts", 2)).toBe("2");
    expect(cell("closed_at", null)).toBe("-");
  });

  it("is offered to staff who read traders, masked or not", () => {
    expect(screensFor(["staff.view"]).has("traders")).toBe(true);
    expect(screensFor(["staff.view_masked"]).has("traders")).toBe(true);
    expect(screensFor(["news.desk"]).has("traders")).toBe(false);
  });
});
