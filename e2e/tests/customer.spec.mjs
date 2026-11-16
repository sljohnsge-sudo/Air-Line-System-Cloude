// Customer site (tickandbook.com) — read-only smoke checks. No search, no
// flight choice, no booking, no payment, no sign-in (see playwright.config.mjs).
import { test, expect } from '@playwright/test';

test.describe('customer site', () => {
  test('home page opens with the flight search form', async ({ page }) => {
    const res = await page.goto('/');
    expect(res?.status(), 'home page HTTP status').toBeLessThan(400);
    await expect(page).toHaveTitle(/Tick and Book/);
    // Only checks the button is there — it is never pressed.
    await expect(page.getByRole('button', { name: 'Search Live Flights' })).toBeVisible();
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
