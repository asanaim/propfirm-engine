import { fireEvent, render, screen } from "@testing-library/react";
import { AdminContext, testDataProvider } from "react-admin";
import type { UserManager } from "oidc-client-ts";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { CouponsPage, newCode, type Code } from "./Coupons";

const CODE: Code = { code: "LAUNCH10", discount_percent: "10", on: true, valid: true, created_at: 0, created_by: "admin-1", expires_at: null, max_uses: 100, uses: 3, paid: 2, given_cents: 2_180 };

function mount(post: (path: string, body: unknown) => void) {
  const api: Api = {
    get: <T,>() => Promise.resolve([CODE] as T),
    post: <T,>(path: string, body?: unknown) => {
      post(path, body);
      return Promise.resolve({} as T);
    },
    put: () => Promise.reject(new Error("no")),
  };
  render(
    <AdminContext dataProvider={testDataProvider()}>
      <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
        <CouponsPage />
      </BackOfficeContext.Provider>
    </AdminContext>,
  );
}

describe("coupons", () => {
  it("builds a new code, leaving out what is not set", () => {
    expect(newCode(" launch10 ", "10", "", "", " go ")).toEqual({ code: "LAUNCH10", discount_percent: "10", reason: "go" });
    const full = newCode("X-1", "5", "2026-12-01", "50", "r");
    expect(full.max_uses).toBe(50);
    expect(full.expires_at).toBe(new Date("2026-12-01T00:00:00").getTime());
  });

  it("lists codes with uses and switches one with a reason", async () => {
    const post = vi.fn();
    mount(post);
    expect(await screen.findByText("LAUNCH10")).toBeInTheDocument();
    expect(screen.getByText("3 / 100")).toBeInTheDocument();
    expect(screen.getByText("$21.80")).toBeInTheDocument();
    const toggle = screen.getByRole("switch", { name: "LAUNCH10 on" });
    expect(toggle).toBeDisabled(); // no reason yet
    fireEvent.change(screen.getByLabelText("Reason for a switch"), { target: { value: "paused" } });
    fireEvent.click(toggle);
    expect(post).toHaveBeenCalledWith("/staff/codes/LAUNCH10/switch", { on: false, reason: "paused" });
  });
});

describe("referrals", () => {
  it("offers a clawback only on commission not yet taken back", async () => {
    const { clawable } = await import("./Referrals");
    const e = (ord: string, kind: "accrued" | "clawback") => ({ id: 1, referrer_cif: "C1", referred_cif: "C2", ord, kind, amount_cents: kind === "accrued" ? 550 : -550, available_at: 0, by_login: null, reason: null, ts: 0 });
    expect([...clawable([e("ORD1", "accrued"), e("ORD2", "accrued"), e("ORD1", "clawback")])]).toEqual(["ORD2"]);
  });
});

describe("books", () => {
  it("reads typed dollars as cents and shows balances the normal way round", async () => {
    const { cents, shown } = await import("./Books");
    expect(cents("29.99")).toBe(2_999);
    expect(cents("12.5")).toBe(1_250);
    expect(cents("7")).toBe(700);
    expect(cents("0")).toBeNull();
    expect(cents("1.234")).toBeNull();
    expect(cents("-3")).toBeNull();
    expect(shown({ account: "income:fees:lite", type: "income", name: "Fees", balance_cents: -12_535 })).toBe(12_535);
    expect(shown({ account: "asset:cash", type: "asset", name: "Cash", balance_cents: 8_446 })).toBe(8_446);
  });
});
