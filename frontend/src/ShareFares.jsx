// Share fares from the search results with friends by WhatsApp or email.
// The customer ticks one or more fare cards; each tick stores a summary of
// that fare as shown on the card at that moment (flights, cabin, baggage,
// price). Sharing opens WhatsApp or the customer's own mail app with the
// message filled in — nothing is sent from our server and no customer
// details are included.
//
// The message text can be edited by whoever forwards it, so the price in it
// is only "at the time of sharing". The message carries a link that re-runs
// the same search live and shows the shared flights first, with the
// airline's price right now — that, never the message, is what gets booked.
import React, { useState, useEffect } from 'react';
import whatsappIcon from './assets/whatsapp_icon.png';
import { encryptForUrl } from './urlCrypto.js';
import { fmtDateWeekday } from './dateFormat.js';

const fmtTime = (iso) => iso?.split('T')[1]?.slice(0, 5) || iso?.split(' ')[1]?.slice(0, 5) || '';
const isoDay = (iso) => iso?.split('T')[0] || iso?.split(' ')[0] || '';
const fmtDate = (iso) => {
  const d = isoDay(iso);
  if (!d) return '';
  return fmtDateWeekday(d);
};
const fmtDuration = (iso) => iso ? iso.replace('PT', '').replace('H', 'h ').replace('M', 'm').trim() : '';
const cabinName = (c) => c === 'PremiumEconomy' ? 'Premium Economy' : (c || 'Economy');
const titleCase = (s) => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

const legsOf = (flight) => flight.legs?.length
  ? [...flight.legs].sort((a, b) => (a.departure_time || '').localeCompare(b.departure_time || ''))
  : [flight];
const segsOf = (leg) => leg.segments?.length ? leg.segments : [leg];

// The same flights in a later live search for the same dates: their flight
// numbers (offer ids and prices change between searches, these don't). Kept
// short because it travels in the shared link.
export const flightIdentity = (flight) =>
  legsOf(flight).map(l => segsOf(l).map(s => s.flight_number).join('.')).join('-');

