import { defineConfig } from "vitest/config";
import { resolve } from "path";

export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  // Transform JSX/TSX in component tests via esbuild (automatic runtime).
  // We deliberately avoid @vitejs/plugin-react: its v6 peer-requires Vite 8,
  // which conflicts with the Vite that Vitest 3 ships.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    fileParallelism: false,
    globals: true,
    typecheck: {
      tsconfig: "./tsconfig.test.json",
    },
  },
});
