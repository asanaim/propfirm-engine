import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { useBackOffice } from "./context";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

/** GET one path; `reload` fetches again (after an action). */
export function useGet<T>(path: string): { result: Loaded<T>; reload: () => void } {
  const { api } = useBackOffice();
  const [round, setRound] = useState(0);
  const [result, setResult] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    api.get<T>(path).then(
      (data) => {
        if (live) setResult({ state: "ready", data });
      },
      (e: unknown) => {
        if (live) setResult({ state: "error", message: e instanceof ApiError ? e.message : "could not load" });
      },
    );
    return () => {
      live = false;
    };
  }, [api, path, round]);
  const reload = useCallback(() => {
    setRound((n) => n + 1);
  }, []);
  return { result, reload };
}
