// Special service requests a passenger can make while booking — a special
// meal and wheelchair assistance — and the airline's answer on the booking.
// Codes are the IATA SSR codes Travelport's Remarks & Service Requests Guide
// supports; the backend (services/special_services.py) holds the same list.
import React from 'react';

export const MEAL_OPTIONS = [
  ['AVML', 'Vegetarian Hindu (Asian vegetarian)'],
  ['VGML', 'Vegan'],
  ['VLML', 'Vegetarian (lacto-ovo)'],
  ['VJML', 'Vegetarian Jain'],
  ['VOML', 'Vegetarian Oriental'],
  ['RVML', 'Raw vegetarian'],
  ['HNML', 'Hindu (non-vegetarian)'],
  ['MOML', 'Muslim'],
  ['KSML', 'Kosher'],
  ['DBML', 'Diabetic'],
  ['LFML', 'Low fat'],
  ['LSML', 'Low salt'],
  ['LCML', 'Low calorie'],
  ['NLML', 'Low lactose'],
  ['SFML', 'Seafood'],
  ['FPML', 'Fruit platter'],
  ['CHML', 'Child meal'],
  ['BBML', 'Baby meal'],
  ['NOML', 'No meal'],
];

export const WHEELCHAIR_OPTIONS = [
  ['WCHR', 'Wheelchair for distances — can climb stairs and walk to the seat'],
  ['WCHS', "Wheelchair, can't climb stairs — can walk to the seat"],
  ['WCHC', 'Fully immobile — needs carrying to and from the seat'],
];

// NDC fares take meal requests through Travelport only on these airlines
// (Remarks & Service Requests Guide); wheelchair requests work for all.
const NDC_MEAL_CARRIERS = ['AA', 'QF', 'UA'];

const legsOf = (ro) => !ro ? [] : ro.outbound && ro.inbound ? [ro.outbound, ro.inbound] : ro.legs || [ro];

// What the selected fare lets a passenger request online:
// { meals, wheelchair, note }.
export function ssrSupport(rawOffering) {
  const legs = legsOf(rawOffering);
  const isGds = legs.length > 0 && legs.every(l => l.fare_source === 'GDS');
  const carrier = legs[0]?.segments?.[0]?.carrier;
  // Emirates NDC books and tickets in one Instant Pay step, which sends no SSRs.
  if (!isGds && carrier === 'EK') {
    return { meals: false, wheelchair: false, note: 'Meal and wheelchair requests for this Emirates fare can be made with the airline after booking.' };
  }
  const meals = isGds || NDC_MEAL_CARRIERS.includes(carrier);
  return { meals, wheelchair: true, note: meals ? '' : 'This airline doesn\'t take meal requests online for this fare — you can ask the airline after booking.' };
}

const label = (code) => (MEAL_OPTIONS.find(o => o[0] === code) || WHEELCHAIR_OPTIONS.find(o => o[0] === code) || [code, code])[1];

const STATUS = {
  confirmed: { text: 'Confirmed by airline', color: '#166534', bg: '#dcfce7' },
  pending: { text: 'Pending airline reply', color: '#92400e', bg: '#fef3c7' },
  requested: { text: 'Requested — no airline reply yet', color: '#1e40af', bg: '#dbeafe' },
  failed: { text: 'Not sent', color: '#991b1b', bg: '#fee2e2' },
  not_supported: { text: 'Not available online', color: '#991b1b', bg: '#fee2e2' },
};

// The airline's answer to each meal / wheelchair request on a booking
// (ticket.ssr_status from the backend).
export function SsrStatusList({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div style={{ marginBottom: '1.5rem', padding: '0.75rem 1rem', border: '1px solid #e2e8f0', borderRadius: '8px', background: '#f8fafc' }}>
      <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase', fontWeight: '700', letterSpacing: '0.04em', marginBottom: '0.4rem' }}>Special Assistance &amp; Meals</div>
      {items.map((s, i) => {
        const st = STATUS[s.status] || { text: s.status, color: '#334155', bg: '#e2e8f0' };
        return (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', fontSize: '0.8rem', padding: '0.25rem 0', flexWrap: 'wrap' }}>
            <span>
              {s.kind === 'wheelchair' ? '♿' : '🍽'} <strong>{s.passenger_name}</strong> — {label(s.code)} <span style={{ fontFamily: 'monospace', color: '#64748b' }}>({s.code})</span>
              {s.error && <span style={{ display: 'block', fontSize: '0.72rem', color: '#991b1b' }}>{s.error}</span>}
            </span>
            <span style={{ fontSize: '0.7rem', fontWeight: 700, color: st.color, background: st.bg, padding: '0.15rem 0.5rem', borderRadius: '20px', whiteSpace: 'nowrap' }}>{st.text}</span>
          </div>
        );
      })}
    </div>
  );
}
