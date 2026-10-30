import { InMemoryWebStorage, UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

/** Second factors recognised in the `amr` claim (as the API: RFC 8176). */
export const SECOND_FACTORS = new Set(["otp", "mfa", "hwk", "swk", "webauthn", "sms"]);

export interface Session {
  subject: string;
  email: string | null;
  name: string | null;
  amr: string[];
  mfa: boolean; // this sign-in used a second factor
  authTime: number | null; // ms UTC: when the user last entered their credentials
}

export function sessionOf(user: User): Session {
  const claims = user.profile as Record<string, unknown>;
  const amr = Array.isArray(claims.amr) ? claims.amr.filter((m): m is string => typeof m === "string") : [];
  const authTime = typeof claims.auth_time === "number" ? claims.auth_time * 1000 : null;
  return {
    subject: user.profile.sub,
    email: typeof claims.email === "string" ? claims.email : null,
    name: typeof claims.name === "string" ? claims.name : null,
    amr,
    mfa: amr.some((m) => SECOND_FACTORS.has(m)),
    authTime,
  };
}

/** Authorization Code with PKCE (OAuth 2.0 for Browser-Based Apps): tokens
are kept in memory only, never in localStorage; a reload signs in again
silently through the Keycloak session. */
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
    silentRequestTimeoutInSeconds: 5,
    // keep amr and auth_time in the profile (the library drops them by
    // default): the portal shows whether this sign-in used a second factor
    filterProtocolClaims: ["nbf", "jti", "nonce", "azp", "at_hash"],
  });
}

export interface ReturnState {
  returnTo: string;
}

export function returnPath(user: User): string {
  const state = user.state as Partial<ReturnState> | undefined;
  const to = state?.returnTo;
  // only paths of this portal: never an outside address from the state
  return typeof to === "string" && to.startsWith("/") && !to.startsWith("//") ? to : "/";
}
