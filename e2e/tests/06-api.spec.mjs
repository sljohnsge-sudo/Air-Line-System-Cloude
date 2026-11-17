// Every backend API, one test each (17/11/2026), from e2e/data/api-catalog.json
// (made by e2e/tools/api_catalog.py from the backend's own API list).
//
// Each test shows what was sent and what came back: open a test in the
// Playwright interface or report -> "Attachments" ("request" / "reply" for each
// step). Passwords, tokens and card fields in replies are masked.
//
// NEVER A BOOKING, PAYMENT OR TICKET (Tick and Book rule):
//   read     GET - called without sign-in (a protected one must refuse: 401),
//            then signed in with a temporary test account (Admin for staff
//            APIs, the test customer for customer APIs); must not fail (5xx)
//   guard    needs a sign-in and changes something - called WITHOUT sign-in
//            only, must be refused (401) before it does anything
//   invalid  public, changes or searches something - called with invalid input
//            only, must be refused (422) before it does anything
//   never    public booking / payment / ticketing / cancel step - not called
// and every API is checked on the other site, where it must not exist (404).
// Signed-in steps need e2e/tools/auth_accounts.py (laptop); skipped otherwise.
import { test, expect, request as pwRequest } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SITE_URL, ADMIN_URL } from './sites.mjs';
import { testAccounts, bearer } from './auth-helpers.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const catalog = JSON.parse(fs.readFileSync(path.join(here, '..', 'data', 'api-catalog.json'), 'utf8'));
const acc = testAccounts();
const HOME = { website: SITE_URL, both: SITE_URL, admin: ADMIN_URL };
const OTHER = { website: ADMIN_URL, admin: SITE_URL };
const PROTECTED = new Set(['staff', 'analytics', 'customer', 'staff-or-customer']);
const MASK = /token|password|secret|hash|cvv|cvc|card_?number|pan$|otp|code_hash/i;
// Personal details a public (no sign-in) reply must never contain.
const PERSONAL = /"(guest|passenger|customer|traveller|contact)_?(name|email|phone|mobile|passport)"\s*:\s*"[^"]+"/i;

test.describe.configure({ retries: 0, timeout: 120_000 });

// How a reply is judged:
//   switched off  503 "temporarily unavailable" - the module (hotels, visa,
//                 Amadeus, Indigo...) is turned off: a correct refusal
//   handled       5xx with a JSON {"detail": ...} - the API caught a supplier
//                 or system error (e.g. Travelport refusing a made-up code)
//   crash         any other 5xx, or no answer - a real fault: the test fails
function judge(r) {
  let detail = '';
  try { detail = JSON.parse(r.text).detail; } catch { /* not JSON */ }
  const off = r.status === 503 && /temporarily unavailable/i.test(String(detail || ''));
  const handled = !off && r.status >= 500 && typeof detail === 'string' && detail.length > 0;
  const crash = r.status === 0 || (r.status >= 500 && !off && !handled);
  return { off, handled, crash, detail };
}

function note(testInfo, label, j) {
  if (j.off) testInfo.annotations.push({ type: `${label}: switched off`, description: 'module turned off on this system (503)' });
  if (j.handled) testInfo.annotations.push({ type: `${label}: handled error`, description: j.detail });
}

let tokens = { staff: null, customer: null };
test.beforeAll(async () => {
  if (!acc) return;
  const admin = await pwRequest.newContext({ baseURL: ADMIN_URL });
  const site = await pwRequest.newContext({ baseURL: SITE_URL });
  try {
    const a = await admin.post('/api/admin/login', { data: { username: acc.staff.admin.username, password: acc.staff.admin.password } });
    if (a.ok()) tokens.staff = (await a.json()).access_token;
    const c = await site.post('/api/customers/login', { data: { email: acc.customer.email, password: acc.customer.password } });
    if (c.ok()) tokens.customer = (await c.json()).access_token;
  } finally {
    await admin.dispose();
    await site.dispose();
  }
});

function masked(v) {
  if (Array.isArray(v)) return v.slice(0, 20).map(masked).concat(v.length > 20 ? [`... ${v.length - 20} more`] : []);
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, MASK.test(k) && x ? '***' : masked(x)]));
  }
  return v;
}