const fmtMinutes = (m) => m > 0 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`.replace(/^0h /, '') : '';
const minutesBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 60000);
const terminal = (t) => t ? ` T${t}` : '';
// "SecondCheckedBag" -> "Second Checked Bag"
const bagName = (t) => (t || 'Baggage').replace(/([a-z])([A-Z])/g, '$1 $2');

// Everything on one fare card, as the customer sees it now: each flight of
// each leg with times, terminals, aircraft and stopover, plus fare, baggage,
// seat selection and price.
export function buildShareItem({ flight, cabin, fareOption, price, currency, grandTotal, paxLabel }) {
  const legs = legsOf(flight).map((leg, i, all) => {
    const segs = segsOf(leg);
    const first = segs[0];
    const last = segs[segs.length - 1];
    return {
      outbound: i === 0,
      label: all.length === 2 ? (i === 0 ? 'Depart' : 'Return') : all.length > 2 ? `Flight ${i + 1}` : 'Depart',
      from: first.departure_airport,
      to: last.arrival_airport,
      date: fmtDate(first.departure_time),
      stops: segs.length === 1 ? 'Non-stop' : `${segs.length - 1} stop${segs.length > 2 ? 's' : ''}`,
      duration: fmtDuration(leg.duration),
      segments: segs.map((s, k) => {
        const next = segs[k + 1];
        const stopMinutes = next ? (s.layover_minutes ?? minutesBetween(s.arrival_time, next.departure_time)) : 0;
        return {
          flightNo: s.flight_number,
          // Another airline's flight on this ticket (a 2-letter code alone adds nothing to the flight number).
          carrier: s.carrier_name && s.carrier_name.length > 3 && s.carrier_name !== flight.airline ? s.carrier_name : null,
          aircraft: s.aircraft_type || (segs.length === 1 ? leg.aircraft_type || flight.aircraft_type : null),
          dep: `${fmtTime(s.departure_time)} ${s.departure_airport}${terminal(s.departure_terminal)}`,
          arr: `${fmtTime(s.arrival_time)} ${s.arrival_airport}${terminal(s.arrival_terminal)}`,
          arrDate: isoDay(s.arrival_time) !== isoDay(first.departure_time) ? fmtDate(s.arrival_time) : null,
          duration: fmtDuration(s.duration),
          stopover: next ? `${s.arrival_airport}${fmtMinutes(stopMinutes) ? ` · ${fmtMinutes(stopMinutes)}` : ''}` : null,
        };
      }),
    };
  });
  const bags = (fareOption?.baggage_allowance || []).map(b => `${bagName(b.type)}: ${b.allowance}`);
  const total = `${currency} ${Math.round(grandTotal || price).toLocaleString()}`;
  return {
    key: flightIdentity(flight),
    airline: `${flight.airline}${flight.airline_code ? ` (${flight.airline_code})` : ''}`,
    // "Economy Classic" says the cabin already; "Saver" alone doesn't.
    fare: fareOption?.brand_name
      ? (titleCase(fareOption.brand_name).includes(cabinName(cabin)) ? titleCase(fareOption.brand_name) : `${cabinName(cabin)} · ${titleCase(fareOption.brand_name)}`)
      : cabinName(cabin),
    cabin: `${cabinName(cabin)} Class`,
    seats: flight.seat_map_heuristic === true ? 'Seat selection available' : 'Seat selection not available',
    seatsLeft: flight.seats_remaining != null && flight.seats_remaining < 5 ? `Only ${flight.seats_remaining} seats left` : null,
    legs,
    bags,
    total,
    paxLabel,
    label: `${flight.airline} · ${legs[0].from} → ${legs[0].to} · ${total}`,
  };
}

// Link back to our site: the same search, run live, shared flights first.
function shareLink(items, search) {
  const token = encryptForUrl(JSON.stringify({ ...search, step: null, shared: items.map(i => i.key) }));
  const base = (import.meta.env.VITE_PUBLIC_SITE_URL || window.location.origin).replace(/\/$/, '');
  return token ? `${base}/results/${token}` : base;
}

// WhatsApp shows *text* as bold and _text_ as italic; email gets plain text.
export function shareText(items, link, { bold, italic }) {
  const pad = '      ';
  const blocks = items.map((it, n) => [
    `${bold(`${items.length > 1 ? `${n + 1}. ` : ''}${it.airline}`)}`,
    `${it.fare}`,
    ...it.legs.flatMap(l => [
      '',
      `${l.outbound ? '🛫' : '🛬'} ${bold(`${l.label.toUpperCase()} · ${l.from} → ${l.to}`)}`,
      `${pad}${l.date} · ${l.stops}${l.duration ? ` · ${l.duration} total` : ''}`,
      ...l.segments.flatMap(s => [
        `${pad}${bold(s.flightNo)}${s.carrier ? ` · ${s.carrier}` : ''}${s.aircraft ? ` · ${s.aircraft}` : ''}`,
        `${pad}${s.dep} → ${s.arr}${s.arrDate ? ` (${s.arrDate})` : ''}${s.duration ? ` · ${s.duration}` : ''}`,
        ...(s.stopover ? [`${pad}⏳ Stopover in ${s.stopover}`] : []),
      ]),
    ]),
    '',
    `💺 ${it.cabin} · ${it.seats}${it.seatsLeft ? ` · ${it.seatsLeft}` : ''}`,
    ...(it.bags.length ? [`🧳 ${it.bags.join(' · ')}`] : []),
    `💰 ${bold(it.total)}${it.paxLabel ? ` for ${it.paxLabel}` : ''}`,
  ].join('\n'));
  return [
    `✈️ ${bold(items.length > 1 ? `${items.length} flight options for you` : 'A flight option for you')}`,
    ...blocks,
    [
      `👉 ${bold('See the live price & book:')}`,
      link,
      italic('Prices shown are from when this was shared and change with seat availability. The live price on the link is the price you pay.'),
    ].join('\n'),
  ].join(items.length > 1 ? '\n\n━━━━━━━━━━\n\n' : '\n\n');
}

// The tick box on a fare card.
export function ShareToggle({ checked, onChange }) {
  return (
    <label title="Tick to share this fare by WhatsApp or email" style={{
      display: 'inline-flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer',
      fontSize: '0.68rem', fontWeight: 600, color: checked ? '#245d38' : '#64748b', userSelect: 'none',
    }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
             style={{ accentColor: '#245d38', width: '13px', height: '13px', margin: 0, cursor: 'pointer' }} />
      Share this fare
    </label>
  );
}

// Shown on results opened from a shared link.
export function SharedFaresBanner({ count, found, onDismiss }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem',
      padding: '0.75rem 1rem', borderRadius: '10px', background: '#f0fdf4', border: '1px solid #86efac', color: '#14532d',
    }}>
      <div style={{ flex: '1 1 260px', fontSize: '0.85rem' }}>
        <strong>Shared with you:</strong>{' '}
        {found > 0
          ? <>the {found === 1 ? 'flight is' : `${found} flights are`} shown first, marked “Shared with you”. Prices are live from the airline now and may differ from the message.</>
          : <>the shared flight{count > 1 ? 's are' : ' is'} no longer available. Here are the flights on sale now for the same trip.</>}
        {found > 0 && found < count && ` ${count - found} of the shared flights are no longer available.`}
      </div>
      <button type="button" onClick={onDismiss} style={{ background: 'none', border: '1px solid #86efac', borderRadius: '6px', padding: '0.35rem 0.7rem', color: '#14532d', fontWeight: 700, fontSize: '0.75rem', cursor: 'pointer' }}>
        Show all flights
      </button>
    </div>
  );
}

// Bar pinned to the bottom of the screen while one or more fares are ticked.
export function ShareFaresBar({ items, search, onClear }) {
  const [copied, setCopied] = useState(false);
  const visible = items.length > 0;
  // Room at the bottom of the page so the bar never covers the last card.
  useEffect(() => {
    if (!visible) return undefined;
    const prev = document.body.style.paddingBottom;
    document.body.style.paddingBottom = '110px';
    return () => { document.body.style.paddingBottom = prev; };
  }, [visible]);
  if (!visible) return null;

  const link = shareLink(items, search);
  const whatsappText = shareText(items, link, { bold: s => `*${s}*`, italic: s => `_${s}_` });
  const plainText = shareText(items, link, { bold: s => s, italic: s => s });
  const subject = items.length > 1 ? `${items.length} flight options for you` : `Flight option: ${items[0].label}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked — the WhatsApp and email buttons still work */ }
  };

  const btn = { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', border: 'none', borderRadius: '6px', padding: '0.5rem 0.9rem', fontWeight: 700, fontSize: '0.8rem', cursor: 'pointer', textDecoration: 'none', whiteSpace: 'nowrap' };
  return (
    <div style={{
      position: 'fixed', left: '50%', bottom: '16px', transform: 'translateX(-50%)', zIndex: 900,
      width: 'min(760px, calc(100% - 32px))', boxSizing: 'border-box',
      background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: '12px', boxShadow: '0 10px 30px rgba(15,23,42,0.18)',
      padding: '0.7rem 0.9rem', display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
    }}>
      <div style={{ flex: '1 1 180px', minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: '0.85rem', color: '#1e293b' }}>
          {items.length} fare{items.length > 1 ? 's' : ''} selected to share
        </div>
        <div style={{ fontSize: '0.7rem', color: '#64748b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {items.map(i => i.label).join('  ·  ')}
        </div>
      </div>
      {/* api.whatsapp.com keeps emoji intact; wa.me's redirect can garble them. */}
      <a href={`https://api.whatsapp.com/send?text=${encodeURIComponent(whatsappText)}`} target="_blank" rel="noopener noreferrer"
         style={{ ...btn, background: '#25d366', color: '#ffffff' }}>
        <img src={whatsappIcon} alt="" style={{ width: '16px', height: '16px' }} /> WhatsApp
      </a>
      <a href={`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(plainText)}`}
         style={{ ...btn, background: '#1e293b', color: '#ffffff' }}>
        ✉ Email
      </a>
      <button type="button" onClick={copy} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>
        {copied ? '✓ Copied' : 'Copy text'}
      </button>
      <button type="button" onClick={onClear} style={{ ...btn, background: 'none', color: '#64748b', padding: '0.5rem 0.4rem' }}>
        Clear
      </button>
    </div>
  );
}
