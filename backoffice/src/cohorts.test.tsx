import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { type CohortDesk, CohortsPage, cohortName } from "./Cohorts";
import { BackOfficeContext } from "./context";

const DESK: CohortDesk = {
  significance: "1.0 %",
  min_resolved: 10,
  cohorts: [
    { kind: "product", key: "lite:2026-11", product_id: "lite", resolved: 40, passed: 20, rate: "50.0 %", bound: "25.0 %", above: true, alarmed: true },
    { kind: "affiliate", key: "CIF00000007:lite", product_id: "lite", resolved: 12, passed: 4, rate: "33.3 %", bound: "25.0 %", above: true, alarmed: false },
  ],
  alarms: [{ id: 3, kind: "product", key: "lite:2026-11", product_id: "lite", resolved: 40, passed: 20, bound: "1/4", raised_at: 0, cleared_at: null, cleared_by: null, reason: null }],
};

describe("cohort alarm (D-14.33)", () => {
  it("names a cohort in words", () => {
    expect(cohortName({ kind: "product", key: "lite:2026-11" })).toBe("lite, bought in 2026-11");
    expect(cohortName({ kind: "affiliate", key: "CIF00000007:lite" })).toBe("affiliate CIF00000007, lite");
  });

  it("clears an alarm with a reason", async () => {
    const posted: [string, unknown][] = [];
    const api: Api = {
      get: <T,>() => Promise.resolve(DESK as T),
      post: <T,>(path: string, body: unknown) => {
        posted.push([path, body]);
        return Promise.resolve({} as T);
      },
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <CohortsPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("Alarm")).toBeInTheDocument();
    expect(screen.getByText("Above, not significant")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Clear" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason for clearing"), { target: { value: "reviewed" } });
    fireEvent.click(button);
    await waitFor(() => {
      expect(posted).toEqual([["/staff/cohorts/3/clear", { reason: "reviewed" }]]);
    });
  });
});