async function call(base, api, { token, body, who }, testInfo, label) {
  const ctx = await pwRequest.newContext();
  const qs = new URLSearchParams(Object.entries(api.query || {}).map(([k, v]) => [k, String(v)])).toString();
  const url = base + api.url + (qs ? `?${qs}` : '');
  const opts = { method: api.method, maxRedirects: 0, timeout: 90_000, failOnStatusCode: false,
                 headers: token ? bearer(token) : {} };
  if (body !== undefined) opts.data = body;
  let status = 0, text = '', ctype = '', location = '';
  try {
    const res = await ctx.fetch(url, opts);
    status = res.status();
    ctype = res.headers()['content-type'] || '';
    location = res.headers()['location'] || '';
    text = ctype.includes('image') ? `(${(await res.body()).length} bytes of ${ctype})` : await res.text();
  } catch (e) {
    text = `request failed: ${e.message}`;
  } finally {
    await ctx.dispose();
  }
  let shown = text;
  try { shown = JSON.stringify(masked(JSON.parse(text)), null, 1); } catch { /* not JSON */ }
  if (shown.length > 8000) shown = shown.slice(0, 8000) + `\n... (${text.length} characters in all)`;
  await testInfo.attach(`${label} - request`, { contentType: 'text/plain', body:
    `${api.method} ${url}\nSigned in: ${who || 'no'}\n` + (body !== undefined ? `Body: ${JSON.stringify(body)}\n` : '') });
  await testInfo.attach(`${label} - reply`, { contentType: 'text/plain', body:
    `HTTP ${status}${ctype ? `  (${ctype.split(';')[0]})` : ''}${location ? `\nLocation: ${location.split('?')[0]}?...` : ''}\n\n${shown}` });
  testInfo.annotations.push({ type: label, description: `HTTP ${status}` });
  return { status, text };
}

const groups = {};
for (const api of catalog.apis) (groups[api.area] ||= []).push(api);

for (const [area, apis] of Object.entries(groups)) {
  test.describe(`API ${area} (${apis.length})`, () => {
    for (const api of apis) {
      const title = `${api.method} ${api.path} - ${api.name}`;
      test(title, async ({}, testInfo) => {
        testInfo.annotations.push(
          { type: 'API', description: `${api.function}() · sign-in: ${api.auth} · site: ${api.site}` },
          { type: 'What the test does', description: api.why },
        );
        test.skip(api.action === 'never', api.why);
        const home = HOME[api.site];

        await test.step('without sign-in', async () => {
          const body = api.action === 'invalid' ? [] : undefined;   // [] is never valid input
          const r = await call(home, api, { body }, testInfo, '1 without sign-in');
          const j = judge(r);
          note(testInfo, '1', j);
          expect(j.crash, `server fault: HTTP ${r.status} ${r.text.slice(0, 200)}`).toBe(false);
          if (j.off) return;
          if (api.action === 'invalid') {
            expect(r.status, 'invalid input must be refused before anything happens').toBe(422);
          } else if (PROTECTED.has(api.auth)) {
            expect(r.status, 'must be refused without sign-in').toBe(401);
          } else {
            expect(r.text, 'a public reply must not contain personal details').not.toMatch(PERSONAL);
          }
        });

        if (api.action === 'read' && PROTECTED.has(api.auth)) {
          const as = api.auth === 'customer' || (api.auth === 'staff-or-customer' && api.site !== 'admin') ? 'customer' : 'staff';
          await test.step(`signed in as the test ${as === 'staff' ? 'Admin' : 'customer'}`, async () => {
            if (!tokens[as]) {
              testInfo.annotations.push({ type: '2 signed in', description: 'not run: no test accounts (e2e/tools/auth_accounts.py create)' });
              return;
            }
            const r = await call(home, api, { token: tokens[as], who: as === 'staff' ? 'test Admin' : 'test customer' }, testInfo, '2 signed in');
            const j = judge(r);
            note(testInfo, '2', j);
            expect(j.crash, `server fault: HTTP ${r.status} ${r.text.slice(0, 200)}`).toBe(false);
            expect([401, 403], `the test ${as} must be let in`).not.toContain(r.status);
          });
        }

        if (api.site !== 'both') {
          await test.step(`not on the ${api.site === 'admin' ? 'website' : 'admin site'}`, async () => {
            const r = await call(OTHER[api.site], api, {}, testInfo, '3 other site');
            const j = judge(r);
            note(testInfo, '3', j);
            if (j.off) return;
            expect(r.status, `${api.site === 'admin' ? 'staff' : 'website'} API must not exist on the other site`).toBe(404);
          });
        }
      });
    }
  });
}

test('the API list is complete (every API in backend/main.py has a test)', async () => {
  // backend/main.py is mounted read-only next to e2e by the run scripts.
  const file = path.join(here, '..', '..', 'backend', 'main.py');
  test.skip(!fs.existsSync(file), 'backend/main.py not available here');
  const main = fs.readFileSync(file, 'utf8');
  const inCode = [...main.matchAll(/^@app\.(get|post|put|delete|patch)\("([^"]+)"/gm)].map((m) => `${m[1].toUpperCase()} ${m[2]}`);
  const inCatalog = new Set(catalog.apis.map((a) => `${a.method} ${a.path}`));
  const missing = inCode.filter((k) => !inCatalog.has(k));
  expect(missing, 'APIs without a test - run: py e2e/tools/api_catalog.py').toEqual([]);
});
