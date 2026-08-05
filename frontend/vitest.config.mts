import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirrors the "@/*" path alias in tsconfig.json.
    // import.meta.dirname rather than __dirname: this file is ESM.
    alias: { "@": resolve(import.meta.dirname, ".") },
  },
  test: {
    // jsdom rather than node: the code under test uses browser APIs (File,
    // FileReader, crypto, document), and the bug this suite exists to catch was
    // a browser API behaving differently than it does in Node.
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**", ".next-verify/**"],
  },
});
