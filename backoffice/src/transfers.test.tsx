import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { TransfersPage } from "./Transfers";

const WAITING = [{ po: "PO00000001", acc: "ACC00000001", cif: "CIF00000001", method_id: "usdt-hand", trader_amount_cents: 80_000, charge_cents: 900, net_cents: 79_100, currency: "USDT", network: "TRC20", address: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" }];

describe("payouts sent by hand (D-14.25)", () => {
  it("shows the net amount and address, and records the transaction", async () => {
    const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    const api: Api = {
      get: <T,>() => Promise.resolve(WAITING as T),
      post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <TransfersPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("$791.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("$800.00 less $9.00 charge")).toBeInTheDocument();
    const record = screen.getByRole("button", { name: "Record" });
    expect(record).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Transaction for PO00000001"), "a1b2c3");
    await userEvent.click(record);
    expect(post).toHaveBeenCalledWith("/staff/payouts/PO00000001/transfer", { tx_ref: "a1b2c3" });
  });
});
