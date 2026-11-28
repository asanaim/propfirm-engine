import { clean, install, scriptPolicy } from "./extensions";

const UUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0";

describe("extension slots (rule AC22)", () => {
  it("keeps only well formed slots of the expected provider", () => {
    expect(
      clean({
        chat: { provider: "crisp", public_id: UUID },
        captcha: { provider: "turnstile", public_id: "x\"><script>" },
        analytics: { provider: "crisp", public_id: "0".repeat(32) },
      }),
    ).toEqual({ chat: { provider: "crisp", public_id: UUID }, captcha: null, analytics: null });
    expect(clean(null)).toEqual({ chat: null, captcha: null, analytics: null });
  });

  it("loads chat and the analytics beacon once each", () => {
    const found = { chat: { provider: "crisp", public_id: UUID }, captcha: null, analytics: { provider: "cloudflare", public_id: "a".repeat(32) } };
    install(found);
    install(found);
    expect(document.querySelectorAll('script[src="https://client.crisp.chat/l.js"]')).toHaveLength(1);
    const beacon = document.querySelectorAll('script[src="https://static.cloudflareinsights.com/beacon.min.js"]');
    expect(beacon).toHaveLength(1);
    expect(beacon[0]?.getAttribute("data-cf-beacon")).toBe(JSON.stringify({ token: "a".repeat(32) }));
    expect(window.CRISP_WEBSITE_ID).toBe(UUID);
  });

  it("narrows the script policy to the slots switched on", () => {
    const none = { chat: null, captcha: null, analytics: null };
    expect(scriptPolicy(none)).toBe("script-src 'self'");
    expect(scriptPolicy({ ...none, captcha: { provider: "turnstile", public_id: "0x4AAAAAAAAAAAAAAA" } })).toBe(
      "script-src 'self' https://challenges.cloudflare.com",
    );
    const meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
    expect(meta?.getAttribute("content")).toBe("script-src 'self' https://client.crisp.chat https://settings.crisp.chat https://static.cloudflareinsights.com");
  });
});
