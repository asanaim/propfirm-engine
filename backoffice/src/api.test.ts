import { ApiError, createApi, isStepUp, StepUpRequired } from "./api";

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn<typeof fetch>(() => Promise.resolve(new Response(body === null ? "" : JSON.stringify(body), { status, headers })));
}

describe("createApi", () => {
  it("sends the bearer token and JSON body", async () => {
    const fetcher = reply(200, { ok: true });
    const api = createApi("/api", () => Promise.resolve("tok"), fetcher);
    expect(await api.post("/staff/x", { reason: "r" })).toEqual({ ok: true });
    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("/api/staff/x");
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok");
    expect(init?.body).toBe('{"reason":"r"}');
  });

  it("keeps the API's code, message and permission", async () => {
    const body = { detail: { code: "forbidden", message: "needs payout.pass", permission: "payout.pass" } };
    const api = createApi("/api", () => Promise.resolve(null), reply(403, body));
    const err = await api.get("/staff/payouts").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, code: "forbidden", message: "needs payout.pass", permission: "payout.pass" });
  });

  it("turns an RFC 9470 challenge into StepUpRequired", async () => {
    const challenge = 'Bearer error="insufficient_user_authentication", max_age=300';
    const api = createApi("/api", () => Promise.resolve("t"), reply(401, { detail: { code: "mfa_required", message: "again" } }, { "WWW-Authenticate": challenge }));
    await expect(api.post("/staff/payouts/PO00000001/approve", { reason: "ok" })).rejects.toBeInstanceOf(StepUpRequired);
  });

  it("a plain 401 is not a step-up", async () => {
    const api = createApi("/api", () => Promise.resolve("t"), reply(401, { detail: "expired" }));
    const err = await api.get("/staff/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(StepUpRequired);
    expect(isStepUp(null)).toBe(false);
  });
});
