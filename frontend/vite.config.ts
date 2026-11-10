/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": "/src" } },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  // In development, /api is proxied to `promolens serve` on port 8000.
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    testTimeout: 20_000,
  },
});
