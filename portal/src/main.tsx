import "@fontsource-variable/inter";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ApiProvider } from "./api/ApiProvider";
import { AuthProvider } from "./auth/AuthProvider";
import { createManager } from "./auth/session";
import { config } from "./config";
import "./styles/portal.css";

// the web server writes each page's own title (review D10); in development
// there is none, so the app's name
if (document.title === "") document.title = "pipridge";

const manager = createManager(config.authority, config.clientId, window.location.origin);

if (window.location.pathname === "/silent") {
  // the hidden frame of a silent sign-in: hand the answer to the portal, no UI
  void manager.signinSilentCallback();
} else {
  const root = document.getElementById("root");
  if (root === null) throw new Error("no #root");
  createRoot(root).render(
    <StrictMode>
      <AuthProvider manager={manager}>
        <ApiProvider base={config.apiBase}>
          <App />
        </ApiProvider>
      </AuthProvider>
    </StrictMode>,
  );
}
