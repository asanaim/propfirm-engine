import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { UserManager } from "oidc-client-ts";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { ExtensionsPage, type Slots, warning } from "./Extensions";

const CHAT = { provider: "crisp", enabled: false, public_id: "", secret: null };
const CAPTCHA = { provider: "turnstile", enabled: false, public_id: "", secret: false };
const SLOTS: Slots = { chat: CHAT, captcha: CAPTCHA, analytics: { provider: "cloudflare", enabled: false, public_id: "", secret: null } };

function nth<T>(found: T[], i: number): T {
  const el = found[i];
  if (el === undefined) throw new Error(`no element ${String(i)}`);
  return el;
}

describe("extensions (rule AC22)", () => {
  it("asks for the captcha's secret before it is switched on", () => {
    expect(warning(CAPTCHA)).toMatch(/secret key first/);
    expect(warning({ ...CAPTCHA, secret: true })).toBeNull();
    expect(warning(CHAT)).toBeNull();
  });

  it("proposes a slot change and a secret", async () => {
    const put: [string, unknown][] = [];
    const api: Api = {
      get: <T,>() => Promise.resolve(SLOTS as T),
      post: () => Promise.reject(new Error("no")),
      put: <T,>(path: string, body: unknown) => {
        put.push([path, body]);
        return Promise.resolve({} as T);
      },
    };
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
          <ExtensionsPage />
        </BackOfficeContext.Provider>
      </AdminContext>,
    );
    const ids = await screen.findAllByLabelText("Crisp website ID");
    fireEvent.change(nth(ids, 0), { target: { value: "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0" } });
    fireEvent.click(nth(screen.getAllByRole("switch"), 0));
    const reasons = screen.getAllByLabelText("Reason");
    fireEvent.change(nth(reasons, 0), { target: { value: "support chat" } });
    fireEvent.click(nth(screen.getAllByRole("button", { name: "Propose change" }), 0));
    await waitFor(() => {
      expect(put[0]).toEqual(["/admin/extensions/chat", { enabled: true, public_id: "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", reason: "support chat" }]);
    });
    fireEvent.change(screen.getByLabelText("Secret key (not set)"), { target: { value: "0x4AAAsecret" } });
    fireEvent.change(nth(screen.getAllByLabelText("Reason"), 1), { target: { value: "turnstile key" } });
    fireEvent.click(screen.getByRole("button", { name: "Propose secret" }));
    await waitFor(() => {
      expect(put[1]).toEqual(["/admin/extensions/captcha/secret", { value: "0x4AAAsecret", reason: "turnstile key" }]);
    });
  });
});
