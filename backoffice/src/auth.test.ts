import type { UserManager } from "oidc-client-ts";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Api } from "./api";
import { authProvider, refusedSignIn } from "./auth";

function manager() {
  return {
    getUser: vi.fn(() => Promise.resolve({ access_token: "t", expired: false, profile: { sub: "u1" } })),
    removeUser: vi.fn(() => Promise.resolve()),
    signinRedirect: vi.fn(() => Promise.resolve()),
    signinSilent: vi.fn(() => Promise.resolve(null)),
  };
}

function api(answer: () => Promise<unknown>): Api {
  return { get: <T,>() => answer() as Promise<T>, post: () => Promise.reject(new Error("no")), put: () => Promise.reject(new Error("no")) };
}

describe("D-14.19: staff sign-in only", () => {
  it("signs a trader's login out of the back office and says why", async () => {
    const m = manager();
    const provider = authProvider(m as unknown as UserManager, api(() => Promise.reject(new ApiError(403, "not_staff", "this sign-in is not a staff login"))));
    await expect(provider.checkAuth({})).rejects.toThrow("not a staff login");
    expect(m.removeUser).toHaveBeenCalled();
    expect(refusedSignIn()).toBe("not_staff");
  });

  it("keeps a staff login, and a refusal of another kind is not a sign-out", async () => {
    const m = manager();
    await authProvider(m as unknown as UserManager, api(() => Promise.resolve({ access: { permissions: [] } }))).checkAuth({});
    const screening = authProvider(m as unknown as UserManager, api(() => Promise.reject(new ApiError(403, "screening_required", "screening"))));
    await screening.checkAuth({});
    expect(m.removeUser).not.toHaveBeenCalled();
  });

  it("always asks for the password at the back office", async () => {
    const m = manager();
    await authProvider(m as unknown as UserManager, api(() => Promise.resolve({}))).login({});
    expect(m.signinRedirect).toHaveBeenCalledWith(expect.objectContaining({ prompt: "login" }));
    expect(refusedSignIn()).toBeNull();
  });
});
