// Sign-in tests for the admin site, the admin API and staff roles (16/11/2026).
// Read-only: only GET requests and sign-ins, never a change to a setting,
// booking or user. Wrong passwords use made-up usernames (limit: 5 per
// account, 20 per connection in 15 minutes). The role tests use the temporary
// accounts from e2e/tools/auth_accounts.py and are skipped without them.
import { test, expect, request as pwRequest } from '@playwright/test';
import { testAccounts, forgedToken, tamperedToken, tokenPayload, bearer } from './auth-helpers.mjs';

const SITE_URL = (process.env.SITE_URL || 'http://localhost:5176').replace(/\/$/, '');
const STAFF_APIS = ['/api/admin/go-live-switches', '/api/admin/pricing-settings', '/api/admin/admins',
  '/api/bookings/history', '/api/admin/reports/summary'];
const run = Date.now().toString(36);

async function staffLogin(request, acc) {
  const res = await request.post('/api/admin/login', { data: { username: acc.username, password: acc.password } });
  return { status: res.status(), body: res.ok() ? await res.json() : await res.json().catch(() => ({})) };
}

test.describe('sign-in: admin site and admin API', () => {
  test('staff and analytics APIs need a sign-in (no token: 401)', async ({ request }) => {
    for (const p of [...STAFF_APIS, '/api/analytics/me']) {
      const res = await request.get(p);
      expect(res.status(), `${p} without a token`).toBe(401);
    }
  });

  test('made-up and forged staff tokens are refused (401)', async ({ request }) => {
    const tokens = {
      'garbage': 'abc.def.ghi',
      'Admin role signed with a wrong key': forgedToken({ sub: 'admin', role: 'admin', staff_role: 'admin', admin_id: 1 }),
      'alg "none" (no signature)': forgedToken({ sub: 'admin', role: 'admin', staff_role: 'admin', admin_id: 1 }, { alg: 'none' }),
      'Analytics role signed with a wrong key': forgedToken({ sub: 'a', role: 'analytics', staff_role: 'analytics', admin_id: 1 }),
    };
    for (const [name, t] of Object.entries(tokens)) {
      for (const p of ['/api/admin/go-live-switches', '/api/admin/admins', '/api/analytics/me']) {
        const res = await request.get(p, { headers: bearer(t) });
        expect(res.status(), `${p} with a ${name} token`).toBe(401);
      }
    }
  });

  test('wrong password: refused with the same message', async ({ request }) => {
    const res = await request.post('/api/admin/login', { data: { username: `no-such-staff-${run}`, password: 'wrong-password-1' } });
    expect(res.status()).toBe(401);
    expect((await res.json()).detail).toBe('Invalid username or password');
  });

  test('sign-in page: username and password only, no customer sign-in', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Admin Login' })).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole('button', { name: /Continue with Google/ })).toHaveCount(0);
    const cust = await page.request.post('/api/customers/login', { data: { email: `x-${run}@example.com`, password: 'x-password-1' } });
    expect(cust.status(), 'customer sign-in must not exist on the admin site').toBe(404);
  });

  test.describe('roles, with the temporary staff accounts (laptop only)', () => {
    const acc = testAccounts();
    test.skip(!acc, 'no test accounts (e2e/tools/auth_accounts.py create)');

    // What each role may open. 200 = allowed, 403 = signed in but not allowed.
    const MATRIX = {
      admin:           { portal: 200, users: 200, analytics: 200, analyticsLogin: 200 },
      staff:           { portal: 200, users: 403, analytics: 403, analyticsLogin: 403 },
      analytics:       { portal: 403, users: 403, analytics: 200, analyticsLogin: 200 },
      staff_analytics: { portal: 200, users: 403, analytics: 200, analyticsLogin: 200 },
    };
    for (const [role, want] of Object.entries(MATRIX)) {
      test(`${role}: signs in and reaches only what the role allows`, async ({ request }) => {
        const { status, body } = await staffLogin(request, acc.staff[role]);
        expect(status, 'sign-in').toBe(200);
        expect(body.staff_role).toBe(role);
        const t = body.access_token;
        expect(tokenPayload(t).staff_role).toBe(role);
        const got = {
          portal: (await request.get('/api/admin/go-live-switches', { headers: bearer(t) })).status(),
          users: (await request.get('/api/admin/admins', { headers: bearer(t) })).status(),
          analytics: (await request.get('/api/analytics/me', { headers: bearer(t) })).status(),
          analyticsLogin: (await request.post('/api/analytics/login', { data: { username: acc.staff[role].username, password: acc.staff[role].password } })).status(),
        };
        expect(got).toEqual(want);
      });
    }

    test('a disabled account cannot sign in', async ({ request }) => {
      const { status, body } = await staffLogin(request, acc.staff.disabled);
      expect(status).toBe(403);
      expect(body.detail || '').toMatch(/disabled/i);
    });

    test('a real staff token with its contents changed is refused', async ({ request }) => {
      const { body } = await staffLogin(request, acc.staff.staff);
      for (const change of [{ staff_role: 'admin' }, { admin_id: acc.staff.admin.id }, { sub: acc.staff.admin.username }]) {
        const r = await request.get('/api/admin/admins', { headers: bearer(tamperedToken(body.access_token, change)) });
        expect(r.status(), `token changed: ${JSON.stringify(change)}`).toBe(401);
      }
      const an = await staffLogin(request, acc.staff.analytics);
      const r = await request.get('/api/admin/go-live-switches', { headers: bearer(tamperedToken(an.body.access_token, { role: 'admin' })) });
      expect(r.status(), 'analytics token changed to role admin').toBe(401);
    });

    test('customer and staff tokens only work on their own site', async ({ request }) => {
      // A real customer token from the website must not open the admin API,
      // and a real staff token must not open customer pages on the website.
      const site = await pwRequest.newContext({ baseURL: SITE_URL });
      try {
        const cust = await site.post('/api/customers/login', { data: { email: acc.customer.email, password: acc.customer.password } });
        expect(cust.status(), `customer sign-in on the website (${SITE_URL})`).toBe(200);
        const ct = (await cust.json()).access_token;
        for (const p of ['/api/admin/go-live-switches', '/api/admin/admins', '/api/analytics/me']) {
          expect((await request.get(p, { headers: bearer(ct) })).status(), `customer token on admin ${p}`).toBe(401);
        }
        const { body } = await staffLogin(request, acc.staff.admin);
        for (const p of ['/api/customers/me/profile', '/api/customers/me/bookings']) {
          expect([401, 403], `Admin staff token on website ${p}`).toContain((await site.get(p, { headers: bearer(body.access_token) })).status());
        }
      } finally {
        await site.dispose();
      }
    });

    test('in the browser: wrong password shows an error, Admin signs in and out', async ({ page }) => {
      await page.goto('/');
      const user = page.locator('input.form-input').first();
      const pass = page.locator('input[type="password"]');
      await user.fill(acc.staff.admin.username);
      await pass.fill('definitely-wrong-3');
      await page.getByRole('button', { name: 'Sign In', exact: true }).click();
      await expect(page.locator('.error-banner')).toContainText(/Invalid username or password/i);
      await pass.fill(acc.staff.admin.password);
      await page.getByRole('button', { name: 'Sign In', exact: true }).click();
      await expect(page.getByText('Tick and Book · Admin Console')).toBeVisible({ timeout: 20_000 });
      await page.getByRole('button', { name: /Log out/ }).click();
      await expect(page.getByRole('heading', { name: 'Admin Login' })).toBeVisible();
    });
  });
});
