import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { fill, type NoticesView, NoticesPage, problems } from "./Notices";

const VIEW: NoticesView = {
  channels: ["email"],
  events: {
    wallet_changed: {
      label: "Payout wallet added or changed (security email)",
      variables: { full_name: "name", action: "added or changed", time: "when", new_wallet: "wallet", previous_wallet_line: "line" },
      required: ["action", "new_wallet", "time"],
      default: { subject: "Your payout wallet was {action}", body: "Hello {full_name}, {new_wallet} {action} {time}{previous_wallet_line}" },
      sample: { full_name: "Ada Trader", action: "changed", time: "2026-11-09 14:05 UTC", new_wallet: "T9y", previous_wallet_line: "" },
    },
  },
  templates: { wallet_changed: { subject: "Your payout wallet was {action}", body: "Hello {full_name}, {new_wallet} {action} {time}{previous_wallet_line}", approved: false } },
  wrapper: { email: "{body}" },
  default_wrapper: "{body}",
  limits: { subject: 120, body: 4000, wrapper: 2000 },
};

describe("notification templates (rule AC12)", () => {
  it("fills variables and flags what the engine would refuse", () => {
    expect(fill("Hi {full_name} {x}", { full_name: "Ada" })).toBe("Hi Ada {x}");
    expect(problems("{new_wallet} {time}", ["new_wallet", "time", "action"], ["new_wallet", "action"])).toEqual(["must include {action}"]);
    expect(problems("{pw} {", ["a"], [])).toEqual(["unknown variable {pw}", "braces are only for variables"]);
  });

  it("previews with sample values and proposes new wording with a reason", async () => {
    const put = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}));
    const api: Api = {
      get: <T,>() => Promise.resolve(VIEW as T),
      post: () => Promise.reject(new Error("no")),
      put: <T,>(path: string, body?: unknown) => put(path, body) as Promise<T>,
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <NoticesPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    expect(await screen.findByText("Built-in wording")).toBeInTheDocument();
    expect(screen.getByText("Your payout wallet was changed")).toBeInTheDocument();
    const subject = screen.getByLabelText("Subject");
    await userEvent.clear(subject);
    await userEvent.type(subject, "Security: wallet {{action}");
    await userEvent.type(screen.getAllByLabelText("Reason (kept with the record)")[1] as HTMLElement, "clearer subject");
    const propose = screen.getAllByRole("button", { name: "Propose" })[1];
    if (propose === undefined) throw new Error("no propose button");
    await userEvent.click(propose);
    expect(put).toHaveBeenCalledWith("/admin/notices/wallet_changed", {
      subject: "Security: wallet {action}",
      body: VIEW.templates.wallet_changed?.body,
      reason: "clearer subject",
    });
  });

  it("will not propose a body without a required variable", async () => {
    const api: Api = { get: <T,>() => Promise.resolve(VIEW as T), post: () => Promise.reject(new Error("no")), put: () => Promise.reject(new Error("no")) };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <NoticesPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    const body = await screen.findByLabelText("Body");
    await userEvent.clear(body);
    await userEvent.type(body, "Hello");
    expect(screen.getByText(/must include \{new_wallet\}/)).toBeInTheDocument();
  });
});
