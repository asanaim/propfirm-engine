/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OIDC_AUTHORITY?: string; // the Keycloak realm
  readonly VITE_BRAND?: string; // the brand shown (default pipridge)
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
