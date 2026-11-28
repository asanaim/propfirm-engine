import { useEffect, useRef } from "react";
import { turnstileReady } from "../lib/extensions";

/** Cloudflare Turnstile's check (rule AC22): hands its one-time token to
`onToken`, and null when it expires. */
export function Captcha({ siteKey, onToken }: { siteKey: string; onToken: (token: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const report = useRef(onToken);
  useEffect(() => {
    report.current = onToken;
  });
  useEffect(() => {
    let id: string | null = null;
    let live = true;
    turnstileReady().then(
      (t) => {
        if (!live || box.current === null) return;
        id = t.render(box.current, {
          sitekey: siteKey,
          callback: (token) => {
            report.current(token);
          },
          "expired-callback": () => {
            report.current(null);
          },
        });
      },
      () => undefined,
    );
    return () => {
      live = false;
      if (id !== null) window.turnstile?.remove(id);
    };
  }, [siteKey]);
  return <div className="captcha" ref={box} />;
}
