// Share fares from the search results with friends by WhatsApp or email.
// The customer ticks one or more fare cards; each tick stores a text summary
// of that fare as shown on the card at that moment (flights, cabin, baggage,
// price). Sharing opens WhatsApp (wa.me) or the customer's own mail app
// (mailto:) with the text filled in — nothing is sent from our server and no
// customer details are included.
import React, { useState, useEffect } from 'react';
import whatsappIcon from './assets/whatsapp_icon.png';

const fmtTime = (iso) => iso?.split('T')[1]?.slice(0, 5) || iso?.split(' ')[1]?.slice(0, 5) || '';
const fmtDate = (iso) => {
  const d = iso?.split('T')[0] || iso?.split(' ')[0];
  if (!d) return '';
  const dt = new Date(`${d}T00:00:00`);
  return isNaN(dt) ? d : dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
};
const fmtDuration = (iso) => iso ? iso.replace('PT', '').replace('H', 'h ').replace('M', 'm').trim() : '';
const cabinName = (c) => c === 'PremiumEconomy' ? 'Premium Economy' : (c || 'Economy');

// Text summary of one fare card, as the customer sees it now.
export function buildShareItem({ flight, cabin, fareOption, price, currency, grandTotal, totalPax }) {
  const legs = flight.legs?.length ? [...flight.legs].sort((a, b) => (a.departure_time || '').localeCompare(b.departure_time || '')) : [flight];
  const legLines = legs.map((leg, i) => {
    const segs = leg.segments?.length ? leg.segments : [leg];
    const first = segs[0];
    const last = segs[segs.length - 1];
    const label = legs.length === 2 ? (i === 0 ? 'Depart' : 'Return') : legs.length > 2 ? `Leg ${i + 1}` : 'Flight';
    const stops = segs.length - 1;
    const via = stops > 0 ? ` via ${segs.slice(0, -1).map(s => s.arrival_airport).join(', ')}` : '';
    return [
      `${label}: ${first.departure_airport} → ${last.arrival_airport} · ${fmtDate(first.departure_time)}`,
      `  ${fmtTime(first.departure_time)} → ${fmtTime(last.arrival_time)}${fmtDate(last.arrival_time) !== fmtDate(first.departure_time) ? ` (${fmtDate(last.arrival_time)})` : ''}`
        + ` · ${stops === 0 ? 'Non-stop' : `${stops} stop${stops > 1 ? 's' : ''}${via}`}${leg.duration ? ` · ${fmtDuration(leg.duration)}` : ''}`,
      `  ${segs.map(s => s.flight_number).filter(Boolean).join(' / ')}`,
    ].join('\n');
  });
  const bags = (fareOption?.baggage_allowance || []).map(b => `${b.type}: ${b.allowance}`).join(' · ');
  const money = (n) => `${currency} ${Math.round(n).toLocaleString()}`;
  const priceLine = totalPax > 1
    ? `${money(grandTotal)} total for ${totalPax} travellers`
    : `${money(grandTotal || price)}`;
  const text = [
    `✈ ${flight.airline}${flight.airline_code ? ` (${flight.airline_code})` : ''} · ${cabinName(cabin)}${fareOption?.brand_name ? ` · ${fareOption.brand_name}` : ''}`,
    ...legLines,
    ...(bags ? [`  Baggage: ${bags}`] : []),
    `  Fare: ${priceLine}`,
  ].join('\n');
  const route = `${legs[0].segments?.[0]?.departure_airport || flight.departure_airport || ''} → ${(legs[0].segments?.length ? legs[0].segments[legs[0].segments.length - 1] : legs[0]).arrival_airport || ''}`;
  return { text, label: `${flight.airline} · ${route} · ${money(grandTotal || price)}` };
}

function shareText(items) {
  const header = items.length > 1 ? `Have a look at these ${items.length} flight options:` : 'Have a look at this flight option:';
  const footer = [
    'Fares are live and change with availability — the price is confirmed when you book.',
    `Book at ${window.location.origin}`,
  ].join('\n');
  return [header, ...items.map((it, i) => (items.length > 1 ? `${i + 1}. ` : '') + it.text), footer].join('\n\n');
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

// Bar pinned to the bottom of the screen while one or more fares are ticked.
export function ShareFaresBar({ items, onClear }) {
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
  const text = shareText(items);
  const subject = items.length > 1 ? `${items.length} flight options for you` : `Flight option: ${items[0].label}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
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
      <a href={`https://wa.me/?text=${encodeURIComponent(text)}`} target="_blank" rel="noopener noreferrer"
         style={{ ...btn, background: '#25d366', color: '#ffffff' }}>
        <img src={whatsappIcon} alt="" style={{ width: '16px', height: '16px' }} /> WhatsApp
      </a>
      <a href={`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(text)}`}
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
