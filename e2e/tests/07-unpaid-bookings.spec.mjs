// 07 - Bookings without a ticket in the Customer Portal (17/11/2026).
//
// A booking is only a ticket once Travelport has issued one, and a ticket is
// only issued from a payment the gateway confirmed. Until then the portal must
// never show its PNR or airline reference, never give an e-ticket, invoice,
// live status or Manage My Booking for it, and never give it loyalty points;
// clicking it asks the customer to pay the ticket amount. Loyalty points are
// taken back when a ticket is cancelled, and the account is checked live
// with Travelport at sign-in.
//
// NEVER PAYS OR TICKETS: the Pay button is shown but never pressed, and no
// payment or ticket-issue API is called. Uses the test "payer" customer and
// its five test bookings from e2e/tools/auth_accounts.py (laptop only; made-up
// ZZ... PNRs that no Travelport system has). The last test asks Travelport
// (read only) about those made-up PNRs.
import { test, expect, request as pwRequest } from '@playwright/test';
import { testAccounts, bearer } from './auth-helpers.mjs';
import { SITE_URL } from './sites.mjs';

test.use({ baseURL: SITE_URL });
test.describe.configure({ mode: 'serial', retries: 0, timeout: 120_000 });

const acc = testAccounts();
const payer = acc?.payer;
const B = payer?.bookings || {};
const NO_TICKET = /no ticket yet/i;
const attach = (info, name, body) => info.attach(name, { body: JSON.stringify(body, null, 2), contentType: 'application/json' });

test('paying from My Bookings needs a customer sign-in (no token: 401)', async ({ request }) => {
  const r = await request.post('/api/customers/me/bookings/TBK1/pay');
  expect(r.status()).toBe(401);
});

