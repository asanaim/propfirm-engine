import { render, screen } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { GeneralPage } from "./SettingsHub";

describe("the General page (rule AC05)", () => {
  it("lists the site name and the engine's basics, each with why it is fixed", async () => {
    const basics = [{ name: "Base currency", value: "US dollar (USD)", explains: "In US cents.", why_fixed: "Every record is in US cents." }];
    const api: Api = { get: <T,>() => Promise.resolve(basics as T), post: () => Promise.reject(new Error("no")), put: () => Promise.reject(new Error("no")) };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <GeneralPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("US dollar (USD)")).toBeInTheDocument();
    expect(screen.getByText("Site name")).toBeInTheDocument();
    expect(screen.getByText("pipridge")).toBeInTheDocument();
    expect(screen.getByText("Every record is in US cents.")).toBeInTheDocument();
  });
});
