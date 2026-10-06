/* End-to-end tests: a real browser against the real Django API on a real SQL
   Server database. See e2e/env.js for the ports, the database and the Python used,
   and the README's Tests section for how to run them. */
import { defineConfig, devices } from '@playwright/test'

import { apiPort, apiURL, backendDir, backendEnv, pythonPath, webPort, webURL } from './e2e/env.js'

// A Chromium other than the one this Playwright version downloads, e.g. one baked
// into a container image. Unset, Playwright uses its own.
const executablePath = process.env.E2E_CHROMIUM_PATH || undefined

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.js',
  // A file's tests run in order; separate files run side by side.
  fullyParallel: false,
  workers: process.env.CI ? 2 : 3,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: webURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // --noreload: one process, so Playwright's shutdown actually stops it.
      command: `"${pythonPath()}" manage.py runserver 127.0.0.1:${apiPort} --noreload`,
      cwd: backendDir,
      env: backendEnv,
      // Unauthenticated, so it answers 401 as soon as Django is up.
      url: `${apiURL}/api/auth/me`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      // runserver logs every request to stderr; E2E_SERVER_LOG=1 shows them.
      stdout: 'ignore',
      stderr: process.env.E2E_SERVER_LOG ? 'pipe' : 'ignore',
    },
    {
      // The dev server's /api proxy is pointed at the test backend, not :8000.
      command: `npx vite --host 127.0.0.1 --port ${webPort} --strictPort`,
      env: { ...process.env, VITE_PROXY_TARGET: apiURL },
      url: webURL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
