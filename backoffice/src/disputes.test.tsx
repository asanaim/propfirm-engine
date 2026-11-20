import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { DisputesPage, feeCents, orderId } from "./Disputes";

const REFUND = { rfd: "RFD00000001", ord: "ORD00000001", cif: "CIF00000001", product_id: "lite", price_cents: 5_500, amount_cents: 1_000, reason: "goodwill", proposed_by: "admin-1", proposed_at: 0, status: "proposed", decided_by: null, ended_acc: null };
const CHARGE = { cbk: "CBK00000001", ord: "ORD00000002", cif: "CIF00000002", product_id: "lite", amount_cents: 5_500, fee_cents: 1_500, provider_case: "d-42", reason: "dispute", received_at: Date.UTC(2026, 10, 20), recorded_by: "admin-1", status: "open", cas: "CAS00000001", ended_acc: "ACC00000002" };

function page(post: (path: string, body: unknown) => Promise<unknown>) {
  const api: Api = {
    get: <T,>(path: string) => Promise.resolve((path === "/staff/refunds" ? [REFUND] : [CHARGE]) as T),
    post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
    put: () => Promise.reject(new Error("no")),
  };
  render(
    <AdminContext dataProvider={testDataProvider()}>
      <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
        <DisputesPage />
      </BackOfficeContext.Provider>
    </AdminContext>,
  );
}

describe("refunds and chargebacks (D-14.22)", () => {
  it("reads order ids and fees exactly", () => {
    expect(orderId(" ord00000001 ")).toBe("ORD00000001");
    expect(orderId("ORD1")).toBeNull();
    expect(feeCents("")).toBe(0);
    expect(feeCents("0")).toBe(0);
    expect(feeCents("15.5")).toBe(1_550);
    expect(feeCents("1.555")).toBeNull();
  });

  it("decides only with a reason, and records a chargeback in UTC", async () => {
    const post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    page(post);
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "Won" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason for a decision"), "checked");
    await userEvent.click(approve);
    expect(post).toHaveBeenCalledWith("/staff/refunds/RFD00000001/decision", { approve: true, reason: "checked" });
    await userEvent.click(screen.getByRole("button", { name: "Won" }));
    expect(post).toHaveBeenCalledWith("/staff/chargebacks/CBK00000001/decision", { outcome: "won", reason: "checked" });

    const record = screen.getByRole("button", { name: "Record" });
    expect(record).toBeDisabled();
    const orders = screen.getAllByLabelText("Order");
    await userEvent.type(orders[1] as HTMLElement, "ORD00000003");
    await userEvent.type(screen.getByLabelText("Processor case"), "d-43");
    await userEvent.type(screen.getAllByLabelText("Amount (USD)")[1] as HTMLElement, "55");
    await userEvent.type(screen.getByLabelText("Processor fee (USD)"), "15");
    await userEvent.type(screen.getByLabelText("Taken on (UTC)"), "2026-11-20");
    await userEvent.type(screen.getAllByLabelText("Reason")[1] as HTMLElement, "cardholder");
    await userEvent.click(record);
    expect(post).toHaveBeenCalledWith("/staff/orders/ORD00000003/chargebacks", {
      amount_cents: 5_500,
      fee_cents: 1_500,
      provider_case: "d-43",
      reason: "cardholder",
      received_at: Date.UTC(2026, 10, 20),
    });
  });
});
