// Tick and Book - Playwright tests (16/11/2026).
//
// NEVER BOOKS: no test chooses a flight, creates a booking (PNR), pays or
// issues a ticket (Tick and Book rule: bookings are only ever made by a
// person, by hand).
//
// One group with every test (the Playwright interface shows only the first
// group by default, so the tests are not split into groups). The files, in
// the order the interface lists them:
//   01-website                    smoke checks for the customer website
//   02-website-sign-in            sign-in tests for the website and its API
//   03-admin-site                 smoke checks for the admin site
//   04-admin-sign-in-and-roles    sign-in and staff-role tests for the admin site
//   05-cmb-routes                 CMB route test: 52 destinations / 448 flights
//                                 from the 7 Nov Excel, Travelport SEARCH ONLY
//                                 (runs only with ROUTE_TESTS=1)
//   06-api                        every backend API (218), one test each, with
//                                 the request and reply attached; booking,
//                                 payment and ticketing steps are never called
// Each file sets its own site (test.use baseURL, see tests/sites.mjs).
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

export default defineConfig({
  testDir: './tests',
  testMatch: /\.spec\.mjs$/,
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
});
