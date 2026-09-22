import { homedir } from "node:os";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function loadDevToken(): string {
  const fromEnv = process.env.VITE_CLUB_TOKEN || process.env.CLUB_API_TOKEN;
  if (fromEnv) return fromEnv;
  // Match the backend's state override so an isolated checkout never borrows
  // the token belonging to the operator's normal installation.
  const override = process.env.CLUB_STATE_DIR;
  const stateDir = override === "~"
    ? homedir()
    : override?.startsWith("~/")
      ? join(homedir(), override.slice(2))
      : override || join(homedir(), ".config", "club");
  const path = join(stateDir, "backend-8765.token");
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
