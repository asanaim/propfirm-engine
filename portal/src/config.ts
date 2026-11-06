/** Settings the portal is built with (Vite `VITE_*` variables). */
export interface PortalConfig {
  authority: string;
  clientId: string;
  apiBase: string;
  brand: string;
}

export const config: PortalConfig = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/realms/propfirm",
  clientId: "portal",
  apiBase: "/api",
  brand: import.meta.env.VITE_BRAND ?? "pipridge", // written all lowercase; a buyer sets their own
};
