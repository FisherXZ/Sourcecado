/// <reference types="vite/client" />

declare const __CLUB_DEV_TOKEN__: string;

interface Window {
  __CLUB_HTTP__?: string;
  __CLUB_API_TOKEN__?: string;
}

/**
 * Stamped into the bundle at build time by the packaging workflow. Absent in a
 * developer build, which `describeChannel` reads as the stable channel.
 */
interface ImportMetaEnv {
  readonly VITE_SOURCECADO_CHANNEL?: string;
  readonly VITE_SOURCECADO_VERSION?: string;
  readonly VITE_SOURCECADO_COMMIT?: string;
}
