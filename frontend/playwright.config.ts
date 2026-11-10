import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against the real Vercel entrypoint (app.py: stateless FastAPI + the
// production build in frontend/dist). Build first: `npm run build`. Uses the installed
// Microsoft Edge, so no browser download is needed.
// Set E2E_BASE_URL to run the same specs against a deployment instead (no local server).
const PORT = 8781;
const REMOTE = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "../runs/playwright", // gitignored: traces may contain screenplay text
  use: {
    baseURL: REMOTE ?? `http://127.0.0.1:${PORT}`,
    channel: "msedge",
    trace: "off",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Edge"], channel: "msedge", viewport: { width: 1440, height: 900 } },
      testIgnore: /mobile.spec.ts/,
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"], browserName: "chromium", channel: "msedge" },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: REMOTE ? undefined : {
    // run from the repository root, where app.py and the .venv live
    command: `.venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
