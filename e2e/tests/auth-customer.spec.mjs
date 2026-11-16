// Sign-in tests for the customer website and its API (16/11/2026).
// Never books, pays or tickets. Wrong-password attempts use made-up emails,
// one each, well under the limit (5 per email in 15 minutes). The tests that
// need an account use the temporary test customer from
// e2e/tools/auth_accounts.py and are skipped when it isn't there (server).
import { test, expect } from '@playwright/test';
import { testAccounts, forgedToken, tamperedToken, tokenPayload, bearer } from './auth-helpers.mjs';

const CUSTOMER_APIS = ['/api/customers/me/profile', '/api/customers/me/bookings', '/api/customers/me/saved-travellers',
  '/api/customers/me/loyalty', '/api/customers/me/interests', '/api/customers/me/cancellation-requests'];
const run = Date.now().toString(36);

test.describe('sign-in: website and its API', () => {
  test('customer pages need a sign-in (no token: 401)', async ({ request }) => {
    for (const p of CUSTOMER_APIS) {
      const res = await request.get(p);
      expect(res.status(), `${p} without a token`).toBe(401);
    }
  });

  test('made-up and forged tokens are refused (401)', async ({ request }) => {
    const tokens = {
      'garbage': 'abc.def.ghi',
      'signed with a wrong key': forgedToken({ sub: 'someone@example.com', role: 'customer', customer_id: 1 }),
      'alg "none" (no signature)': forgedToken({ sub: 'someone@example.com', role: 'customer', customer_id: 1 }, { alg: 'none' }),
      'expired + wrong key': forgedToken({ sub: 'x@example.com', role: 'customer', customer_id: 1, exp: 1 }),
      'staff role + wrong key': forgedToken({ sub: 'admin', role: 'admin', admin_id: 1 }),
    };
    for (const [name, t] of Object.entries(tokens)) {
      for (const p of ['/api/customers/me/profile', '/api/customers/me/bookings']) {
        const res = await request.get(p, { headers: bearer(t) });
        expect(res.status(), `${p} with a ${name} token`).toBe(401);
      }
    }
    const basic = await request.get('/api/customers/me/profile', { headers: { Authorization: 'Basic ' + Buffer.from('a:b').toString('base64') } });
    expect([401, 403], 'Basic auth instead of a token').toContain(basic.status());
  });

  test('wrong password: refused, same message whether or not the email exists', async ({ request }) => {
    const res = await request.post('/api/customers/login', { data: { email: `no-such-${run}@example.com`, password: 'wrong-password-1' } });
    expect(res.status()).toBe(401);
    expect((await res.json()).detail).toBe('Invalid email or password');
  });

  test('sign-up refuses bad details (no account is created)', async ({ request }) => {
    const bad = [
      { email: 'not-an-email', password: 'long-enough-1', full_name: 'Test Person' },
      { email: `short-${run}@example.com`, password: '123', full_name: 'Test Person' },
      { email: `noname-${run}@example.com`, password: 'long-enough-1', full_name: '' },
    ];
    for (const body of bad) {
      const res = await request.post('/api/customers/register', { data: body });
      expect(res.status(), `sign-up with ${JSON.stringify({ ...body, password: '***' })}`).toBe(422);
    }
  });

  for (const [provider, host] of [['google', 'accounts.google.com'], ['microsoft', 'login.microsoftonline.com']]) {
    test(`${provider} sign-in starts at ${host} with a one-time state`, async ({ request }) => {
      const res = await request.get(`/api/customers/oauth/${provider}/start`, { maxRedirects: 0 });
      expect(res.status(), 'a redirect').toBeGreaterThanOrEqual(300);
      expect(res.status()).toBeLessThan(400);
      const to = new URL(res.headers()['location']);
      expect(to.host, `goes to ${provider} (not "not configured")`).toBe(host);
      expect(to.searchParams.get('state'), 'one-time state').toBeTruthy();
      expect(to.searchParams.get('client_id')).toBeTruthy();
      expect(to.searchParams.get('redirect_uri') || '').toMatch(new RegExp(`/api/customers/oauth/${provider}/callback$`));
    });
  }

  test('sign-in return with a made-up code or state gives no token', async ({ request }) => {
    for (const q of ['code=fake&state=fake', 'code=fake', 'error=access_denied&state=x', '']) {
      const res = await request.get(`/api/customers/oauth/google/callback?${q}`, { maxRedirects: 0 });
      const to = res.headers()['location'] || '';
      expect(to, `callback ?${q}`).toContain('oauth_error=failed');
      expect(to).not.toContain('oauth_token');
    }
    const unknown = await request.get('/api/customers/oauth/facebook/start', { maxRedirects: 0 });
    expect(unknown.status(), 'unknown provider').toBe(404);
  });

  test('other websites cannot use the API from a browser (CORS)', async ({ request }) => {
    const evil = 'https://evil.example.com';
    const res = await request.get('/api/feature-flags', { headers: { Origin: evil } });
    expect(res.headers()['access-control-allow-origin'] || '').not.toBe(evil);
    expect(res.headers()['access-control-allow-origin'] || '').not.toBe('*');
    const pre = await request.fetch('/api/customers/login', { method: 'OPTIONS',
      headers: { Origin: evil, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type' } });
    expect(pre.headers()['access-control-allow-origin'] || '', 'pre-flight from another site').not.toBe(evil);
  });

  test.describe('with the temporary test customer (laptop only)', () => {
    const acc = testAccounts();
    test.skip(!acc, 'no test accounts (e2e/tools/auth_accounts.py create)');

    test('signs in and sees only their own account', async ({ request }) => {
      const res = await request.post('/api/customers/login', { data: { email: acc.customer.email, password: acc.customer.password } });
      expect(res.status()).toBe(200);
      const { access_token } = await res.json();
      const p = tokenPayload(access_token);
      expect(p.role).toBe('customer');
      expect(p.customer_id).toBe(acc.customer.id);
      expect(p.exp * 1000, 'the token expires').toBeGreaterThan(Date.now());
      expect(JSON.stringify(p), 'no password in the token').not.toContain(acc.customer.password);
      // A brand-new account: its own (empty) profile and no bookings, so the
      // token opens this customer's data and nobody else's.
      const me = await request.get('/api/customers/me/profile', { headers: bearer(access_token) });
      expect(me.status()).toBe(200);
      expect((await me.json()).travellers).toEqual([]);
      const bookings = await request.get('/api/customers/me/bookings', { headers: bearer(access_token) });
      expect(bookings.status()).toBe(200);
      const list = await bookings.json();
      expect(Array.isArray(list) ? list : (list.bookings || []), 'a new account has no bookings').toHaveLength(0);
    });

    test('a real token with its contents changed is refused', async ({ request }) => {
      const res = await request.post('/api/customers/login', { data: { email: acc.customer.email, password: acc.customer.password } });
      const { access_token } = await res.json();
      for (const change of [{ customer_id: acc.customer.id + 1 }, { role: 'admin' }, { exp: Math.floor(Date.now() / 1000) + 10 * 365 * 86400 }]) {
        const r = await request.get('/api/customers/me/profile', { headers: bearer(tamperedToken(access_token, change)) });
        expect(r.status(), `token changed: ${JSON.stringify(change)}`).toBe(401);
      }
    });

    test('wrong password for a real account: same message as an unknown email', async ({ request }) => {
      const res = await request.post('/api/customers/login', { data: { email: acc.customer.email, password: 'definitely-wrong-1' } });
      expect(res.status()).toBe(401);
      expect((await res.json()).detail).toBe('Invalid email or password');
    });

    test('in the browser: wrong password shows an error, right password signs in', async ({ page }) => {
      await page.goto('/account');
      await expect(page.getByRole('heading', { name: 'Customer Sign In' })).toBeVisible();
      const email = page.locator('input[type="email"]');
      const password = page.locator('input[type="password"]');
      await email.fill(acc.customer.email);
      await password.fill('definitely-wrong-2');
      await page.getByRole('button', { name: 'Sign In', exact: true }).last().click();
      await expect(page.getByText(/Invalid email or password/i)).toBeVisible();
      await password.fill(acc.customer.password);
      await page.getByRole('button', { name: 'Sign In', exact: true }).last().click();
      await expect(page.getByRole('heading', { name: 'Customer Sign In' })).toHaveCount(0, { timeout: 15_000 });
      await expect(page.getByText(/E2E Test Customer|E2E/).first()).toBeVisible();
    });
  });
});
