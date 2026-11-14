import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { type SiteEditor, SiteEditorPage } from "./Site";

const EDITOR: SiteEditor = {
  shapes: {
    maintenance: { fields: { title: 80, message: 600, image: 300 }, items: {}, max_items: 0, optional: ["image"], images: ["image"] },
  },
  sections: {},
  tokens: {},
  mode: { maintenance: false, registration_open: true },
};

describe("maintenance and registration in the website editor (rule AC17)", () => {
  it("proposes a change of mode with a reason, never the mode in force", async () => {
    const put = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    const api: Api = {
      get: <T,>() => Promise.resolve(EDITOR as T),
      post: () => Promise.reject(new Error("no")),
      put: <T,>(path: string, body?: unknown) => put(path, body) as Promise<T>,
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <SiteEditorPage start="maintenance" />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("Maintenance page")).toBeInTheDocument();
    expect(screen.getByText(/the path of an image on the site/)).toBeInTheDocument();
    const propose = screen.getAllByRole("button", { name: "Propose" })[0];
    if (propose === undefined) throw new Error("no propose button");
    await userEvent.click(screen.getByLabelText("Maintenance mode"));
    expect(propose).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getAllByLabelText("Reason (kept with the record)")[0] as HTMLElement, "database upgrade");
    await userEvent.click(propose);
    expect(put).toHaveBeenCalledWith("/admin/site-mode", { maintenance: true, registration_open: true, reason: "database upgrade" });
  });
});

describe("the website screen's parts", () => {
  it("opens the part a link names, content by default", async () => {
    const { partOf } = await import("./Site");
    expect(partOf("seo")).toBe("seo");
    expect(partOf("maintenance")).toBe("maintenance");
    expect(partOf(null)).toBe("content");
    expect(partOf("nonsense")).toBe("content");
  });
  it("links each settings card to its own part", async () => {
    const { AREAS } = await import("./places");
    const path = (key: string) => AREAS.find((a) => a.key === key)?.path;
    expect([path("seo"), path("kyc"), path("maintenance"), path("website")]).toEqual(["/site?part=seo", "/site?part=kyc", "/site?part=maintenance", "/site"]);
  });
});
