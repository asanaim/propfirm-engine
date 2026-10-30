import { User } from "oidc-client-ts";
import { createManager, returnPath, sessionOf } from "./session";

function user(profile: Record<string, unknown>, state?: unknown): User {
  return new User({
    access_token: "a",
    token_type: "Bearer",
    profile: { sub: "u1", iss: "i", aud: "portal", exp: 0, iat: 0, ...profile },
    ...(state === undefined ? {} : { userState: state }),
  });
}

it("reads how the user signed in", () => {
  const s = sessionOf(user({ email: "a@example.com", amr: ["pwd", "otp"], auth_time: 1_793_000_000 }));
  expect(s).toEqual({
    subject: "u1",
    email: "a@example.com",
    name: null,
    amr: ["pwd", "otp"],
    mfa: true,
    authTime: 1_793_000_000_000,
  });
  expect(sessionOf(user({ amr: ["pwd"] })).mfa).toBe(false);
  expect(sessionOf(user({ amr: "otp", auth_time: "1" }))).toMatchObject({ amr: [], mfa: false, authTime: null });
});

it("returns only to paths of the portal", () => {
  expect(returnPath(user({}, { returnTo: "/shop" }))).toBe("/shop");
  expect(returnPath(user({}, { returnTo: "https://evil.example/" }))).toBe("/");
  expect(returnPath(user({}, { returnTo: "//evil.example/" }))).toBe("/");
  expect(returnPath(user({}))).toBe("/");
});

it("keeps the sign-in claims the portal reads", () => {
  const settings = createManager("http://kc.example/realms/r", "portal", "http://portal.example").settings;
  const removed = settings.filterProtocolClaims;
  expect(Array.isArray(removed) ? removed : []).not.toContain("amr");
  expect(Array.isArray(removed) ? removed : []).not.toContain("auth_time");
  expect(settings.redirect_uri).toBe("http://portal.example/callback");
  expect(settings.response_type).toBe("code");
});
