// Admin site (admin.tickandbook.com) — read-only smoke checks. Never signs
// in with a real account, never changes a setting (see playwright.config.mjs).
import { test, expect } from '@playwright/test';
import { ADMIN_URL } from './sites.mjs';

test.use({ baseURL: ADMIN_URL });

test.describe('admin site', () => {
  test('opens on the staff sign-in', async ({ page }) => {
    const res = await page.goto('/');
    expect(res?.status(), 'admin site HTTP status').toBeLessThan(400);
    await expect(page.getByRole('heading', { name: 'Admin Login' })).toBeVisible();
    await expect(page.getByText('Staff Admin')).toBeVisible();
  });

  test('has no customer pages: /check-ticket goes to the staff sign-in', async ({ page }) => {
    await page.goto('/check-ticket');
    await expect(page.getByRole('heading', { name: 'Admin Login' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search Live Flights' })).toHaveCount(0);
  });

  test('tells search engines not to index it', async ({ request }) => {
    const res = await request.get('/');
    test.skip(!/nginx/i.test(res.headers()['server'] || ''), 'headers come from nginx (deployed sites only)');
    expect(res.headers()['x-robots-tag'] || '').toContain('noindex');
    expect(res.headers()['x-frame-options']).toBe('DENY');
  });

  test('no search, booking, payment or ticketing on the admin site', async ({ request }) => {
    // Every call below must be refused with 404 before it reaches any logic;
    // an empty body is sent on purpose.
    for (const path of ['/api/flights/search', '/api/bookings/confirm', '/api/payments/init',
                        '/api/bookings/ZZSMOKE/issue-ticket', '/api/customers/login']) {
      const res = await request.post(path, { data: {} });
      expect(res.status(), `${path} must be 404 on the admin site`).toBe(404);
    }
  });

  test('staff routes need a sign-in', async ({ request }) => {
    const res = await request.get('/api/admin/go-live-switches');
    expect(res.status()).toBe(401);
    // A made-up username: one wrong attempt per run (the limit is 5 per 15 min).
    const login = await request.post('/api/admin/login', { data: { username: 'smoke-check-no-such-user', password: 'not-a-password' } });
    expect(login.status()).toBe(401);
  });
});
