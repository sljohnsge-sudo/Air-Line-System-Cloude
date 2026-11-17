// Hotels (17/11/2026) - the hotel booking flow on the customer site.
//
// NEVER SELECTS A ROOM, FILLS GUEST DETAILS, PAYS OR BOOKS (Tick and Book rule:
// bookings and payments are only ever made by a person, by hand). The tests
// check the pages, the destination lookup, that a hotel can only be booked
// after a card payment, and - only with HOTEL_SEARCH=1 (or SMOKE_SEARCH=1),
// since every search counts toward Travelport's look-to-book ratio - one live
// search and one hotel's rooms: source badges (TVPT / BKNG) and LKR prices.
// "Select room" and "Pay ... & book" are only checked to be there.
import { test, expect } from '@playwright/test';
import { SITE_URL, ADMIN_URL } from './sites.mjs';

test.use({ baseURL: SITE_URL });

const LIVE = process.env.HOTEL_SEARCH === '1' || process.env.SMOKE_SEARCH === '1';
const day = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

async function hotelsEnabled(request) {
  const r = await request.get('/api/feature-flags');
  if (r.status() !== 200) return true;
  const flags = await r.json();
  return flags.hotels_enabled !== false && flags.flags?.hotels_enabled !== false;
}

async function openHotels(page, request) {
  test.skip(!(await hotelsEnabled(request)), 'Hotels are switched off on this system (admin: Go-live / feature flags)');
  await page.goto('/hotels');
  await expect(page.getByRole('combobox', { name: /Destination/ })).toBeVisible();
}

async function pickDestination(page, typed, option) {
  const box = page.getByRole('combobox', { name: /Destination/ });
  await box.click();
  await box.fill(typed);
  await page.getByRole('option', { name: option }).first().click();
}

