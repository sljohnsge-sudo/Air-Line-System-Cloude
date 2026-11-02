// Prices on nearby dates, on the results page. Loads by itself with the
// results: real Travelport searches (the same /api/flights/search the main
// search uses) for 3 days before and 5 days after the customer's date
// (never past days), same route, travellers and cabin — round trips keep the
// same length of stay. All days are searched at the same time so they fill in
// together. Shows the cheapest live fare per day; tapping a day runs that
// search. Every day is searched live on Travelport each time the strip shows;
// while that runs, a day already found in the last 10 minutes keeps showing
// its earlier Travelport price (so picking a day doesn't flash "Checking…"),
// and is replaced by the fresh Travelport price as soon as it arrives.
// prefetchNearby() starts these searches together with the main search, so
// the strip is usually full by the time the results appear.
import React, { useEffect, useState } from 'react';
import { fmtDateWeekday } from './dateFormat.js';

const BEFORE = 3;
const AFTER = 5;
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
// Short box label so nine days fit: "Mon 02/11"
const short = (iso) => {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.toLocaleDateString('en-GB', { weekday: 'short' })} ${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
};

const keyOf = (q, dep, ret) => [q.origin, q.destination, dep, ret || '', q.counts.adults, q.counts.children,
  q.counts.infants, q.counts.students, q.cabin || ''].join('|');
const remembered = (q, dep, ret) => {
  const hit = seen.get(keyOf(q, dep, ret));
  return hit && Date.now() - hit.at < KEEP_MS ? hit : null;
};

// The days around the customer's date. Flights can't be booked on days that
// have passed, so when the date is close to today the window slides forward.
const windowDays = (date, returnDate, roundTrip) => {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const daysToToday = Math.round((today - new Date(`${date}T00:00:00`)) / 86400000);
  const first = Math.max(-BEFORE, Math.min(0, daysToToday));
  return Array.from({ length: BEFORE + AFTER + 1 }, (_, i) => first + i).map(offset => {
    const dep = shift(date, offset);
    return { offset, date: dep, ret: roundTrip && returnDate ? shift(returnDate, offset) : null,
             past: new Date(`${dep}T00:00:00`) < today };
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

export default function NearbyDates({ apiBase, origin, destination, date, returnDate, roundTrip, counts, cabin, currentCheapest, currency, onPick }) {
  const [days, setDays] = useState([]);   // [{ offset, date, ret, state: 'loading'|'done'|'none'|'error'|'past', price }]
  const q = { apiBase, origin, destination, roundTrip, counts, cabin };

  useEffect(() => {
    let alive = true;
    const ret0 = roundTrip && returnDate ? returnDate : null;
    if (currentCheapest > 0) {
      seen.set(keyOf(q, date, ret0), { state: 'done', price: currentCheapest, currency, at: Date.now() });
    }
    const list = windowDays(date, returnDate, roundTrip).map(({ offset, date: dep, ret, past }) => {
      if (offset === 0) return { offset, date: dep, ret, state: 'done', price: currentCheapest, currency };
      if (past) return { offset, date: dep, ret, state: 'past' };
      // A search started with the main search and already finished shows straight away.
      const hit = !inFlight.has(keyOf(q, dep, ret)) && remembered(q, dep, ret);
      if (hit && Date.now() - hit.at < 60000) return { offset, date: dep, ret, ...hit };
      return hit ? { offset, date: dep, ret, ...hit, live: true } : { offset, date: dep, ret, state: 'loading', live: true };
    });
    setDays(list);
    const update = (dep, patch) => alive && setDays(prev => prev.map(d => (d.date === dep ? { ...d, ...patch } : d)));
    // Every day searched live on Travelport, all at once, so prices arrive together.
    list.filter(d => d.live).forEach(async (d) => {
      try {
        const best = await searchOne(q, d.date, d.ret);
        update(d.date, best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' });
      } catch {
        update(d.date, { state: 'error' });
      }
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!days.length) return null;
  // Highlight a day only when it is genuinely cheaper than the customer's dates
  const found = days.filter(d => d.offset !== 0 && d.state === 'done' && d.price > 0);
  const lowest = found.length ? Math.min(...found.map(d => d.price)) : null;
  const cheapest = lowest != null && lowest < currentCheapest ? lowest : null;
  return (
    <div className="nearby-dates" role="group" aria-label="Cheapest live fare for nearby dates">
      {days.map(d => {
        const isCurrent = d.offset === 0;
        const clickable = !isCurrent && d.state === 'done';
        return (
          <button
            key={d.date} type="button"
            className={`nearby-day${isCurrent ? ' current' : ''}${d.state === 'done' && d.price === cheapest ? ' cheapest' : ''}`}
            disabled={!clickable}
            onClick={() => clickable && onPick(d.date, d.ret)}
            title={roundTrip && d.ret ? `${label(d.date)} – ${label(d.ret)}` : label(d.date)}
          >
            <span className="nearby-day-date">{roundTrip && d.ret ? 'Depart ' : ''}{short(d.date)}</span>
            {roundTrip && d.ret && <span className="nearby-day-ret">Return {short(d.ret)}</span>}
            <span className="nearby-day-price">
              {d.state === 'loading' && <span className="nearby-day-wait">Checking…</span>}
              {d.state === 'done' && `${d.currency || currency} ${Math.floor(d.price).toLocaleString()}`}
              {d.state === 'none' && 'No flights'}
              {d.state === 'error' && 'Unavailable'}
              {d.state === 'past' && '—'}
            </span>
            {isCurrent && <span className="nearby-day-note">Your dates</span>}
            {!isCurrent && d.state === 'done' && d.price === cheapest && <span className="nearby-day-note">Cheaper</span>}
          </button>
        );
      })}
    </div>
  );
}
