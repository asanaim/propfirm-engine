import { render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { ClustersPage, evidenceText } from "./Clusters";
import { BackOfficeContext } from "./context";

const CLUSTER = {
  cluster: "CIF00000001",
  members: ["CIF00000001", "CIF00000002"],
  links: [
    { a_cif: "CIF00000001", b_cif: "CIF00000002", reason: "trades", evidence: { matches: 5, symbols: ["EUR/USD", "XAU/USD"] }, first_seen: 0, last_seen: 0 },
    { a_cif: "CIF00000001", b_cif: "CIF00000002", reason: "wallet", evidence: { address: "tr7nh" }, first_seen: 0, last_seen: 0 },
  ],
};

describe("identity clusters (D-14.27)", () => {
  it("says what each link rests on", () => {
    expect(evidenceText(CLUSTER.links[0] as never)).toBe("5 positions opened within 2 s of each other (EUR/USD, XAU/USD), last 30 days");
    expect(evidenceText(CLUSTER.links[1] as never)).toBe("address tr7nh");
    expect(evidenceText({ a_cif: "a", b_cif: "b", reason: "identity", evidence: {}, first_seen: 0, last_seen: 0 })).toBe("found at an identity check");
  });

  it("lists clusters with their members as one trader", async () => {
    const api: Api = {
      get: <T,>() => Promise.resolve([CLUSTER] as T),
      post: () => Promise.reject(new Error("no")),
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ClustersPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("Cluster CIF00000001")).toBeInTheDocument();
    expect(screen.getByText("2 customers, counted as one trader")).toBeInTheDocument();
    expect(screen.getByText("Matching trades")).toBeInTheDocument();
    expect(screen.getByText("Same payout wallet")).toBeInTheDocument();
  });
});
