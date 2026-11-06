import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { ApiProvider } from "./api/ApiProvider";
import { AuthContext, type Auth } from "./auth/AuthProvider";

export function fakeAuth(change: Partial<Auth> = {}): Auth {
  return {
    status: "signed_in",
    session: { subject: "u1", email: "u1@example.com", name: null, amr: ["pwd"], mfa: false, authTime: 0 },
    error: null,
    signIn: vi.fn(() => Promise.resolve()),
    signOut: vi.fn(() => Promise.resolve()),
    stepUp: vi.fn(() => Promise.resolve()),
    setUpAuthenticator: vi.fn(() => Promise.resolve()),
    accessToken: () => Promise.resolve("t"),
    ...change,
  };
}

type Route = [method: string, path: string, status: number, body: unknown, headers?: Record<string, string>];

/** A fetch that answers from a list of routes and records each call. */
export function fakeFetch(routes: Route[]) {
  return vi.fn<typeof fetch>((input, init) => {
    const method = init?.method ?? "GET";
    const path = (input as string).replace(/^\/api/, "");
    const found = routes.find(([m, p]) => m === method && p === path);
    if (found === undefined) return Promise.reject(new Error(`no route ${method} ${path}`));
    const [, , status, body, headers] = found;
    return Promise.resolve(new Response(JSON.stringify(body), { status, headers: headers ?? {} }));
  });
}

export function renderWith(ui: ReactNode, auth: Auth, fetcher: typeof fetch) {
  return render(
    <AuthContext.Provider value={auth}>
      <ApiProvider base="/api" fetcher={fetcher}>
        {ui}
      </ApiProvider>
    </AuthContext.Provider>,
  );
}
