import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { ReceiptsPage, todayUtc } from "./Receipts";

const LISTING = {
  awaiting: [{ ord: "ORD00000002", cif: "CIF00000002", provider: "bank", price_cents: 10_900, created_at: 0 }],
  receipts: [{ rcp: "RCP00000001", ord: "ORD00000001", cif: "CIF00000001", provider: "bank", amount_cents: 10_900, reference: "BANK-77", received_at: 0, recorded_by: "admin-1", status: "recorded", decided_by: null }],
};

describe("payments by hand (D-14.24)", () => {
  it("records the exact price with the bank's reference, and confirms only with a reason", async () => {
    expect(todayUtc(Date.UTC(2026, 10, 20, 23, 59))).toBe("2026-11-20");
    const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    const api: Api = {
      get: <T,>() => Promise.resolve(LISTING as T),
      post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ReceiptsPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    const record = await screen.findByRole("button", { name: "Record $109.00" });
    expect(record).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reference for ORD00000002"), "BANK-78");
    await userEvent.click(record);
    const [path, body] = post.mock.calls[0] ?? [];
    expect(path).toBe("/staff/orders/ORD00000002/receipts");
    expect(body).toMatchObject({ amount_cents: 10_900, reference: "BANK-78" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason for a decision"), "on the statement");
    await userEvent.click(confirm);
    expect(post).toHaveBeenLastCalledWith("/staff/receipts/RCP00000001/decision", { confirm: true, reason: "on the statement" });
  });
});
