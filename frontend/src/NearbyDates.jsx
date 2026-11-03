// Fare Calendar on the results page (the date strip it replaced used the
// same search helpers). Prices on nearby dates: Loads by itself with the
// results: real Travelport searches (the same /api/flights/search the main
// search uses) for 3 days before and 5 days after the customer's departure
// date (never past days), same route, travellers and cabin. Round trips keep
// the customer's return date and move only the departure, like the airline
// sites do. All days are searched at the same time so they fill in together.
// Shows the cheapest live fare per day; tapping a day runs that search.
// Every day is searched live on Travelport each time the strip shows; while
// that runs, a day already found in the last 10 minutes keeps showing its
// earlier Travelport price (so picking a day doesn't flash "Checking…"), and
// is replaced by the fresh Travelport price as soon as it arrives.
// prefetchNearby() starts these searches together with the main search, so
// the strip is usually full by the time the results appear.
// Round trips also get the Fare Calendar: a departure × return grid of live
// prices (15 × 15 days around the customer's dates), searched only when opened —
// from the calendar button or by tapping a date. Travelport's API has no
// fare-calendar / flexible-date search (checked v11.37.3), so it is built
// from individual live searches.
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { fmtDateWeekday } from './dateFormat.js';
import BookingWaitLoader from './BookingWaitLoader.jsx';

const TAKEOFF = 'M2.5 19h19v2h-19v-2zm19.57-9.36c-.21-.8-1.04-1.28-1.84-1.06L14.92 10l-6.9-6.43-1.93.51 4.14 7.17-4.97 1.33-1.97-1.54-1.45.39 2.59 4.49L21 11.49c.81-.23 1.28-1.05 1.07-1.85z';
const LANDING = 'M2.5 19h19v2h-19v-2zm7.18-5.73l4.35 1.16 5.31 1.42c.8.21 1.62-.26 1.84-1.06.21-.8-.26-1.62-1.06-1.84l-5.31-1.42-2.76-9.02L10.12 2v8.28L5.15 8.95l-.93-2.32-1.45-.39v5.17l1.6.43 5.31 1.43z';
const AxisIcon = ({ d, name }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" role="img" aria-label={name}><title>{name}</title><path d={d} /></svg>
);

const BEFORE = 3;
const AFTER = 5;
const GRID = 7;   // departure × return grid: 7 days either side of each date
const GRID_PARALLEL = 10;   // calendar searches running at once
const KEEP_MS = 10 * 60 * 1000;
const seen = new Map();   // search key -> { state, price, currency, at }
const inFlight = new Map();   // search key -> pending search, so a day is never searched twice at once

const shift = (iso, days) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
const label = (iso) => fmtDateWeekday(iso);
// Box label: "13 Nov, Fri"
const short = (iso) => {
  const d = new Date(`${iso}T00:00:00`);
  return `${iso.slice(8, 10)} ${d.toLocaleDateString('en-GB', { month: 'short' })}, ${d.toLocaleDateString('en-GB', { weekday: 'short' })}`;
};
const today0 = () => { const t = new Date(); t.setHours(0, 0, 0, 0); return t; };
const isPast = (iso) => new Date(`${iso}T00:00:00`) < today0();

const keyOf = (q, dep, ret) => [q.origin, q.destination, dep, ret || '', q.counts.adults, q.counts.children,
  q.counts.infants, q.counts.students, q.cabin || ''].join('|');
const remembered = (q, dep, ret) => {
  const hit = seen.get(keyOf(q, dep, ret));
  return hit && Date.now() - hit.at < KEEP_MS ? hit : null;
};

// The days around the customer's departure date. Flights can't be booked on
// days that have passed, so when the date is close to today the window slides
// forward. A round trip keeps its return date; a departure after it is invalid.
const windowDays = (date, returnDate, roundTrip) => {
  const daysToToday = Math.round((today0() - new Date(`${date}T00:00:00`)) / 86400000);
  const first = Math.max(-BEFORE, Math.min(0, daysToToday));
  const ret = roundTrip && returnDate ? returnDate : null;
  return Array.from({ length: BEFORE + AFTER + 1 }, (_, i) => first + i).map(offset => {
    const dep = shift(date, offset);
    return { offset, date: dep, ret, past: isPast(dep) || (ret && dep > ret) };
  });
};

