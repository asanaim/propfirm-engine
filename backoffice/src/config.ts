/** Settings the back office is built with (Vite `VITE_*` variables). */
export const config = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/realms/propfirm",
  clientId: "backoffice",
  apiBase: "/api",
  brand: import.meta.env.VITE_BRAND ?? "pipridge",
};
