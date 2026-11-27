import { render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { ExposurePage, type ExposureDesk, ratioValue, tone } from "./Exposure";

const DESK: ExposureDesk = {
  as_of: 0,
  free_reserve_cents: 10_000_000,
  accounts: 3,
  unread: [],
  groups: [
    { kind: "symbol", name: "EUR/USD", scenario: "EUR/USD +3 sd", loss_cents: 2_600_000, limit_cents: 2_500_000, ratio: "104.0 %", level: "halve" },
    { kind: "bloc", name: "USD", scenario: "USD down", loss_cents: 4_000_000, limit_cents: 5_000_000, ratio: "80.0 %", level: "none" },
  ],
  cuts: [{ symbol: "EUR/USD", side: "buy", level: "halve", classes: ["B", "C"], since: 0, under_since: null }],
  moves: [{ seq: 1, at: 0, symbol: "EUR/USD", side: "buy", event: "raise", level: "halve", ratio: "104.0 %", scenario: "EUR/USD +3 sd", policy_version: 7 }],
};

describe("exposure desk (D-14.32)", () => {
  it("colours by the share of the limit", () => {
    expect(ratioValue("80.4 %")).toBe(80.4);
    expect(ratioValue(null)).toBeNull();
    expect(tone("79.9 %")).toBe("default");
    expect(tone("80.0 %")).toBe("warning");
    expect(tone("150.0 %")).toBe("error");
  });

  it("shows groups, cuts and the record", async () => {
    const api: Api = {
      get: <T,>() => Promise.resolve(DESK as T),
      post: () => Promise.reject(new Error("no")),
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ExposurePage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("104.0 % · Lot caps halved")).toBeInTheDocument();
    expect(screen.getByText("USD bloc")).toBeInTheDocument();
    expect(screen.getByText("B, C")).toBeInTheDocument();
    expect(screen.getByText("v7")).toBeInTheDocument();
  });
});
