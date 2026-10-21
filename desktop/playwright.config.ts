import { existsSync } from "node:fs";

import { defineConfig } from "@playwright/test";

// The container ships a Chromium build; use it instead of downloading one.
const preinstalled = "/opt/pw-browsers/chromium";

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:1420",
    locale: "he-IL",
    trace: "retain-on-failure",
    launchOptions: existsSync(preinstalled) ? { executablePath: preinstalled } : {},
  },
  webServer: [
    {
      command: "python3 e2e/fake_openrouter.py 23710",
      port: 23710,
      reuseExistingServer: false,
    },
    {
      command: "bash e2e/start-manager.sh",
      url: "http://127.0.0.1:23700/health",
      timeout: 600_000,
      reuseExistingServer: false,
      stdout: "pipe",
      // Let the manager stop its agent servers instead of being killed outright.
      gracefulShutdown: { signal: "SIGTERM", timeout: 20_000 },
    },
    {
      command: "npm run dev",
      url: "http://localhost:1420",
      reuseExistingServer: false,
      env: { VITE_MANAGER_URL: "http://127.0.0.1:23700", VITE_MANAGER_TOKEN: "e2e-token" },
    },
  ],
});
