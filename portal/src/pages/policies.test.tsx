import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { PolicyGate, PolicyPage } from "./PolicyPages";

const TERMS = { slug: "terms", version: 2, title: "Terms of Service", published_at: Date.UTC(2026, 10, 8) };

describe("policy pages (rule AC10)", () => {
  it("shows a page as plain text, never as HTML", async () => {
    const fetcher = fakeFetch([["GET", "/policies/terms", 200, { ...TERMS, body: "1. <b>Simulated</b> only." }]]);
    renderWith(<PolicyPage slug="terms" />, fakeAuth(), fetcher);
    expect(await screen.findByRole("heading", { name: "Terms of Service" })).toBeInTheDocument();
    expect(screen.getByText("1. <b>Simulated</b> only.")).toBeInTheDocument();
    expect(screen.getByText(/Version 2/)).toBeInTheDocument();
  });

  it("asks a signed-in trader to accept each updated policy, then goes away", async () => {
    let accepted = false;
    const base = fakeFetch([["POST", "/me/policies/terms/accept", 200, { ...TERMS, accepted: true }]]);
    const fetcher = vi.fn<typeof fetch>((input, init) => {
      const path = (input as string).replace(/^\/api/, "");
      if (path === "/me/policies/pending") {
        return Promise.resolve(new Response(JSON.stringify(accepted ? [] : [TERMS]), { status: 200 }));
      }
      accepted = true;
      return base(input, init);
    });
    renderWith(<PolicyGate />, fakeAuth(), fetcher);
    expect(await screen.findByRole("link", { name: "Terms of Service (version 2)" })).toHaveAttribute(
      "href",
      "/legal/terms",
    );
    await userEvent.click(screen.getByRole("button", { name: /accept these policies/ }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Updated policies" })).not.toBeInTheDocument();
    });
    expect(fetcher).toHaveBeenCalledWith("/api/me/policies/terms/accept", expect.objectContaining({ method: "POST" }));
  });

  it("asks nothing when signed out", () => {
    const fetcher = fakeFetch([]);
    renderWith(<PolicyGate />, fakeAuth({ status: "anonymous", session: null }), fetcher);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
