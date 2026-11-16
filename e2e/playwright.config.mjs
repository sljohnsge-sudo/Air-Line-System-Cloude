// Tick and Book - Playwright tests (16/11/2026).
//
// NEVER BOOKS: no test chooses a flight, creates a booking (PNR), pays or
// issues a ticket (Tick and Book rule: bookings are only ever made by a
// person, by hand).
//
//   customer-site  smoke checks + sign-in tests for the website and its API
//   admin-site     smoke checks + sign-in and staff-role tests for the admin site
//   cmb-routes     CMB route test: 52 destinations / 448 flights from the
//                  7 Nov Excel, Travelport SEARCH ONLY (ROUTE_TESTS=1)
//
// Sign-in tests with real accounts use temporary test accounts made by
// e2e/tools/auth_accounts.py on the laptop (skipped when they don't exist).
// One live search in the smoke checks only with SMOKE_SEARCH=1.
//
// Addresses (defaults = this PC's local sites):
//   SITE_URL   customer site   e.g. https://tickandbook.com   (default http://localhost:5176)
//   ADMIN_URL  admin site      e.g. https://admin.tickandbook.com (default http://localhost:5177)
//
// Run with Docker (no install needed): deploy/run-smoke.ps1 (-Routes for the
// CMB route test), deploy/playwright-ui.bat for the interface.
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
    { name: 'customer-site', testMatch: /(customer|auth-customer)\.spec\.mjs/, use: { baseURL: SITE_URL } },
    { name: 'admin-site', testMatch: /(admin|auth-admin)\.spec\.mjs/, use: { baseURL: ADMIN_URL } },
    // A missing airline is a result, not a hiccup: no retries (a retry would
    // only re-read the saved search).
    { name: 'cmb-routes', testMatch: /cmb-routes\.spec\.mjs/, retries: 0, use: { baseURL: SITE_URL, trace: 'off', screenshot: 'off' } },
  ],
});
