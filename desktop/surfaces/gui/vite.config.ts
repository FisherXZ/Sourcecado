import { homedir } from "node:os";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function loadDevToken(): string {
  const fromEnv = process.env.VITE_CLUB_TOKEN || process.env.CLUB_API_TOKEN;
  if (fromEnv) return fromEnv;
  const path = join(homedir(), ".config", "club", "sidecar-8765.token");
  try {
    return readFileSync(path, "utf8").trim();
  } catch {
    return "";
  }
}

export default defineConfig(({ command }) => ({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
      },
    },
  },
  define: {
    __CLUB_DEV_TOKEN__: JSON.stringify(command === "serve" ? loadDevToken() : ""),
  },
}));
