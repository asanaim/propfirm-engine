import { render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { type Consent, SubscribersPage, toCsv } from "./Subscribers";

const ROWS: Consent[] = [
  { email: "ada@example.com", status: "confirmed", subscribed_at: 1, source: "footer", wording: "Send me news", wording_version: "abcdef012345", country: "BD", confirmed_at: 2, unsubscribed_at: null },
  { email: "bo,b@example.com", status: "confirmed", subscribed_at: 3, source: "home", wording: "Send me news", wording_version: "abcdef012345", country: null, confirmed_at: 4, unsubscribed_at: null },
  { email: "cy@example.com", status: "pending", subscribed_at: 5, source: "footer", wording: "Send me news", wording_version: "abcdef012345", country: null, confirmed_at: null, unsubscribed_at: null },
];

describe("subscribers (rule AC25)", () => {
  it("exports only confirmed addresses, quoted where needed", () => {
    expect(toCsv(ROWS)).toBe(
      "email,subscribed_at,confirmed_at,source,wording_version,country\nada@example.com,1,2,footer,abcdef012345,BD\n\"bo,b@example.com\",3,4,home,abcdef012345,\n",
    );
  });

  it("lists consents", async () => {
    const asked: string[] = [];
    const api: Api = {
      get: <T,>(path: string) => {
        asked.push(path);
        return Promise.resolve(ROWS as T);
      },
      post: () => Promise.reject(new Error("no")),
      put: () => Promise.reject(new Error("no")),
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <SubscribersPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
    expect(asked).toEqual(["/staff/subscribers?status=confirmed"]);
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
  });
});
