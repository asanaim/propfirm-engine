import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { type ErasureRow, ErasuresPage, summary } from "./Erasures";

const ROW: ErasureRow = {
  id: 4,
  cif: "CIF00000007",
  reason: "erasure request by email",
  proposed_by: "owner-a",
  proposed_at: 1_793_491_200_000,
  status: "proposed",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
  erased: null,
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("data erasure (rule AC27)", () => {
  it("says what was overwritten and that the login goes by hand", () => {
    expect(summary({ cif: 1, wallets: 0, sign_ins: 2, identity_login: 0 })).toBe("cif 1, sign ins 2. Remove the login in the identity provider by hand.");
  });

  it("proposes, then approves with a reason", async () => {
    const posted: [string, unknown][] = [];
    const api: Api = {
      get: <T,>() => Promise.resolve([ROW] as T),
      post: <T,>(path: string, body: unknown) => {
        posted.push([path, body]);
        return Promise.resolve({} as T);
      },
      put: () => Promise.reject(new Error("no")),
    };
    vi.spyOn(window, "prompt").mockReturnValue("retention ended");
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ErasuresPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    fireEvent.change(screen.getByLabelText("Customer (CIF)"), { target: { value: "cif00000007" } });
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "asked by email" } });
    fireEvent.click(screen.getByRole("button", { name: "Propose" }));
    await waitFor(() => {
      expect(posted[0]).toEqual(["/admin/erasures", { cif: "CIF00000007", reason: "asked by email" }]);
    });
    fireEvent.click(await screen.findByRole("button", { name: "Approve and erase" }));
    await waitFor(() => {
      expect(posted[1]).toEqual(["/admin/erasures/4/decision", { decision: "approve", reason: "retention ended" }]);
    });
  });
});
