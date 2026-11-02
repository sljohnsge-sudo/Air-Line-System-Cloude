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

export default function NearbyDates({ apiBase, origin, destination, date, returnDate, roundTrip, counts, cabin, currentCheapest, currency, onPick }) {
  const [days, setDays] = useState([]);   // [{ offset, date, ret, state: 'loading'|'done'|'none'|'error'|'past', price }]

  const keyOf = (dep, ret) => [origin, destination, dep, ret || '', counts.adults, counts.children,
    counts.infants, counts.students, cabin || ''].join('|');
  const remembered = (dep, ret) => {
    const hit = seen.get(keyOf(dep, ret));
    return hit && Date.now() - hit.at < KEEP_MS ? hit : null;
  };

  const searchOne = (dep, ret) => {
    const k = keyOf(dep, ret);
    if (!inFlight.has(k)) inFlight.set(k, searchLive(dep, ret).finally(() => inFlight.delete(k)));
    return inFlight.get(k);
  };

  const searchLive = async (dep, ret) => {
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

  useEffect(() => {
    let alive = true;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    // Flights can't be booked on days that have passed, so when the date is
    // close to today the window slides forward.
    const daysToToday = Math.round((today - new Date(`${date}T00:00:00`)) / 86400000);
    const first = Math.max(-BEFORE, Math.min(0, daysToToday));
    const ret0 = roundTrip && returnDate ? returnDate : null;
    if (currentCheapest > 0) {
      seen.set(keyOf(date, ret0), { state: 'done', price: currentCheapest, currency, at: Date.now() });
    }
    const list = Array.from({ length: BEFORE + AFTER + 1 }, (_, i) => first + i).map(offset => {
      const dep = shift(date, offset);
      const ret = roundTrip && returnDate ? shift(returnDate, offset) : null;
      if (offset === 0) return { offset, date: dep, ret, state: 'done', price: currentCheapest, currency };
      if (new Date(`${dep}T00:00:00`) < today) return { offset, date: dep, ret, state: 'past' };
      const hit = remembered(dep, ret);
      return hit ? { offset, date: dep, ret, ...hit, live: true } : { offset, date: dep, ret, state: 'loading', live: true };
    });
    setDays(list);
    const update = (dep, patch) => alive && setDays(prev => prev.map(d => (d.date === dep ? { ...d, ...patch } : d)));
    // Every day searched live on Travelport, all at once, so prices arrive together.
    list.filter(d => d.live).forEach(async (d) => {
      try {
        const best = await searchOne(d.date, d.ret);
        const patch = best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' };
        seen.set(keyOf(d.date, d.ret), { ...patch, at: Date.now() });
        update(d.date, patch);
      } catch {
        seen.delete(keyOf(d.date, d.ret));
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
            <span className="nearby-day-date">{short(d.date)}</span>
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
