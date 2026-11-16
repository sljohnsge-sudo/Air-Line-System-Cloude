// Tick and Book — post-deploy smoke checks (16/11/2026).
//
// READ-ONLY BY DESIGN: these checks open pages and call a few API routes to
// confirm both sites are up and separated correctly. They never search for
// flights, choose a flight, create a booking, pay, issue a ticket or sign in
// with a real account (Tick and Book rule: bookings are only ever made by a
// person, by hand).
//
// Addresses (defaults = this PC's local sites):
//   SITE_URL   customer site   e.g. https://tickandbook.com   (default http://localhost:5176)
//   ADMIN_URL  admin site      e.g. https://admin.tickandbook.com (default http://localhost:5177)
//
// Run with Docker (no install needed): deploy/run-smoke.ps1 or deploy/run-smoke.sh
// Or with Node: cd e2e && npm install && npx playwright install chromium && npx playwright test
import { defineConfig, devices } from '@playwright/test';

const SITE_URL = (process.env.SITE_URL || 'http://localhost:5176').replace(/\/$/, '');
const ADMIN_URL = (process.env.ADMIN_URL || 'http://localhost:5177').replace(/\/$/, '');

export default defineConfig({
  testDir: './tests',
  timeout: 45_000,
  expect: { timeout: 15_000 },
  retries: 1,
  workers: 2,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  outputDir: 'test-results',
  use: {
    ...devices['Desktop Chrome'],
    ignoreHTTPSErrors: !!process.env.SMOKE_IGNORE_HTTPS_ERRORS,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'customer-site', testMatch: /customer\.spec\.mjs/, use: { baseURL: SITE_URL } },
    { name: 'admin-site', testMatch: /admin\.spec\.mjs/, use: { baseURL: ADMIN_URL } },
  ],
});
