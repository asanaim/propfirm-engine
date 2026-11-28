import { fireEvent, screen, waitFor } from "@testing-library/react";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { linkToken, NewsletterForm, SubscriptionLinkPage } from "./Newsletter";

const WORDING = { on: true, wording: "Send me Pipridge news by email. One click unsubscribes.", version: "abcdef012345" };

describe("the newsletter (rule AC25)", () => {
  it("sends the wording's version with the address and asks to check the inbox", async () => {
    const fetcher = fakeFetch([
      ["GET", "/subscribe", 200, WORDING],
      ["POST", "/subscribers", 202, { status: "check_email" }],
    ]);
    renderWith(<NewsletterForm />, fakeAuth({ status: "anonymous" }), fetcher);
    fireEvent.change(await screen.findByLabelText(WORDING.wording), { target: { value: "ada@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    expect(await screen.findByText(/Check your inbox/)).toBeInTheDocument();
    const call = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ email: "ada@example.com", source: "footer", version: "abcdef012345" });
  });

  it("shows nothing when the newsletter is off", async () => {
    const fetcher = fakeFetch([["GET", "/subscribe", 200, { ...WORDING, on: false }]]);
    const { container } = renderWith(<NewsletterForm />, fakeAuth(), fetcher);
    await waitFor(() => {
      expect(fetcher).toHaveBeenCalled();
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("confirms by the emailed link", async () => {
    const fetcher = fakeFetch([["POST", "/subscribers/confirm", 200, { confirmed: true }]]);
    renderWith(<SubscriptionLinkPage action="confirm" token="tok-0123456789" />, fakeAuth({ status: "anonymous" }), fetcher);
    expect(await screen.findByText("You are subscribed. Thank you.")).toBeInTheDocument();
  });

  it("reads only a well formed token", () => {
    expect(linkToken("?token=abc_DEF-0123456789")).toBe("abc_DEF-0123456789");
    expect(linkToken("?token=<script>")).toBeNull();
    expect(linkToken("")).toBeNull();
  });
});
