/// <reference types="vite/client" />

declare const __CLUB_DEV_TOKEN__: string;

interface Window {
  __CLUB_HTTP__?: string;
  __CLUB_API_TOKEN__?: string;
}
