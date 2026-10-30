import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";
import { ApiError, createApi, type Api } from "./client";
import type { Me } from "./types";

const ApiContext = createContext<Api | null>(null);

export function ApiProvider({ base, children, fetcher }: { base: string; children: ReactNode; fetcher?: typeof fetch }) {
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(base, accessToken, fetcher), [base, accessToken, fetcher]);
  return <ApiContext.Provider value={api}>{children}</ApiContext.Provider>;
}

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (api === null) throw new Error("useApi outside ApiProvider");
  return api;
}

export type MeState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unregistered" }
  | { status: "ready"; me: Me }
  | { status: "error"; error: string };

/** The signed-in customer: GET /me (404: not registered yet). */
export function useMe(): MeState & { reload: () => void } {
  const api = useApi();
  const auth = useAuth();
  const [round, setRound] = useState(0);
  const [fetched, setFetched] = useState<{ round: number; state: MeState } | null>(null);
  const signedIn = auth.status === "signed_in";
  useEffect(() => {
    if (!signedIn) return;
    let alive = true;
    api.get<Me>("/me").then(
      (me) => {
        if (alive) setFetched({ round, state: { status: "ready", me } });
      },
      (error: unknown) => {
        if (!alive) return;
        const state: MeState =
          error instanceof ApiError && error.status === 404
            ? { status: "unregistered" }
            : { status: "error", error: error instanceof Error ? error.message : "failed" };
        setFetched({ round, state });
      },
    );
    return () => {
      alive = false;
    };
  }, [api, signedIn, round]);
  const reload = useCallback(() => {
    setRound((r) => r + 1);
  }, []);
  let state: MeState;
  if (auth.status === "loading") state = { status: "loading" };
  else if (!signedIn) state = { status: "anonymous" };
  else if (fetched === null) state = { status: "loading" };
  else state = fetched.state; // a reload keeps showing the last answer until the next
  return { ...state, reload };
}