test.describe('with the test payer and its test bookings (laptop only)', () => {
  test.skip(!payer, 'no test accounts (e2e/tools/auth_accounts.py create)');
  let token = '';

  test.beforeAll(async () => {
    const ctx = await pwRequest.newContext({ baseURL: SITE_URL });
    const r = await ctx.post('/api/customers/login', { data: { email: payer.email, password: payer.password } });
    expect(r.status(), 'test payer signs in').toBe(200);
    token = (await r.json()).access_token;
    await ctx.dispose();
  });

  test('My Bookings: a booking without a ticket comes without its PNR', async ({ request }, info) => {
    const res = await request.get('/api/customers/me/bookings', { headers: bearer(token) });
    expect(res.status()).toBe(200);
    const text = await res.text();
    const { bookings } = JSON.parse(text);
    await attach(info, 'My Bookings reply', bookings);
    for (const key of ['unpaid', 'expired', 'sync']) {
      expect(text, `${key}: its PNR is nowhere in the reply`).not.toContain(B[key].locator);
      expect(text, `${key}: its airline reference is nowhere in the reply`).not.toContain(B[key].airline_pnr);
    }
    const unpaid = bookings.find(b => b.pay_ref === B.unpaid.pay_ref);
    expect(unpaid, 'the unpaid booking is listed').toBeTruthy();
    expect(unpaid).toMatchObject({ locator_code: null, ticket_number: null, unpaid: true,
                                   payment_state: 'awaiting_payment', status: 'Awaiting payment', total_fare: 12345 });
    const expired = bookings.find(b => b.pay_ref === B.expired.pay_ref);
    expect(expired).toMatchObject({ locator_code: null, payment_state: 'expired' });
    // A ticketed booking is listed as before, with its PNR and ticket.
    const ticketed = bookings.find(b => b.locator_code === B.ticketed.locator);
    expect(ticketed?.ticket_number).toBe(B.ticketed.ticket);
  });

  test('no e-ticket, invoice, live status or ticket status for it (402)', async ({ request }, info) => {
    // As if the customer had the PNR from somewhere else: still nothing that works as a ticket.
    const loc = B.unpaid.locator;
    const calls = {
      'View / Print Ticket': await request.get(`/api/bookings/retrieve/${loc}?live_only=true`, { headers: bearer(token) }),
      'View / Print Ticket, not signed in': await request.get(`/api/bookings/retrieve/${loc}`),
      'Invoice': await request.get(`/api/invoice/pnr/${loc}`, { headers: bearer(token) }),
      'Live status': await request.get(`/api/bookings/${loc}/live`, { headers: bearer(token) }),
      'Check My Ticket Status': await request.get(`/api/ticket-status?query=${loc}`, { headers: bearer(token) }),
      'Check My Ticket Status, not signed in': await request.get(`/api/ticket-status?query=${loc}`),
    };
    const seen = {};
    for (const [name, r] of Object.entries(calls)) {
      const body = await r.json().catch(() => ({}));
      seen[name] = { status: r.status(), detail: body.detail };
      expect(r.status(), `${name}: refused - pay first`).toBe(402);
      expect(String(body.detail), name).toMatch(NO_TICKET);
    }
    await attach(info, 'answers', seen);
  });

  test('Pay: refused for an expired, a ticketed and someone else\'s booking (no payment page opened)', async ({ request }, info) => {
    const expired = await request.post(`/api/customers/me/bookings/${B.expired.pay_ref}/pay`, { headers: bearer(token) });
    const ticketed = await request.post(`/api/customers/me/bookings/${B.ticketed.pay_ref}/pay`, { headers: bearer(token) });
    const notMine = await request.post('/api/customers/me/bookings/TBK999999999/pay', { headers: bearer(token) });
    await attach(info, 'answers', { expired: await expired.json(), ticketed: await ticketed.json(), notMine: await notMine.json() });
    expect(expired.status()).toBe(409);
    expect((await expired.json()).detail).toMatch(/not paid in time/i);
    expect(ticketed.status()).toBe(409);
    expect((await ticketed.json()).detail).toMatch(/already has its ticket/i);
    expect(notMine.status()).toBe(404);
  });

  test('cancellation request: refused for a booking with no ticket and no payment', async ({ request }) => {
    const travel = new Date(Date.now() + 30 * 86400_000).toISOString().slice(0, 10);
    const r = await request.post('/api/cancellation-requests', {
      headers: bearer(token),
      data: { booking_locator: B.unpaid.locator, travel_date: travel, requester_name: 'E2E Test', email: payer.email,
              phone: '0770000000', all_passengers_cancelling: true },
    });
    expect(r.status()).toBe(409);
    expect((await r.json()).detail).toMatch(/nothing to cancel/i);
  });

  test('loyalty: points only for the ticket, and taken back when it was voided', async ({ request }, info) => {
    const r = await request.get('/api/customers/me/loyalty', { headers: bearer(token) });
    expect(r.status()).toBe(200);
    const l = await r.json();
    await attach(info, 'loyalty reply', { balance: l.points_balance, points_by_booking: l.points_by_booking, ledger: l.recent_transactions });
    const by = l.points_by_booking || {};
    expect(B.ticketed.points, 'the ticketed booking earned points').toBeGreaterThan(0);
    expect(by[B.ticketed.locator]).toBe(B.ticketed.points);
    expect(by[B.voided.locator] || 0, 'voided ticket: its points were taken back').toBe(0);
    const voidedLines = l.recent_transactions.filter(t => t.locator_code === B.voided.locator).map(t => t.points_change);
    expect(voidedLines.some(p => p > 0) && voidedLines.some(p => p < 0), 'earned, then removed').toBe(true);
    for (const key of ['unpaid', 'expired', 'sync']) {
      expect(l.recent_transactions.some(t => t.locator_code === B[key].locator), `${key}: no points without a ticket`).toBe(false);
    }
    expect(l.points_balance).toBe(B.ticketed.points);
  });

  test('in the browser: no PNR, a pop-up asks to pay, Manage booking only for the ticket', async ({ page }, info) => {
    // The live check at sign-in is tested on its own (last test); here it is
    // answered with "nothing changed" so the test bookings stay as they are.
    await page.route('**/api/customers/me/bookings/refresh-status', r => r.fulfill({ json: { statuses: {}, changed: false } }));
    await page.addInitScript(([t, email]) => {
      localStorage.setItem('customerToken', t);
      localStorage.setItem('customerProfile', JSON.stringify({ email, full_name: 'E2E Test Payer' }));
    }, [token, payer.email]);
    await page.goto('/account');
    const unpaidRow = page.getByRole('row', { name: /Booking without a ticket.*Awaiting payment/ }).first();
    await expect(unpaidRow).toBeVisible({ timeout: 20_000 });
    const body = await page.locator('body').innerText();
    for (const key of ['unpaid', 'expired', 'sync']) {
      expect(body, `${key}: PNR not on the page`).not.toContain(B[key].locator);
      expect(body, `${key}: airline reference not on the page`).not.toContain(B[key].airline_pnr);
    }
    expect(body, 'the ticketed booking still shows its PNR').toContain(B.ticketed.locator);
    await expect(page.getByRole('button', { name: 'Manage booking' }), 'Manage booking only for the ticketed trip').toHaveCount(1);
    await expect(unpaidRow.getByRole('button', { name: 'Pay to issue ticket' })).toBeVisible();

    await unpaidRow.click({ position: { x: 40, y: 12 } });  // the row itself (flight column)
    const dialog = page.getByRole('dialog', { name: 'Pay to issue your ticket' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('Pay the ticket amount to issue the ticket.')).toBeVisible();
    await expect(dialog.getByRole('button', { name: /^Pay LKR 12,345\.00$/ }), 'Pay button shown (never pressed)').toBeVisible();
    await info.attach('pay pop-up', { body: await page.screenshot(), contentType: 'image/png' });
    await dialog.getByRole('button', { name: 'Close' }).click();
    await expect(dialog).toHaveCount(0);

    await page.getByRole('row', { name: /Booking without a ticket.*Not paid - expired/ }).first().click({ position: { x: 40, y: 12 } });
    const expired = page.getByRole('dialog', { name: 'Booking expired - not paid' });
    await expect(expired).toBeVisible();
    await expect(expired.getByRole('button', { name: /^Pay / }), 'no Pay for an expired booking').toHaveCount(0);
    await expired.getByRole('button', { name: 'Close' }).click();
  });

  test('sign-in live check: Travelport has no such PNR, so the unpaid booking is saved as cancelled', async ({ request }, info) => {
    const r = await request.post('/api/customers/me/bookings/refresh-status', { headers: bearer(token), timeout: 110_000 });
    expect(r.status()).toBe(200);
    const live = await r.json();
    await attach(info, 'live check reply', live);
    expect(JSON.stringify(live), 'no unpaid PNR in the reply').not.toContain(B.sync.locator);
    expect(live.statuses[B.sync.pay_ref]?.status).toBe('Cancelled');
    expect(live.changed).toBe(true);
    // The made-up ticket can't be read at Travelport: the ticketed booking is left as it is.
    expect(live.statuses[B.ticketed.locator]?.status).not.toMatch(/Voided|Refunded|Cancelled/);
    const after = await (await request.get('/api/customers/me/bookings', { headers: bearer(token) })).json();
    expect(after.bookings.find(b => b.pay_ref === B.sync.pay_ref)?.payment_state).toBe('expired');
  });
});