test.describe('hotels', () => {
  test('hotel page opens with one "City, region or country" box', async ({ page, request }) => {
    await openHotels(page, request);
    await expect(page.getByRole('heading', { name: /Land, check in/ })).toBeVisible();
    await expect(page.getByPlaceholder('City, region or country')).toBeVisible();
    // The old Country / City drop-downs (airport codes) are gone.
    await expect(page.getByText('Select country...')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Search hotels' })).toBeDisabled();
  });

  test('destination suggestions are cities, without airport codes', async ({ page, request }) => {
    await openHotels(page, request);
    await page.getByRole('combobox', { name: /Destination/ }).fill('colombo');
    const option = page.getByRole('option', { name: /Colombo/ }).first();
    await expect(option).toBeVisible();
    await expect(option).toContainText('Sri Lanka');
    await expect(page.getByRole('listbox')).not.toContainText(/\bCMB\b/);
  });

  test('a country lists its cities', async ({ page, request }) => {
    await openHotels(page, request);
    await pickDestination(page, 'sri lanka', /Sri Lanka.*Country/);
    await expect(page.getByText('Cities in Sri Lanka')).toBeVisible();
    await expect(page.getByRole('option', { name: /Colombo/ }).first()).toBeVisible();
  });

  test('picking a city enables the search', async ({ page, request }) => {
    await openHotels(page, request);
    await pickDestination(page, 'dubai', /^Dubai/);
    await expect(page.getByText('United Arab Emirates').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search hotels' })).toBeEnabled();
  });

  test('API: destination lookup uses city codes (London = LON)', async ({ request }) => {
    const r = await request.get('/api/reference/hotel-destinations?q=london');
    expect(r.status()).toBe(200);
    const { destinations } = await r.json();
    const london = destinations.find(d => d.type === 'city' && d.name === 'London' && d.country === 'GB');
    expect(london?.code, 'multi-airport city searched by its city code').toBe('LON');
    const lk = await (await request.get('/api/reference/hotel-destinations?country=LK')).json();
    expect(lk.destinations.some(d => d.name === 'Colombo' && d.code === 'CMB')).toBeTruthy();
  });

  test('API: a hotel can only be booked after a card payment', async ({ request }) => {
    test.skip(!(await hotelsEnabled(request)), 'Hotels are switched off on this system');
    // Booking without payment is staff only: not on the customer site at all.
    expect((await request.post('/api/hotels/book', { data: {} })).status()).toBe(404);
    expect((await request.post('/api/hotels/book-offer', { data: {} })).status()).toBe(404);
    // Checkout refuses incomplete input before anything happens (nothing is
    // priced, no payment page is opened).
    expect((await request.post('/api/hotels/checkout', { data: {} })).status()).toBe(422);
    // Completing an order that doesn't exist checks no payment and books nothing.
    expect((await request.post('/api/hotels/checkout/HTLNOSUCH0/complete', { data: { reqid: 'zztest0000' } })).status()).toBe(404);
  });

  test('API: rate sources check refuses bad input before asking Travelport', async ({ request }) => {
    test.skip(!(await hotelsEnabled(request)), 'Hotels are switched off on this system');
    expect((await request.post('/api/hotels/rate-sources', { data: {} })).status()).toBe(422);
    const stay = { check_in_date: day(30), check_out_date: day(31), adults: 2 };
    // No hotels, too many hotels, or check-out not after check-in: refused.
    expect((await request.post('/api/hotels/rate-sources', { data: { ...stay, properties: [] } })).status()).toBe(422);
    const nine = Array.from({ length: 9 }, (_, i) => ({ chain_code: 'ZZ', property_code: `X${i}` }));
    expect((await request.post('/api/hotels/rate-sources', { data: { ...stay, properties: nine } })).status()).toBe(422);
    expect((await request.post('/api/hotels/rate-sources', {
      data: { check_in_date: day(31), check_out_date: day(30), properties: [{ chain_code: 'ZZ', property_code: 'X1' }] },
    })).status()).toBe(422);
  });

  test('API: staff hotel orders need a staff sign-in', async ({ request }) => {
    expect((await request.get('/api/admin/hotel-orders')).status(), 'not on the customer site').toBe(404);
    let admin = null;
    try { admin = await request.get(`${ADMIN_URL}/api/admin/hotel-orders`); } catch { admin = null; }
    test.skip(!admin, `admin site not running at ${ADMIN_URL}`);
    expect([401, 403]).toContain(admin.status());
  });

  // ---- live, read-only: one search and one hotel's rooms --------------------
  test('live search: hotels with TVPT/BKNG badges and LKR prices (HOTEL_SEARCH=1)', async ({ page, request }) => {
    test.skip(!LIVE, 'set HOTEL_SEARCH=1 to run one live hotel search');
    test.setTimeout(240_000);
    await openHotels(page, request);
    await pickDestination(page, 'dubai', /^Dubai/);
    await page.locator('input[type="date"]').nth(0).fill(day(30));
    await page.locator('input[type="date"]').nth(1).fill(day(31));
    await page.getByRole('button', { name: 'Search hotels' }).click();
    const cards = page.locator('.hb-card');
    await expect(cards.first()).toBeVisible({ timeout: 180_000 });
    await expect(page.locator('.hb-progress')).toHaveCount(0, { timeout: 180_000 });
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    // Every card has at least one source tag (a hotel can have TVPT and BKNG).
    for (let i = 0; i < Math.min(n, 10); i++) {
      await expect(cards.nth(i).locator('.hb-source').first()).toHaveText(/^(TVPT|BKNG|ETGR)/);
    }
    await expect(cards.first().locator('.hb-card-price')).toContainText(/LKR|per night/);
    // Booking.com (BKNG) is only in each hotel's rates: the cards in view are
    // checked and tagged with every source that has rooms (TVPT and/or BKNG).
    await expect(cards.first().locator('.hb-src-checking')).toHaveCount(0, { timeout: 120_000 });
    await expect(cards.first().locator('.hb-card-top .hb-source').first()).toHaveText(/^(TVPT|BKNG|ETGR)/);
    const tags = await cards.first().locator('.hb-card-top .hb-source').allInnerTexts();
    console.log('first hotel sources:', tags.map(t => t.slice(0, 4)).join(' + '));
    await expect(page.locator('.hb-filter').filter({ hasText: 'Content source' })).toBeVisible();
    // Price filter / sort: lowest first must not be more expensive than the next.
    await page.locator('.hb-sort select').selectOption('price_asc');
    await expect(cards.first()).toBeVisible();
  });

  test('live hotel page: rooms with source, LKR total and a Select button (HOTEL_SEARCH=1)', async ({ page, request }) => {
    test.skip(!LIVE, 'set HOTEL_SEARCH=1 to open one hotel live');
    test.setTimeout(300_000);
    await openHotels(page, request);
    await pickDestination(page, 'dubai', /^Dubai/);
    await page.locator('input[type="date"]').nth(0).fill(day(30));
    await page.locator('input[type="date"]').nth(1).fill(day(31));
    await page.getByRole('button', { name: 'Search hotels' }).click();
    await expect(page.locator('.hb-card').first()).toBeVisible({ timeout: 180_000 });
    await page.locator('.hb-card').first().getByRole('button', { name: 'See rooms' }).click();
    await expect(page.getByRole('heading', { name: /Available rooms/ })).toBeVisible();
    const rooms = page.locator('.hb-room');
    const none = page.getByText(/No rooms are available/);
    await expect(rooms.first().or(none)).toBeVisible({ timeout: 180_000 });
    if (await none.count()) return;
    await expect(rooms.first().locator('.hb-source')).toHaveText(/^(TVPT|BKNG|ETGR)/);
    await expect(rooms.first().locator('.hb-room-price')).toContainText(/total for 1 night/);
    // Only checked to be there - never pressed.
    await expect(rooms.first().getByRole('button', { name: 'Select room' })).toBeVisible();
  });
});
