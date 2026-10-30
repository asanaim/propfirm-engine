import { ApiError, StepUpRequired, createApi, parseEvents, parseStepUp } from "./client";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn<typeof fetch>().mockResolvedValue(
    new Response(body === null ? "" : JSON.stringify(body), { status, headers }),
  );
}

it("sends the bearer token and JSON, and reads JSON", async () => {
  const fetcher = respond(201, { cif: "CIF00000001" });
  const api = createApi("/api", () => Promise.resolve("t0ken"), fetcher);
  expect(await api.post("/me", { full_name: "Ada" })).toEqual({ cif: "CIF00000001" });
  const [url, init] = fetcher.mock.calls[0] ?? [];
  expect(url).toBe("/api/me");
  expect(init?.method).toBe("POST");
  expect(init?.headers).toMatchObject({ Authorization: "Bearer t0ken", "Content-Type": "application/json" });
  expect(init?.body).toBe('{"full_name":"Ada"}');
});

it("sends no token when signed out", async () => {
  const fetcher = respond(200, []);
  await createApi("/api", () => Promise.resolve(null), fetcher).get("/catalogue");
  expect(fetcher.mock.calls[0]?.[1]?.headers).not.toHaveProperty("Authorization");
});

it("keeps the API's refusal code and message", async () => {
  const api = createApi("/api", () => Promise.resolve(null), respond(403, { detail: { code: "refused", message: "no" } }));
  const error = await api.get("/x").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 403, code: "refused", message: "no" });
  const plain = createApi("/api", () => Promise.resolve(null), respond(404, { detail: "unknown account" }));
  expect(await plain.get("/x").catch((e: unknown) => e)).toMatchObject({ status: 404, code: null });
});

it("turns an RFC 9470 challenge into a step-up request", async () => {
  const header = 'Bearer error="insufficient_user_authentication", max_age=300';
  const fetcher = respond(401, { detail: { code: "reauth_required", message: "confirm" } }, { "WWW-Authenticate": header });
  const error = await createApi("/api", () => Promise.resolve("t"), fetcher).post("/me/wallet", {}).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(StepUpRequired);
  expect(error).toMatchObject({ code: "reauth_required", maxAge: 300, status: 401 });
  const expired = respond(401, { detail: "invalid token" }, { "WWW-Authenticate": "Bearer" });
  const other = await createApi("/api", () => Promise.resolve("t"), expired).get("/me").catch((e: unknown) => e);
  expect(other).toBeInstanceOf(ApiError);
  expect(other).not.toBeInstanceOf(StepUpRequired);
});

it("parses the challenge", () => {
  expect(parseStepUp(null)).toBeNull();
  expect(parseStepUp('Bearer error="invalid_token"')).toBeNull();
  expect(parseStepUp('Bearer error="insufficient_user_authentication"')).toEqual({ maxAge: null });
  expect(parseStepUp('Bearer error="insufficient_user_authentication", max_age=60')).toEqual({ maxAge: 60 });
});

it("splits a server-sent event stream across chunks", () => {
  const got: [string, unknown][] = [];
  const push = (name: string, data: unknown) => got.push([name, data]);
  let rest = parseEvents('event: account\ndata: {"a":1}\n\n: keep-alive\n\nevent: quo', push);
  expect(got).toEqual([["account", { a: 1 }]]);
  rest = parseEvents(rest + 'tes\ndata: {"EUR/USD":{}}\n\nevent: end\ndata: {}\n\n', push);
  expect(rest).toBe("");
  expect(got).toEqual([
    ["account", { a: 1 }],
    ["quotes", { "EUR/USD": {} }],
    ["end", {}],
  ]);
});

it("reads events from a response body with the token", async () => {
  const body = 'event: account\ndata: {"equity_cents":5}\n\nevent: end\ndata: {}\n\n';
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 200 }));
  const got: string[] = [];
  await createApi("/api", () => Promise.resolve("t"), fetcher).events(
    "/accounts/A/stream",
    (name) => got.push(name),
    new AbortController().signal,
  );
  expect(got).toEqual(["account", "end"]);
  expect(fetcher.mock.calls[0]?.[1]?.headers).toMatchObject({ Authorization: "Bearer t" });
  const refused = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"detail":"unknown account"}', { status: 404 }));
  const error = await createApi("/api", () => Promise.resolve("t"), refused)
    .events("/accounts/Z/stream", () => undefined, new AbortController().signal)
    .catch((e: unknown) => e);
  expect(error).toMatchObject({ status: 404, message: "unknown account" });
});
