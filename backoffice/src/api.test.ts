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

  it("reads a CSV export as text, and keeps the refusal's message", async () => {
    const csv = vi.fn<typeof fetch>(() => Promise.resolve(new Response("ref,type\r\n", { status: 200 })));
    const api = createApi("/api", () => Promise.resolve("tok"), csv);
    expect(await api.text?.("/staff/reports/transactions/export")).toBe("ref,type\r\n");
    const headers = csv.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok");
    const refused = createApi("/api", () => Promise.resolve("t"), reply(422, { detail: "over 10000 rows: narrow the filters" }));
    await expect(refused.text?.("/x")).rejects.toMatchObject({ status: 422, message: "over 10000 rows: narrow the filters" });
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

describe("the shared /staff/me of the auth checks", () => {
  it("serves concurrent and repeated asks from one request for a short time", async () => {
    const { sharedMe, ME_TTL_MS } = await import("./auth");
    let t = 0;
    const get = vi.fn(() => Promise.resolve({ access: { permissions: ["staff.view"] } }));
    const me = sharedMe({ get, post: vi.fn(), put: vi.fn() } as never, () => t);
    await Promise.all([me.get(), me.get()]);
    t = ME_TTL_MS - 1;
    await me.get();
    expect(get).toHaveBeenCalledTimes(1);
    t = ME_TTL_MS;
    await me.get();
    expect(get).toHaveBeenCalledTimes(2);
    me.drop();
    await me.get();
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("does not keep a failed answer", async () => {
    const { sharedMe } = await import("./auth");
    const get = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue({ access: { permissions: [] } });
    const me = sharedMe({ get, post: vi.fn(), put: vi.fn() } as never, () => 0);
    await expect(me.get()).rejects.toThrow("down");
    await me.get();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
