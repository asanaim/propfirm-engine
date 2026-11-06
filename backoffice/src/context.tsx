import type { UserManager } from "oidc-client-ts";
import { createContext, useContext } from "react";
import type { Api } from "./api";

export interface BackOffice {
  api: Api;
  manager: UserManager;
}

export const BackOfficeContext = createContext<BackOffice | null>(null);

export function useBackOffice(): BackOffice {
  const found = useContext(BackOfficeContext);
  if (found === null) throw new Error("useBackOffice outside BackOfficeContext");
  return found;
}
