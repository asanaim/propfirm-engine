import type { User, UserManager } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { returnPath, sessionOf, type ReturnState, type Session } from "./session";

export interface Auth {
  status: "loading" | "anonymous" | "signed_in" | "error";
  session: Session | null;
  error: string | null;
  signIn: (returnTo?: string) => Promise<void>;
  /** Keycloak's registration page (OIDC prompt=create), then back here signed in. */
  signUp: (returnTo?: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Enter the credentials and second factor again now (RFC 9470 step-up;
  max_age=0, so the new sign-in is fresh whatever the API's limit). */
  stepUp: (returnTo?: string) => Promise<void>;
  /** Keycloak's authenticator-app setup, then back here. */
  setUpAuthenticator: (returnTo?: string) => Promise<void>;
  accessToken: () => Promise<string | null>;
}

export const AuthContext = createContext<Auth | null>(null);

// One start per manager: React runs effects twice in development, and a
// login callback can be redeemed only once.
const starts = new WeakMap<UserManager, Promise<User | null>>();

function start(manager: UserManager): Promise<User | null> {
  let started = starts.get(manager);
  if (started === undefined) {
    started = (async () => {
      if (window.location.pathname === "/callback") {
        const user = await manager.signinRedirectCallback();
        window.history.replaceState(null, "", returnPath(user));
        window.dispatchEvent(new PopStateEvent("popstate"));
        return user;
      }
      const user = await manager.getUser();
      if (user !== null && !user.expired) return user;
      try {
        return await manager.signinSilent(); // the Keycloak session, if any
      } catch {
        return null;
      }
    })();
    starts.set(manager, started);
  }
  return started;
}

function here(): string {
  return `${window.location.pathname}${window.location.search}`;
}

export function AuthProvider({ manager, children }: { manager: UserManager; children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Auth["status"]>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    start(manager).then(
      (found) => {
        if (!alive) return;
        setUser(found);
        setStatus(found === null ? "anonymous" : "signed_in");
      },
      (reason: unknown) => {
        if (!alive) return;
        setError(reason instanceof Error ? reason.message : "sign-in failed");
        setStatus("error");
      },
    );
    const loaded = (next: User) => {
      setUser(next);
      setStatus("signed_in");
    };
    const unloaded = () => {
      setUser(null);
      setStatus("anonymous");
    };
    manager.events.addUserLoaded(loaded);
    manager.events.addUserUnloaded(unloaded);
    return () => {
      alive = false;
      manager.events.removeUserLoaded(loaded);
      manager.events.removeUserUnloaded(unloaded);
    };
  }, [manager]);

  const redirect = useCallback(
    (returnTo: string | undefined, extra: Record<string, string | number> = {}) => {
      const state: ReturnState = { returnTo: returnTo ?? here() };
      const { max_age, ...params } = extra;
      return manager.signinRedirect({
        state,
        ...(max_age === undefined ? {} : { max_age: Number(max_age) }),
        ...(Object.keys(params).length ? { extraQueryParams: params } : {}),
      });
    },
    [manager],
  );

  const value = useMemo<Auth>(
    () => ({
      status,
      session: user === null ? null : sessionOf(user),
      error,
      signIn: (returnTo) => redirect(returnTo),
      signUp: (returnTo) => redirect(returnTo, { prompt: "create" }),
      signOut: () => manager.signoutRedirect(),
      stepUp: (returnTo) => redirect(returnTo, { max_age: 0 }),
      setUpAuthenticator: (returnTo) => redirect(returnTo, { kc_action: "CONFIGURE_TOTP" }),
      accessToken: async () => {
        const current = await manager.getUser();
        return current === null || current.expired ? null : current.access_token;
      },
    }),
    [status, user, error, redirect, manager],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (auth === null) throw new Error("useAuth outside AuthProvider");
  return auth;
}
