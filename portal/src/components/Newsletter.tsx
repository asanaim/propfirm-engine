import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { useApi } from "../api/ApiProvider";
import { extensions, type Slot } from "../lib/extensions";
import { Captcha } from "./Captcha";

interface Wording {
  on: boolean;
  wording: string;
  version: string;
}

/** The footer's newsletter form (rule AC25): the wording shown is the
consent the API records, by its version. A confirmation email follows. */
export function NewsletterForm() {
  const api = useApi();
  const [shown, setShown] = useState<Wording | null>(null);
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");
  const [captcha, setCaptcha] = useState<Slot | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [round, setRound] = useState(0); // a token is good once: a new check after each try
  useEffect(() => {
    let live = true;
    void extensions(api).then((found) => {
      if (live) setCaptcha(found.captcha);
    });
    api.get<Wording>("/subscribe").then(
      (w) => {
        if (live) setShown(w);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  if (shown === null || !shown.on) return null;
  const wording = shown;
  async function send(event: React.SyntheticEvent) {
    event.preventDefault();
    setState("sending");
    try {
      await api.post("/subscribers", {
        email,
        source: "footer",
        version: wording.version,
        ...(token !== null ? { captcha: token } : {}),
      });
      setState("sent");
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Could not send. Try again later.");
      setState("error");
      setToken(null);
      setRound((r) => r + 1);
    }
  }
  if (state === "sent") {
    return <p className="newsletter-sent">Check your inbox: confirm by the link we sent to start receiving news.</p>;
  }
  return (
    <form className="newsletter" onSubmit={(e) => void send(e)} aria-label="Newsletter">
      <label htmlFor="newsletter-email">{wording.wording}</label>
      <div className="newsletter-row">
        <input
          id="newsletter-email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
          }}
        />
        <button type="submit" disabled={state === "sending" || (captcha !== null && token === null)}>
          Subscribe
        </button>
      </div>
      {captcha !== null ? <Captcha key={round} siteKey={captcha.public_id} onToken={setToken} /> : null}
      {state === "error" ? <p role="alert">{error}</p> : null}
    </form>
  );
}

/** The page an emailed link opens: confirm a subscription, or leave the
list. The token travels in the link and is sent once. */
export function SubscriptionLinkPage({ action, token }: { action: "confirm" | "unsubscribe"; token: string | null }) {
  const api = useApi();
  const [answer, setDone] = useState<boolean | null>(null);
  const done = token === null ? false : answer;
  useEffect(() => {
    if (token === null) return;
    let live = true;
    const path = action === "confirm" ? "/subscribers/confirm" : "/subscribers/unsubscribe";
    api.post<Record<string, boolean>>(path, { token }).then(
      (r) => {
        if (live) setDone(Object.values(r)[0] === true);
      },
      () => {
        if (live) setDone(false);
      },
    );
    return () => {
      live = false;
    };
  }, [api, action, token]);
  let text = "One moment…";
  if (done === true) text = action === "confirm" ? "You are subscribed. Thank you." : "You are unsubscribed. No more news will be sent.";
  if (done === false) text = action === "confirm" ? "This link has expired or was already used. Subscribe again from the footer." : "This link is not valid.";
  return (
    <div className="page narrow">
      <h1>{action === "confirm" ? "Newsletter" : "Unsubscribe"}</h1>
      <p role="status">{text}</p>
    </div>
  );
}

export function linkToken(search: string): string | null {
  const token = new URLSearchParams(search).get("token");
  return token !== null && /^[\w-]{10,100}$/.test(token) ? token : null;
}
