// CMB route test (16/11/2026) - SEARCH ONLY, Travelport only.
//
// The airlines, countries and destinations come from the 7 Nov booking test
// (e2e/data/cmb-routes-2026-11-07.json: 52 destinations, 448 flights, 361 GDS
// + 87 NDC). For each destination the website's own search is run once:
// Colombo (CMB), one-way, 1 adult, 27 Nov 2026. One test per flight then
// checks that Travelport offers that airline on that route with that content
// (GDS or NDC) and records the fare, bags, carry-on, seat choice, meals and the
// change/cancel fees the search shows.
//
// NOTHING IS SELECTED, BOOKED, PAID OR TICKETED: the only call is
// POST /api/flights/search (Tick and Book rule: bookings are made by a person,
// by hand). Seat maps and paid extras need a started booking, so they are not
// fetched; the fare's own seat/bag/meal rules from the search are recorded.
//
// Off unless ROUTE_TESTS=1 (52 searches). ROUTE_DATE overrides the date,
// ROUTE_RUN_ID names the results folder (route-results/<id>/, one file per
// destination; a search already saved there is reused, not repeated).
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SITE_URL } from './sites.mjs';

// The website; a missing airline is a result, not a hiccup: no retries (a
// retry would only re-read the saved search), no traces or screenshots.
test.use({ baseURL: SITE_URL, trace: 'off', screenshot: 'off' });
test.describe.configure({ retries: 0 });

const here = path.dirname(fileURLToPath(import.meta.url));
const data = JSON.parse(fs.readFileSync(path.join(here, '..', 'data', 'cmb-routes-2026-11-07.json'), 'utf8'));
const ON = process.env.ROUTE_TESTS === '1';
const DATE = process.env.ROUTE_DATE || data.date;
const RUN = process.env.ROUTE_RUN_ID || `cmb-${DATE}`;
const OUT = path.join(here, '..', 'route-results', RUN);

// Destinations in the 7 Nov order, each with its expected airline/content rows.
const destinations = [];
for (const f of data.flights) {
  let d = destinations.find((x) => x.to === f.to);
  if (!d) destinations.push((d = { no: f.no, to: f.to, city: f.city, country: f.country, flights: [] }));
  d.flights.push(f);
}

const INCLUSION = (attrs, cls) => {
  const a = (attrs || []).find((x) => (x.classification || '').toLowerCase() === cls.toLowerCase());
  return a ? a.inclusion : '';
};
const bags = (fo) => (fo?.baggage_allowance || []).map((b) => `${b.type}: ${b.allowance}`).join('; ');

// Only what the report needs: no raw Travelport payloads.
function slimOffer(o) {
  const opts = (o.fare_options || []).map((fo) => ({
    brand: fo.brand_name || '', cabin: fo.cabin_class || '', price: fo.price, currency: fo.currency,
    bags: bags(fo), carry_on: INCLUSION(fo.brand_attributes, 'CarryOn'),
    seat: INCLUSION(fo.brand_attributes, 'SeatAssignment'), meals: INCLUSION(fo.brand_attributes, 'Meals'),
    change: fo.change_policy || '', cancel: fo.cancel_policy || '',
    attributes: (fo.brand_attributes || []).map((a) => `${a.classification}: ${a.inclusion}`).join('; '),
    fare_basis: (fo.fare_basis_codes || []).join(' '), booking_class: (fo.classes_of_service || []).join(' '),
  }));
  return {
    source: o.source, channel: o.fare_source, airline: o.airline, code: o.airline_code,
    flights: (o.segments || []).map((s) => s.flight_number).join(' + ') || o.flight_number,
    route: (o.segments || []).map((s) => s.departure_airport).concat([o.arrival_airport]).join('-'),
    departure: o.departure_time, arrival: o.arrival_time, duration: o.duration, stops: o.stops,
    aircraft: o.aircraft_type || '', price: o.price, currency: o.currency, bookable: o.bookable,
    seats_left: o.seats_remaining, fare_options: opts,
  };
}

