// "Compare prices ±3 days" on the results page. Runs only when the customer
// asks: real Travelport searches (the same /api/flights/search the main
// search uses) for the three days either side, same route, travellers and
// cabin — round trips keep the same length of stay. Shows the cheapest live
// fare per day; tapping a day runs that search. At most two searches run at
// a time so Travelport isn't flooded. Nothing here is estimated or cached.
import React, { useState } from 'react';

const shift = (iso, days) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
const label = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });

export default function NearbyDates({ apiBase, origin, destination, date, returnDate, roundTrip, counts, cabin, currentCheapest, currency, onPick }) {
  const [days, setDays] = useState(null);   // [{ offset, date, ret, state: 'loading'|'done'|'none'|'error'|'past', price }]
  const today = new Date(); today.setHours(0, 0, 0, 0);

  const searchOne = async (dep, ret) => {
    const legs = roundTrip
      ? [{ origin, destination, departure_date: dep }, { origin: destination, destination: origin, departure_date: ret }]
      : null;
    const res = await fetch(`${apiBase}/flights/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

  const run = async () => {
    const list = [-3, -2, -1, 0, 1, 2, 3].map(offset => {
      const dep = shift(date, offset);
      const ret = roundTrip && returnDate ? shift(returnDate, offset) : null;
      const past = new Date(`${dep}T00:00:00`) < today;
      if (offset === 0) return { offset, date: dep, ret, state: 'done', price: currentCheapest, currency };
      return { offset, date: dep, ret, state: past ? 'past' : 'loading' };
    });
    setDays(list);
    const queue = list.filter(d => d.state === 'loading');
    const update = (dep, patch) => setDays(prev => prev.map(d => (d.date === dep ? { ...d, ...patch } : d)));
    const worker = async () => {
      while (queue.length) {
        const d = queue.shift();
        try {
          const best = await searchOne(d.date, d.ret);
          update(d.date, best ? { state: 'done', price: best.price, currency: best.currency } : { state: 'none' });
        } catch {
          update(d.date, { state: 'error' });
        }
      }
    };
    await Promise.all([worker(), worker()]);
  };

  if (!days) {
    return (
      <button type="button" className="nearby-dates-open" onClick={run}>
        📅 Compare prices ±3 days
      </button>
    );
  }
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
            <span className="nearby-day-date">{label(d.date)}</span>
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
