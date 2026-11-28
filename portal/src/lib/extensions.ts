import type { Api } from "../api/client";

/** One switched-on extension slot (rule AC22): its provider and public id.
Never a secret. */
export interface Slot {
  provider: string;
  public_id: string;
}

export interface Extensions {
  chat: Slot | null;
  captcha: Slot | null;
  analytics: Slot | null;
}

const NONE: Extensions = { chat: null, captcha: null, analytics: null };

// the same shapes the API checks; checked again before any id reaches a script
const SHAPE: Record<string, RegExp> = {
  crisp: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  turnstile: /^[0-9]x[0-9A-Za-z_-]{10,64}$/,
  cloudflare: /^[0-9a-f]{32}$/,
};
const PROVIDER: Record<keyof Extensions, string> = { chat: "crisp", captcha: "turnstile", analytics: "cloudflare" };

/** Keep only well formed slots from an answer of /site/extensions. */
export function clean(answer: unknown): Extensions {
  const out: Extensions = { ...NONE };
  if (typeof answer !== "object" || answer === null) return out;
  for (const slot of Object.keys(PROVIDER) as (keyof Extensions)[]) {
    const s = (answer as Record<string, unknown>)[slot];
    if (typeof s !== "object" || s === null) continue;
    const { provider, public_id } = s as Record<string, unknown>;
    if (provider !== PROVIDER[slot] || typeof public_id !== "string") continue;
    if (!(SHAPE[provider]?.test(public_id) ?? false)) continue;
    out[slot] = { provider, public_id };
  }
  return out;
}

let fetched: Promise<Extensions> | null = null;

/** The slots in force, fetched once per page load. A failure means none. */
export function extensions(api: Api): Promise<Extensions> {
  fetched ??= api.get<unknown>("/site/extensions").then(clean, () => NONE);
  return fetched;
}

/** Test seam: forget the fetched slots. */
export function forget(): void {
  fetched = null;
}

function script(src: string, attrs: Record<string, string> = {}): HTMLScriptElement {
  const found = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
  if (found !== null) return found;
  const el = document.createElement("script");
  el.src = src;
  el.async = true;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  document.head.appendChild(el);
  return el;
}

declare global {
  interface Window {
    $crisp?: unknown[];
    CRISP_WEBSITE_ID?: string;
    turnstile?: {
      render: (el: HTMLElement, opts: { sitekey: string; callback: (token: string) => void; "expired-callback": () => void }) => string;
      remove: (id: string) => void;
    };
  }
}

// each provider's script hosts; the server's policy allows all three
const SCRIPT_HOSTS: Record<keyof Extensions, string[]> = {
  chat: ["https://client.crisp.chat", "https://settings.crisp.chat"],
  captcha: ["https://challenges.cloudflare.com"],
  analytics: ["https://static.cloudflareinsights.com"],
};

/** The script policy for the slots switched on: own scripts plus only
those providers' hosts. */
export function scriptPolicy(found: Extensions): string {
  const hosts = (Object.keys(SCRIPT_HOSTS) as (keyof Extensions)[]).filter((k) => found[k] !== null).flatMap((k) => SCRIPT_HOSTS[k]);
  return ["script-src 'self'", ...hosts].join(" ");
}

/** Narrow the page's script policy to the slots switched on (rule AC22).
The server's policy names all three providers; a second policy can only
narrow it, and browsers apply both. Set once, before any provider loads. */
export function narrow(found: Extensions): void {
  if (document.querySelector('meta[http-equiv="Content-Security-Policy"]') !== null) return;
  const meta = document.createElement("meta");
  meta.httpEquiv = "Content-Security-Policy";
  meta.content = scriptPolicy(found);
  document.head.prepend(meta);
}

/** Load live chat and analytics when switched on (rule AC22). */
export function install(found: Extensions): void {
  narrow(found);
  if (found.chat !== null) {
    window.$crisp ??= [];
    window.CRISP_WEBSITE_ID = found.chat.public_id;
    script("https://client.crisp.chat/l.js");
  }
  if (found.analytics !== null) {
    script("https://static.cloudflareinsights.com/beacon.min.js", {
      defer: "",
      "data-cf-beacon": JSON.stringify({ token: found.analytics.public_id }),
    });
  }
}

export const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

/** Turnstile's script, loaded once; resolves when `window.turnstile` is there. */
export function turnstileReady(): Promise<NonNullable<Window["turnstile"]>> {
  return new Promise((resolve, reject) => {
    if (window.turnstile !== undefined) {
      resolve(window.turnstile);
      return;
    }
    const el = script(TURNSTILE_SRC);
    el.addEventListener("load", () => {
      if (window.turnstile !== undefined) resolve(window.turnstile);
      else reject(new Error("turnstile did not load"));
    });
    el.addEventListener("error", () => {
      reject(new Error("turnstile did not load"));
    });
  });
}
