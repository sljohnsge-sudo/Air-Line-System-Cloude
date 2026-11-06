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
// { meals, wheelchair, note, mealsNotServed }. brandAttributes are the fare's
// benefits from Travelport's search (e.g. { classification: 'Meals',
// inclusion: 'Not Offered' }): a fare with no meal service can't take a
// special-meal request. These requests are free service requests (SSRs) —
// nothing is charged for them; paid meals are a separate extra.
export function ssrSupport(rawOffering, brandAttributes = []) {
  const legs = legsOf(rawOffering);
  const isGds = legs.length > 0 && legs.every(l => l.fare_source === 'GDS');
  const carrier = legs[0]?.segments?.[0]?.carrier;
  // Emirates NDC books and tickets in one Instant Pay step, which sends no SSRs.
  if (!isGds && carrier === 'EK') {
    return { meals: false, wheelchair: false, note: 'Meal and wheelchair requests for this Emirates fare can be made with the airline after booking.' };
  }
  const mealsNotServed = (brandAttributes || []).some(a =>
    /meal/i.test(a?.classification || '') && /not offered/i.test(a?.inclusion || ''));
  if (mealsNotServed) {
    return { meals: false, wheelchair: true, mealsNotServed: true,
      note: 'This fare has no meal service, so special meals can\'t be requested. Wheelchair assistance is free of charge — the airline confirms it.' };
  }
  const meals = isGds || NDC_MEAL_CARRIERS.includes(carrier);
  return { meals, wheelchair: true, note: meals ? '' : 'This airline doesn\'t take meal requests online for this fare — you can ask the airline after booking. Wheelchair assistance is free of charge.' };
}

// What each airline does with a wheelchair request, from its live answers
// on our bookings (Travelport pre-prod, Oct-Nov 2026): Qatar confirms
// (adding "PLS ACQUIRE MEDICAL CLEARENCE IF REQUIRED"), Emirates confirms,
// SriLankan keeps it pending (confirmed closer to departure). A passenger
// who must be carried (WCHC) needs the airline's medical clearance.
const WHEELCHAIR_BY_AIRLINE = {
  QR: 'Qatar Airways confirms wheelchair requests online. It may ask for medical clearance (a MEDIF form) depending on the passenger\'s condition.',
  EK: 'Emirates confirms wheelchair requests online.',
  UL: 'SriLankan Airlines records the request with your booking and confirms it closer to departure — you can check it in Manage my booking.',
};

// The condition shown under the wheelchair choice for this fare's airline.
export function wheelchairCondition(rawOffering, code) {
  if (!code) return '';
  const carrier = legsOf(rawOffering)[0]?.segments?.[0]?.carrier;
  const airline = WHEELCHAIR_BY_AIRLINE[carrier] || 'The airline confirms wheelchair requests itself — the answer shows in Manage my booking.';
  const carried = code === 'WCHC'
    ? ' A passenger who needs to be carried to and from the seat usually needs the airline\'s medical clearance (MEDIF) and may need to travel with a companion — please contact the airline at least 48 hours before departure.'
    : '';
  return `${airline}${carried} Wheelchair assistance is free of charge.`;
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
              {s.kind === 'wheelchair' ? '♿' : '🍽'} <strong>{s.passenger_name}</strong> — {label(s.code)} <span style={{ fontFamily: 'var(--font-body)', color: '#64748b' }}>({s.code})</span>
              {s.error && <span style={{ display: 'block', fontSize: '0.72rem', color: '#991b1b' }}>{s.error}</span>}
            </span>
            <span style={{ fontSize: '0.7rem', fontWeight: 700, color: st.color, background: st.bg, padding: '0.15rem 0.5rem', borderRadius: '20px', whiteSpace: 'nowrap' }}>{st.text}</span>
          </div>
        );
      })}
    </div>
  );
}