// One live search per day; a day already being searched is not searched again.
const searchOne = (q, dep, ret) => {
  const k = keyOf(q, dep, ret);
  if (!inFlight.has(k)) {
    inFlight.set(k, searchLive(q, dep, ret)
      .then(best => {
        seen.set(k, { ...(best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' }), at: Date.now() });
        return best;
      })
      .catch(e => { seen.delete(k); throw e; })
      .finally(() => inFlight.delete(k)));
  }
  return inFlight.get(k);
};

// Start the nearby-date searches together with the main search.
export function prefetchNearby(q, date, returnDate) {
  if (!q.origin || !q.destination || !date) return;
  windowDays(date, returnDate, q.roundTrip)
    .filter(d => d.offset !== 0 && !d.past)
    .forEach(d => searchOne(q, d.date, d.ret).catch(() => {}));
}

const searchLive = async (q, dep, ret) => {
    const { apiBase, origin, destination, roundTrip, counts, cabin } = q;
    const legs = roundTrip
      ? [{ origin, destination, departure_date: dep }, { origin: destination, destination: origin, departure_date: ret }]
      : null;
    const res = await fetch(`${apiBase}/flights/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Search-Kind': 'nearby' },
      body: JSON.stringify({
        origin: legs ? null : origin,
        destination: legs ? null : destination,
        departure_date: legs ? null : dep,
        legs,
        adult_count: counts.adults, child_count: counts.children,
        infant_count: counts.infants, student_count: counts.students,
        cabin_preference: cabin === 'All' ? null : (cabin || null),
      }),
    });
    if (!res.ok) throw new Error(`search ${res.status}`);
    const data = await res.json();
    const prices = (data.flights || []).map(f => f.price).filter(p => p > 0);
    return prices.length ? { price: Math.min(...prices), currency: data.flights[0].currency } : null;
};

const money = (p, cur) => <><span className="nearby-day-cur">{cur}</span> {Math.floor(p).toLocaleString()}</>;

// Departure × return calendar: live Travelport price for every pair of dates
// around the customer's, a few searches at a time; cheapest third green,
// middle amber, dearest red.
function PriceGrid({ q, date, returnDate, focusDep, currentCheapest, currency, onPick, onClose }) {
  // Departure columns never start in the past: near today the window slides forward.
  const depFirst = Math.max(-GRID, Math.min(0, Math.round((today0() - new Date(`${date}T00:00:00`)) / 86400000)));
  const deps = Array.from({ length: GRID * 2 + 1 }, (_, i) => shift(date, depFirst + i));
  const rets = Array.from({ length: GRID * 2 + 1 }, (_, i) => shift(returnDate, i - GRID));
  const valid = (dep, ret) => !isPast(dep) && dep <= ret;
  const [cells, setCells] = useState(() => {
    const out = {};
    rets.forEach(ret => deps.forEach(dep => {
      if (!valid(dep, ret)) return;
      if (dep === date && ret === returnDate && currentCheapest > 0) out[`${dep}|${ret}`] = { state: 'done', price: currentCheapest, currency };
      else out[`${dep}|${ret}`] = remembered(q, dep, ret) || { state: 'loading' };
    }));
    return out;
  });

  useEffect(() => {
    let alive = true;
    // Closest dates to the customer's first, spreading outwards; stops on close.
    const dist = (k) => { const [dep, ret] = k.split('|');
      return Math.abs(new Date(dep) - new Date(date)) + Math.abs(new Date(ret) - new Date(returnDate)); };
    const queue = Object.entries(cells).filter(([, c]) => c.state === 'loading').map(([k]) => k)
      .sort((a, b) => dist(a) - dist(b));
    const worker = async () => {
      while (alive && queue.length) {
        const k = queue.shift();
        const [dep, ret] = k.split('|');
        let patch;
        try {
          const best = await searchOne(q, dep, ret);
          patch = best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' };
        } catch {
          patch = { state: 'error' };
        }
        if (alive) setCells(prev => ({ ...prev, [k]: patch }));
      }
    };
    Array.from({ length: GRID_PARALLEL }, worker);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const prices = Object.values(cells).filter(c => c.state === 'done' && c.price > 0).map(c => c.price).sort((a, b) => a - b);
  // Thirds of the price range (many dates share one fare, so not thirds of the list)
  const lo = prices[0], hi = prices[prices.length - 1], step = (hi - lo) / 3;
  const tone = (p) => (prices.length < 2 || hi === lo ? ' low' : p <= lo + step ? ' low' : p <= lo + 2 * step ? ' mid' : ' high');
  const waiting = Object.values(cells).filter(c => c.state === 'loading').length;
  const total = Object.keys(cells).length;

  return createPortal(
    <div className="modal-overlay" style={{ animation: 'none' }} onClick={onClose}>
      <div className="price-grid-box" onClick={e => e.stopPropagation()} role="dialog" aria-label="Fare Calendar">
        <div className="price-grid-head">
          <span>Fare Calendar</span>
          <button type="button" className="price-grid-close" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {waiting > 0 ? (
          <div className="price-grid-loading">
            <BookingWaitLoader icon="calendar" title="Checking live fares for every date"
              subtitle={`${total - waiting} of ${total} fares found · live from the airlines`} />
          </div>
        ) : (
        <div className="price-grid-scroll">
          <table className="price-grid">
            <thead>
              <tr><th className="price-grid-corner" /><th colSpan={deps.length} className="price-grid-axis"><AxisIcon d={TAKEOFF} name="Departure" /></th></tr>
              <tr><th className="price-grid-corner price-grid-axis"><AxisIcon d={LANDING} name="Return" /></th>{deps.map(d => <th key={d} title={label(d)} className={d === focusDep ? 'focus' : ''}>{short(d)}</th>)}</tr>
            </thead>
            <tbody>
              {rets.map(ret => (
                <tr key={ret}>
                  <th title={label(ret)}>{short(ret)}</th>
                  {deps.map(dep => {
                    const c = cells[`${dep}|${ret}`];
                    const current = dep === date && ret === returnDate;
                    if (!c) return <td key={dep} className="price-grid-na" />;
                    return (
                      <td key={dep} className={`${current ? 'current' : ''}${dep === focusDep && !current ? ' focus' : ''}`}>
                        {c.state === 'done' ? (
                          <button type="button" className={`price-grid-cell${tone(c.price)}`}
                                  disabled={current} onClick={() => { onClose(); onPick(dep, ret); }}
                                  title={`${label(dep)} – ${label(ret)}`}>
                            {money(c.price, c.currency || currency)}
                          </button>
                        ) : c.state === 'loading' ? <span className="price-grid-dot">…</span> : <span className="price-grid-dot">—</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </div>
    </div>,
    document.body
  );
}

// Month-style Fare Calendar: the cheapest live fare for every departure day
// from 2 weeks before to 2 weeks after the customer's date (round trips keep
// their return date), laid out Monday–Sunday. Searched when opened, a few at
// a time; the days nearest the customer's date were already searched with
// the main search. Cheapest third of the price range green, middle amber,
// dearest red. Round trips can switch to the departure × return grid.
const SPAN = 14;
function MonthCalendar({ q, date, returnDate, roundTrip, currentCheapest, currency, onPick, onClose }) {
  const ret = roundTrip && returnDate ? returnDate : null;
  const days = Array.from({ length: SPAN * 2 + 1 }, (_, i) => shift(date, i - SPAN));
  const usable = (d) => !isPast(d) && !(ret && d > ret);
  const [cells, setCells] = useState(() => {
    const out = {};
    days.forEach(d => {
      if (!usable(d)) return;
      if (d === date && currentCheapest > 0) out[d] = { state: 'done', price: currentCheapest, currency };
      else out[d] = remembered(q, d, ret) || { state: 'loading' };
    });
    return out;
  });

  useEffect(() => {
    let alive = true;
    // nearest days first, so the dates the customer most likely wants fill in first
    const queue = Object.entries(cells).filter(([, c]) => c.state === 'loading').map(([d]) => d)
      .sort((a, b) => Math.abs(new Date(a) - new Date(date)) - Math.abs(new Date(b) - new Date(date)));
    const worker = async () => {
      while (alive && queue.length) {
        const d = queue.shift();
        let patch;
        try {
          const best = await searchOne(q, d, ret);
          patch = best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' };
        } catch {
          patch = { state: 'error' };
        }
        if (alive) setCells(prev => ({ ...prev, [d]: patch }));
      }
    };
    Array.from({ length: GRID_PARALLEL }, worker);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const prices = Object.values(cells).filter(c => c.state === 'done' && c.price > 0).map(c => c.price).sort((a, b) => a - b);
  const lo = prices[0], hi = prices[prices.length - 1], step = (hi - lo) / 3;
  const tone = (p) => (prices.length < 2 || hi === lo ? ' low' : p <= lo + step ? ' low' : p <= lo + 2 * step ? ' mid' : ' high');
  const waiting = Object.values(cells).filter(c => c.state === 'loading').length;

  // Monday-first weeks covering the range
  const first = new Date(`${days[0]}T00:00:00`);
  const lead = (first.getDay() + 6) % 7;   // days before the first date in its week
  const start = shift(days[0], -lead);
  const totalCells = Math.ceil((lead + days.length) / 7) * 7;
  const grid = Array.from({ length: totalCells }, (_, i) => shift(start, i));
  const inRange = new Set(days);

  return createPortal(
    <div className="modal-overlay" style={{ animation: 'none' }} onClick={onClose}>
      <div className="price-grid-box fare-month-box" onClick={e => e.stopPropagation()} role="dialog" aria-label="Fare Calendar">
        <div className="price-grid-head">
          <span>Fare Calendar</span>
          <button type="button" className="price-grid-close" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {waiting > 0 ? (
          <div className="price-grid-loading">
            <BookingWaitLoader icon="calendar" title="Checking live fares for every date"
              subtitle={`${Object.keys(cells).length - waiting} of ${Object.keys(cells).length} fares found · live from the airlines`} />
          </div>
        ) : (<>
        <div className="fare-month-sub">
          Cheapest fare by departure date{ret ? ` · return ${label(ret)}` : ''} · live from Travelport
        </div>
        <div className="fare-month">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(w => <div key={w} className="fare-month-wd">{w}</div>)}
          {grid.map(d => {
            const c = cells[d];
            const current = d === date;
            const dd = new Date(`${d}T00:00:00`);
            const head = (
              <span className="fare-month-day">
                {dd.getDate()}{dd.getDate() === 1 || d === days[0] ? ` ${dd.toLocaleDateString('en-GB', { month: 'short' })}` : ''}
              </span>
            );
            if (!inRange.has(d)) return <div key={d} className="fare-month-cell blank" />;
            if (!c) return <div key={d} className="fare-month-cell off" title={label(d)}>{head}<span className="price-grid-dot">—</span></div>;
            const clickable = c.state === 'done' && !current;
            return (
              <button key={d} type="button" title={label(d)} disabled={!clickable}
                      className={`fare-month-cell${current ? ' current' : ''}`}
                      onClick={() => { onClose(); onPick(d, ret); }}>
                {head}
                {c.state === 'done'
                  ? <span className={`fare-month-price price-grid-cell${tone(c.price)}`}>{money(c.price, c.currency || currency)}</span>
                  : c.state === 'loading' ? <span className="price-grid-dot">…</span>
                  : <span className="price-grid-dot">{c.state === 'none' ? 'No flights' : '—'}</span>}
              </button>
            );
          })}
        </div>
        </>)}
      </div>
    </div>,
    document.body
  );
}

export default function NearbyDates({ apiBase, origin, destination, date, returnDate, roundTrip, counts, cabin, currentCheapest, currency, onPick }) {
  const [open, setOpen] = useState(null);   // 'month' | 'grid' | null
  const q = { apiBase, origin, destination, roundTrip, counts, cabin };

  // The fare already shown for the customer's own dates counts as found.
  useEffect(() => {
    if (currentCheapest > 0) {
      seen.set(keyOf(q, date, roundTrip && returnDate ? returnDate : null), { state: 'done', price: currentCheapest, currency, at: Date.now() });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div className="fare-cal-bar">
        <button type="button" className="fare-cal-btn" onClick={() => setOpen(roundTrip && returnDate ? 'grid' : 'month')}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <rect x="3" y="4.5" width="18" height="16" rx="2" />
            <line x1="3" y1="9" x2="21" y2="9" /><line x1="8" y1="2.5" x2="8" y2="6.5" /><line x1="16" y1="2.5" x2="16" y2="6.5" />
            <path d="M7 12.5h2M11 12.5h2M15 12.5h2M7 16h2M11 16h2M15 16h2" />
          </svg>
          <span>Fare Calendar</span>
          <span className="fare-cal-hint">{roundTrip && returnDate ? 'Departure & return dates · 7 days either side' : 'Cheapest fares 2 weeks either side'}</span>
        </button>
      </div>
      {open === 'month' && (
        <MonthCalendar q={q} date={date} returnDate={returnDate} roundTrip={roundTrip} currentCheapest={currentCheapest} currency={currency}
                       onPick={onPick} onClose={() => setOpen(null)} />
      )}
      {open === 'grid' && (
        <PriceGrid q={q} date={date} returnDate={returnDate} focusDep={date} currentCheapest={currentCheapest} currency={currency}
                   onPick={onPick} onClose={() => setOpen(null)} />
      )}
    </>
  );
}
