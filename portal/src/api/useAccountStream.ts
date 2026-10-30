import { useEffect, useState } from "react";
import { ApiError } from "./client";
import { useApi } from "./ApiProvider";
import type { LiveAccount, Quote } from "./types";

export interface Live {
  account: LiveAccount | null;
  quotes: Record<string, Quote>;
  connected: boolean;
  error: ApiError | null; // a refusal (unknown account): no more retries
}

const RETRY_MS = [1_000, 2_000, 5_000, 15_000];
const REOPEN_MS = 500;

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** The account's live state and the quotes, kept up to date by the API's
event stream; the stream is opened again when the server ends it (every
few minutes, so the token is fresh) or the connection drops. */
export function useAccountStream(acc: string): Live {
  const api = useApi();
  const [live, setLive] = useState<Live>({ account: null, quotes: {}, connected: false, error: null });
  useEffect(() => {
    const abort = new AbortController();
    let failures = 0;
    const onEvent = (name: string, data: unknown) => {
      failures = 0;
      if (name === "account") setLive((l) => ({ ...l, account: data as LiveAccount, connected: true }));
      else if (name === "quotes") setLive((l) => ({ ...l, quotes: data as Record<string, Quote>, connected: true }));
    };
    const stopped = () => abort.signal.aborted; // read afresh after every await
    void (async () => {
      while (!stopped()) {
        try {
          await api.events(`/accounts/${encodeURIComponent(acc)}/stream`, onEvent, abort.signal);
          await pause(REOPEN_MS, abort.signal); // the server ended it: open it again shortly
        } catch (error) {
          if (stopped()) return;
          if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 401) {
            setLive((l) => ({ ...l, connected: false, error }));
            return;
          }
          setLive((l) => ({ ...l, connected: false }));
          const wait = RETRY_MS[Math.min(failures, RETRY_MS.length - 1)] ?? 15_000;
          failures += 1;
          await pause(wait, abort.signal);
        }
      }
    })();
    return () => {
      abort.abort();
    };
  }, [api, acc]);
  return live;
}
