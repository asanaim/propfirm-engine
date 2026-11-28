import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { type Campaign, NewsletterPage, progress } from "./Newsletter";

const BASE: Campaign = {
  id: 1,
  subject: "November news",
  body: "Two new sizes.",
  created_by: "admin-1",
  created_at: 0,
  state: "approved",
  decided_by: "admin-2",
  decided_at: 1,
  reason: null,
  audience: 10,
  sent: 7,
  failed: 1,
  skipped: 0,
};

describe("newsletter (D-15.2)", () => {
  it("says how far a campaign got", () => {
    expect(progress(BASE)).toBe("7 of 10 sent, sending (1 failed)");
    expect(progress({ ...BASE, sent: 9, failed: 0, skipped: 1 })).toBe("9 of 10 sent (1 left the list)");
    expect(progress({ ...BASE, state: "proposed" })).toBe("");
  });

  it("proposes a campaign and offers decisions on a proposal", async () => {
    const posted: [string, unknown][] = [];
    const api: Api = {
      get: <T,>() => Promise.resolve([{ ...BASE, id: 2, state: "proposed", decided_by: null, decided_at: null }] as T),
      post: <T,>(path: string, body: unknown) => {
        posted.push([path, body]);
        return Promise.resolve({} as T);
      },
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <NewsletterPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "December" } });
    fireEvent.change(screen.getByLabelText("Text"), { target: { value: "Winter offers start Monday." } });
    fireEvent.click(screen.getByRole("button", { name: "Propose" }));
    await waitFor(() => {
      expect(posted[0]).toEqual(["/staff/campaigns", { subject: "December", body: "Winter offers start Monday." }]);
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => {
      expect(posted[1]).toEqual(["/staff/campaigns/2/decision", { decision: "approve" }]);
    });
  });
});
