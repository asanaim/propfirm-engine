import { InMemoryWebStorage, UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import type { AuthProvider } from "react-admin";
import { ApiError, StepUpRequired, type Api } from "./api";

/** Staff sign in through Keycloak's `backoffice` client: Authorization Code
with PKCE, tokens in memory only, and always with a second factor (the API
refuses a staff login without one). */
export function createManager(authority: string, clientId: string, origin: string): UserManager {
  return new UserManager({
    authority,
    client_id: clientId,
    redirect_uri: `${origin}/callback`,
    silent_redirect_uri: `${origin}/silent`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: "openid",
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
    automaticSilentRenew: true,
  });
}

export interface Me {
  access: {
    sid: string | null;
    tier: string | null;
    title: string | null;
    permissions: string[];
    sections: Record<string, string>;
  };
  member: { name: string; email: string | null } | null;
}

let current: User | null = null;

/** The access token, renewed silently when it has expired. */
export function tokenSource(manager: UserManager): () => Promise<string | null> {
  return async () => {
    if (current === null || current.expired === true) {
      current = await manager.getUser();
      if (current === null || current.expired === true) {
        try {
          current = await manager.signinSilent();
        } catch {
          current = null;
        }
      }
    }
    return current?.access_token ?? null;
  };
}

/** Sign in again now (max_age=0): for approvals the API wants a fresh
second factor (step-up). */
export function stepUp(manager: UserManager): Promise<void> {
  return manager.signinRedirect({ max_age: 0, state: { returnTo: window.location.hash } });
}

export function authProvider(manager: UserManager, api: Api): AuthProvider {
  return {
    async login() {
      const back = window.location.hash === "#/login" ? "" : window.location.hash;
      await manager.signinRedirect({ state: { returnTo: back } });
    },
    async logout() {
      const user = await manager.getUser();
      current = null;
      await manager.removeUser();
      // end the Keycloak session too, or the next sign-in skips the second factor
      if (user?.id_token !== undefined) await manager.signoutRedirect({ id_token_hint: user.id_token });
    },
    async checkAuth() {
      if ((await tokenSource(manager)()) === null) throw new Error("not signed in");
    },
    checkError(error: unknown) {
      if (error instanceof StepUpRequired) return Promise.resolve(); // shown with its own button
      if (error instanceof ApiError && error.status === 401) return Promise.reject(new Error("signed out"));
      return Promise.resolve();
    },
    async getIdentity() {
      if ((await tokenSource(manager)()) === null) return { id: "anonymous" };
      const me = await api.get<Me>("/staff/me");
      const name = me.member?.name ?? current?.profile.email ?? "staff";
      return { id: me.access.sid ?? current?.profile.sub ?? "me", fullName: `${name} (${me.access.title ?? "-"})` };
    },
    async getPermissions() {
      if ((await tokenSource(manager)()) === null) return [];
      const me = await api.get<Me>("/staff/me");
      return me.access.permissions;
    },
  };
}

/** After Keycloak's redirect: redeem the code, go back where we were. */
export async function completeSignIn(manager: UserManager): Promise<void> {
  const user = await manager.signinRedirectCallback();
  current = user;
  const state = user.state as { returnTo?: unknown } | undefined;
  const back = typeof state?.returnTo === "string" ? state.returnTo : "";
  window.history.replaceState(null, "", `/${back}`);
}
