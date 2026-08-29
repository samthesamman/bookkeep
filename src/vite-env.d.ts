/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_VERSION?: string;
  /** When "true"/"1", render the whole UI in retro 90s GeoCities mode. */
  readonly VITE_GEOCITIES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
