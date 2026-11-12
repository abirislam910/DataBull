/// <reference types="vite/client" />

/**
 * Build-time environment variables. Vite inlines `VITE_`-prefixed values into
 * the bundle when it builds, so they are public: never put a secret here.
 */
interface ImportMetaEnv {
  /**
   * The API's base URL for a frontend deployed on another domain, for example
   * `https://<id>.cloudfront.net`. Unset in local development, where requests go
   * to `/api` and the Vite dev server proxies them to the backend.
   */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
