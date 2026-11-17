// Customer site (tickandbook.com) — read-only smoke checks. No flight choice,
// no booking, no payment, no sign-in (see playwright.config.mjs). One live
// search only when SMOKE_SEARCH=1.
import { test, expect } from '@playwright/test';
import { SITE_URL } from './sites.mjs';

test.use({ baseURL: SITE_URL });

test.describe('customer site', () => {
  test('home page opens with the flight search form', async ({ page }) => {
    const res = await page.goto('/');
    expect(res?.status(), 'home page HTTP status').toBeLessThan(400);
    await expect(page).toHaveTitle(/Tick and Book/);
    // Only checks the button is there — it is never pressed.
    await expect(page.getByRole('button', { name: 'Search Live Flights' })).toBeVisible();
  });

  // OPTIONAL, read-only: one live flight search with the form's own default
  // route, to confirm Travelport answers through the website. Off unless
  // SMOKE_SEARCH=1 (on live every search counts toward Travelport's
  // look-to-book ratio). It never presses "Select Flight": choosing a flight,
  // booking and paying are only ever done by a person.
  test('flight search returns results (SMOKE_SEARCH=1 only)', async ({ page }) => {
    test.skip(process.env.SMOKE_SEARCH !== '1', 'set SMOKE_SEARCH=1 to run one live search');
    test.setTimeout(150_000);
    await page.goto('/');
    await page.getByRole('button', { name: 'Search Live Flights' }).click();
    const results = page.getByRole('button', { name: 'Select Flight' }).first();
    const none = page.getByText(/No flights found/i).first();
    await expect(results.or(none)).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText(/Search failed|Travelport search error/i)).toHaveCount(0);
  });

  test('has no admin pages: /admin goes back to Home', async ({ page }) => {
    await page.goto('/admin');
    await expect(page).not.toHaveURL(/\/admin/);
    await expect(page.getByText('Admin Login')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Search Live Flights' })).toBeVisible();
  });

  test('customer sign-in page offers Google, Microsoft and an account', async ({ page }) => {
    await page.goto('/account');
    await expect(page.getByRole('heading', { name: 'Customer Sign In' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Continue with Google/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Continue with Microsoft/ })).toBeVisible();
  });

  test('security headers are sent', async ({ request }) => {
    const res = await request.get('/');
    // Set by the site's nginx; the local development server doesn't send them.
    test.skip(!/nginx/i.test(res.headers()['server'] || ''), 'headers come from nginx (deployed sites only)');
    expect(res.headers()['x-content-type-options']).toBe('nosniff');
    expect(res.headers()['x-frame-options']).toBeTruthy();
  });

  test('public API answers', async ({ request }) => {
    const flags = await request.get('/api/feature-flags');
    expect(flags.status()).toBe(200);
    const texts = await request.get('/api/site-content');
    expect(texts.status()).toBe(200);
    expect(await texts.json()).toHaveProperty('review_passenger_confirmation');
  });

  test('staff routes do not exist on the customer site', async ({ request }) => {
    for (const path of ['/api/admin/go-live-switches', '/api/bookings/history', '/api/analytics/overview', '/api/admin/admins']) {
      const res = await request.get(path);
      expect(res.status(), `${path} must be 404 on the customer site`).toBe(404);
    }
    const login = await request.post('/api/admin/login', { data: { username: 'smoke-check', password: 'not-a-password' } });
    expect(login.status(), 'staff login must not exist on the customer site').toBe(404);
  });

  test('API route list is not reachable through the website', async ({ request }) => {
    // Only /api/ and /uploads/ reach the backend; /docs and /openapi.json
    // must come back as the website itself, never the API's route list.
    for (const path of ['/docs', '/openapi.json', '/api/docs', '/api/openapi.json']) {
      const res = await request.get(path);
      const body = await res.text();
      expect(body, `${path} must not show the API route list`).not.toMatch(/swagger-ui|"openapi"\s*:/i);
    }
  });
});
