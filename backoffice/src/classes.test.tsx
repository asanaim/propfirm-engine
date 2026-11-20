import { render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { ClassesPage, reasonText } from "./Classes";
import { BackOfficeContext } from "./context";

const LIST = {
  counts: { A: 0, B: 1, C: 1, D: 0 },
  accounts: [
    { acc: "ACC00000001", cif: "CIF00000001", class: "C", reason: 'daily: short_holds,trades_cluster; {"trades": 1}', set_by: "system", at: 0 },
    { acc: "ACC00000002", cif: "CIF00000002", class: "B", reason: "reviewed: a shared signal group", set_by: "admin-1", at: 0 },
  ],
};

describe("trader classes (D-14.28)", () => {
  it("says why in words", () => {
    expect(reasonText({ reason: LIST.accounts[0]?.reason ?? "" })).toBe("most profit from short holds, trades match another customer");
    expect(reasonText({ reason: LIST.accounts[1]?.reason ?? "" })).toBe("reviewed: a shared signal group");
    expect(reasonText({ reason: "daily: standard; {}" })).toBe("standard");
  });

  it("lists classes in force with counts", async () => {
    const api: Api = {
      get: <T,>() => Promise.resolve(LIST as T),
      post: () => Promise.reject(new Error("no")),
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ClassesPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("C Watch: 1")).toBeInTheDocument();
    expect(screen.getByText("ACC00000001")).toBeInTheDocument();
    expect(screen.getByText("Daily job or payout check")).toBeInTheDocument();
    expect(screen.getByText("admin-1")).toBeInTheDocument();
  });
});