async function searchOnce(request, dest) {
  const file = path.join(OUT, `${String(dest.no).padStart(2, '0')}-${dest.to}.json`);
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
  let rec;
  for (let attempt = 1; attempt <= 2; attempt++) {
    const started = Date.now();
    try {
      const res = await request.post('/api/flights/search', {
        data: { origin: data.origin, destination: dest.to, departure_date: DATE, adult_count: 1 },
        headers: { 'user-agent': 'TickAndBook route test (search only)' },
        timeout: 240_000,
      });
      const ms = Date.now() - started;
      let body = null;
      try { body = await res.json(); } catch { /* not JSON */ }
      const all = body?.flights || [];
      rec = {
        to: dest.to, date: DATE, status: res.status(), seconds: Math.round(ms / 100) / 10, attempt,
        error: res.ok() ? '' : String(body?.detail || (await res.text()).slice(0, 300)),
        other_suppliers: all.filter((o) => o.source !== 'TP').length,
        offers: all.filter((o) => o.source === 'TP').map(slimOffer),
        searched_at: new Date().toISOString(),
      };
    } catch (e) {
      rec = { to: dest.to, date: DATE, status: 0, seconds: Math.round((Date.now() - started) / 100) / 10, attempt,
              error: String(e.message || e).slice(0, 300), other_suppliers: 0, offers: [], searched_at: new Date().toISOString() };
    }
    if (rec.status === 200 || (rec.status >= 400 && rec.status < 500)) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(rec, null, 1));
  return rec;
}

const cheapest = (offers) => offers.slice().sort((a, b) => (a.price ?? 1e15) - (b.price ?? 1e15))[0];

for (const dest of destinations) {
  const gds = dest.flights.filter((f) => f.channel === 'GDS').length;
  const ndc = dest.flights.length - gds;
  test.describe(`${String(dest.no).padStart(2, '0')}. CMB → ${dest.to} ${dest.city}, ${dest.country} (${gds} GDS, ${ndc} NDC)`, () => {
    let search;
    test.beforeAll(async ({ request }) => {
      if (!ON) return;
      test.setTimeout(600_000);
      search = await searchOnce(request, dest);
    });

    for (const f of dest.flights) {
      const title = `${f.airline} (${f.code}) ${f.channel}` + (f.flight_7nov ? ` - 7 Nov flight ${f.flight_7nov}` : '');
      test(title, async ({}, testInfo) => {
        test.skip(!ON, 'route searches run only with ROUTE_TESTS=1 (52 Travelport searches)');
        const noFares = /NO OFFERS FOUND/i.test(search.error || '');
        expect(noFares ? 0 : 1, `Travelport found no fares at all on CMB→${dest.to} for ${DATE} (NO OFFERS FOUND FOR THE CHANNEL); 7 Nov: ${f.result_7nov}`).toBe(1);
        expect(search.status, `Search CMB→${dest.to} on ${DATE} failed: ${search.error}`).toBe(200);
        const mine = search.offers.filter((o) => o.code === f.code && o.channel === f.channel);
        const best = cheapest(mine);
        if (best) {
          const fo = best.fare_options[0] || {};
          const same = f.flight_7nov && best.flights.replace(/\s/g, '').split('+').includes(f.flight_7nov);
          testInfo.annotations.push(
            { type: 'Offered', description: `${mine.length} option(s); cheapest ${best.flights} (${best.route}), ${best.currency} ${best.price?.toLocaleString('en-US')}` },
            { type: 'Fare', description: `${fo.brand || '-'} | bags: ${fo.bags || '-'} | carry-on: ${fo.carry_on || '-'} | seat: ${fo.seat || '-'} | meals: ${fo.meals || '-'}` },
            { type: 'Change / cancel', description: `${fo.change || '-'} / ${fo.cancel || '-'}` },
            { type: '7 Nov flight', description: f.flight_7nov ? (same ? `${f.flight_7nov} offered again` : `${f.flight_7nov} not in the cheapest option`) : 'none (not offered on 7 Nov)' },
          );
          await testInfo.attach('offers.json', { body: JSON.stringify(mine, null, 1), contentType: 'application/json' });
        }
        expect(mine.length, `Travelport did not offer ${f.airline} ${f.channel} on CMB→${dest.to} for ${DATE} (7 Nov: ${f.result_7nov})`).toBeGreaterThan(0);
      });
    }

    test('other airlines offered (not in the 7 Nov list)', async ({}, testInfo) => {
      test.skip(!ON, 'route searches run only with ROUTE_TESTS=1');
      expect(search.status, `Search failed: ${search.error}`).toBe(200);
      const known = new Set(dest.flights.map((f) => `${f.code}|${f.channel}`));
      const extra = {};
      for (const o of search.offers) {
        const k = `${o.code}|${o.channel}`;
        if (!known.has(k)) (extra[k] ||= { airline: o.airline, code: o.code, channel: o.channel, options: 0 }).options++;
      }
      const list = Object.values(extra);
      testInfo.annotations.push({ type: 'New', description: list.length ? list.map((x) => `${x.airline} (${x.code}) ${x.channel}: ${x.options}`).join('; ') : 'none' });
      testInfo.annotations.push({ type: 'Search', description: `${search.offers.length} Travelport options in ${search.seconds}s` });
    });
  });
}
