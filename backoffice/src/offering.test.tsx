import { fireEvent, render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { OfferingEditor, proposal, sizeLabel, type OfferingView } from "./Offering";
import { screensFor } from "./screens";

const VIEW: OfferingView = {
  products: [
    { id: "lite", name: "Lite", on: true, instant: false, sizes: [{ size_cents: 1_000_000, price_cents: 4_400, on: true }] },
    { id: "classic", name: "Classic", on: false, instant: false, sizes: [{ size_cents: 1_000_000, price_cents: 5_400, on: true }] },
    { id: "instant", name: "Instant", on: true, instant: true, sizes: [{ size_cents: 500_000, price_cents: 10_900, on: true }] },
  ],
  sizes: [
    { size_cents: 500_000, on: true },
    { size_cents: 1_000_000, on: true },
  ],
  allocation_cap_cents: 40_000_000,
  one_instant_funding_per_trader: true,
  unpaid_hold_hours: 24,
};

describe("products and sizes (review D4)", () => {
  it("labels sizes and proposes what is switched off", () => {
    expect(sizeLabel(1_000_000)).toBe("10K");
    expect(sizeLabel(40_000_000)).toBe("400K");
    expect(proposal({ lite: true, classic: false }, { 500_000: false, 1_000_000: true }, "no 5K")).toEqual({
      products_off: ["classic"],
      sizes_off: [500_000],
      reason: "no 5K",
    });
  });

  it("shows the purchase rules and sends a proposal for a size switched off", async () => {
    const put = vi.fn();
    const api: Api = {
      get: () => Promise.reject(new Error("no")),
      post: () => Promise.reject(new Error("no")),
      put: <T,>(path: string, body?: unknown) => {
        put(path, body);
        return Promise.resolve({} as T);
      },
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <OfferingEditor view={VIEW} onDone={() => undefined} />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(screen.getByText("Allocation cap: 400,000.00 USD per trader")).toBeInTheDocument();
    expect(screen.getByText(/400K: no product has a 400K price yet/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Propose the change" });
    expect(button).toBeDisabled(); // nothing changed yet
    fireEvent.click(screen.getByRole("switch", { name: "5K" }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "no 5K accounts" } });
    fireEvent.click(button);
    expect(put).toHaveBeenCalledWith("/admin/offering", { products_off: ["classic"], sizes_off: [500_000], reason: "no 5K accounts" });
    expect(await screen.findByText(/A second admin or owner approves it/)).toBeInTheDocument();
  });

  it("switches an add-on off", () => {
    const put = vi.fn();
    const api: Api = {
      get: () => Promise.reject(new Error("no")),
      post: () => Promise.reject(new Error("no")),
      put: <T,>(path: string, body?: unknown) => {
        put(path, body);
        return Promise.resolve({} as T);
      },
    };
    const view: OfferingView = {
      ...VIEW,
      addons: [
        { id: "split90", price_percent: "20", products: ["lite"], on: true },
        { id: "swing", price_percent: "15", products: ["lite"], on: true },
      ],
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <OfferingEditor view={view} onDone={() => undefined} />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    fireEvent.click(screen.getByRole("switch", { name: /Swing: hold over news and weekends.*\(\+15%\)/ }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "pause Swing" } });
    fireEvent.click(screen.getByRole("button", { name: "Propose the change" }));
    expect(put).toHaveBeenCalledWith("/admin/offering", { products_off: ["classic"], sizes_off: [], addons_off: ["swing"], reason: "pause Swing" });
  });

  it("is offered to logins that change settings", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("offering")).toBe(true);
    expect(screensFor(["staff.view"]).has("offering")).toBe(false);
  });
});
