import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createApi } from "./api";
import { App } from "./App";
import { completeSignIn, createManager, tokenSource } from "./auth";
import { config } from "./config";
import "./backoffice.css";

const manager = createManager(config.authority, config.clientId, window.location.origin);

function start(): void {
  const root = document.getElementById("root");
  if (root === null) throw new Error("no #root");
  const api = createApi(config.apiBase, tokenSource(manager));
  createRoot(root).render(
    <StrictMode>
      <App api={api} manager={manager} />
    </StrictMode>,
  );
}

if (window.location.pathname === "/silent") {
  void manager.signinSilentCallback(); // the hidden frame of a silent renewal
} else if (window.location.pathname === "/callback") {
  completeSignIn(manager).then(start, (e: unknown) => {
    document.body.textContent = `Sign-in failed: ${e instanceof Error ? e.message : "unknown error"}`;
  });
} else {
  start();
}
