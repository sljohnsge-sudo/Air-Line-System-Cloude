// Customer Portal → My Bookings → one booking. Opened by clicking a row in
// BookingLineList. Shows the saved booking (passengers, flights, tickets,
// fare, seats and extras as booked). "Live status & manage booking" opens the
// Booking Status page (BookingStatusPage) pre-filled for this booking. The
// ticket and cancellation actions live here rather than on the list row.
import { useState } from 'react';
import { cleanPassengerName } from './BookingCard.jsx';
import { fmtDateTime } from './dateFormat.js';

const CANCELLED = ['Cancelled', 'Voided', 'Refunded', 'Airline Cancelled'];

const money = (currency, amount) =>
  amount === null || amount === undefined || amount === ''
    ? '—'
    : `${currency || ''} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();

const dt = (s) => fmtDateTime(s);

const label = { fontSize: '0.6rem', fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' };
const panel = { padding: '0.9rem 1.1rem', marginBottom: '0.9rem' };
const h4 = { margin: '0 0 0.6rem' };
const line = { display: 'flex', justifyContent: 'space-between', gap: '0.6rem', flexWrap: 'wrap', fontSize: '0.82rem', padding: '0.35rem 0', borderBottom: '1px solid var(--border-color)' };

const legsOf = (b) => {
  if (Array.isArray(b.legs) && b.legs.length) return b.legs;
  if (Array.isArray(b.segments) && b.segments.length) return [{ ...b, segments: b.segments }];
  return [{ ...b, segments: [{ flight_number: b.flight_number, departure_airport: b.departure_airport, arrival_airport: b.arrival_airport,
                               departure_time: b.departure_time, arrival_time: b.arrival_time, departure_terminal: b.departure_terminal,
                               arrival_terminal: b.arrival_terminal }] }];
};

const travelerName = (t) => cleanPassengerName(t.confirmed_name || t.full_name || `${t.first_name || t.given_name || ''} ${t.last_name || t.surname || ''}`);

export default function BookingDetailView({ booking: b, liveStatus, onBack, onViewTicket, onRequestCancellation, onLiveStatus }) {
  const [adManageNote, setAdManageNote] = useState(false);
  const status = liveStatus?.checked ? liveStatus.status : b.status;
  // Paid but the airline issued no ticket (Amadeus): a full refund request was
  // filed automatically. Shown with that request's status - nothing to manage.
  const notTicketed = !!b.not_ticketed;
  const refund = b.refund_request || null;
  const cancelled = CANCELLED.includes(status) || notTicketed;
  const legs = legsOf(b);
  const travelers = Array.isArray(b.travelers) && b.travelers.length ? b.travelers : [{ full_name: b.passenger_name, passenger_type: 'ADT' }];
  // ticket_numbers is saved as [{passenger_type, number}] in passenger order.
  const tickets = (Array.isArray(b.ticket_numbers) && b.ticket_numbers.length ? b.ticket_numbers : (b.ticket_number ? [b.ticket_number] : []))
    .map(t => (t && typeof t === 'object' ? t.number : t));
  const fb = b.fare_breakdown && typeof b.fare_breakdown === 'object' ? b.fare_breakdown : null;
  const seats = (b.addon_results?.seats || []);
  const extras = (b.addon_results?.extras?.length ? b.addon_results.extras : (b.ancillaries || []));

  return (
    <div className="booking-detail animate-fade">
      <button type="button" className="btn btn-secondary btn-sm" onClick={onBack} style={{ marginBottom: '0.8rem' }}>← Back to My Bookings</button>

      <section className="glass-panel" style={panel}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: '1.15rem', fontWeight: 800 }}>{b.airline} · {b.flight_number}</div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.15rem' }}>
              {legs.map(l => `${l.departure_airport} → ${l.arrival_airport}`).join('  ·  ')}
            </div>
          </div>
          <span className={`pass-status-badge ${status?.toLowerCase()}`}>{status}</span>
        </div>
        <div className="booking-detail-facts">
          <div><div style={label}>PNR</div><strong style={{ color: 'var(--gs-red, #c8102e)' }}>{b.locator_code}</strong></div>
          {b.airline_pnr && <div><div style={label}>Airline reference</div><strong>{String(b.airline_pnr).replace(/^[A-Z0-9]{2}_/, '')}</strong></div>}
          <div><div style={label}>Booked</div>{dt(b.booking_date) || '—'}</div>
          <div><div style={label}>Cabin</div>{b.cabin_class || '—'}{b.fare_family ? ` · ${b.fare_family}` : ''}</div>
          <div><div style={label}>Baggage</div>{b.baggage_allowance || '—'}</div>
          <div><div style={label}>Payment</div>{b.payment_method || '—'}</div>
        </div>
        {notTicketed ? (
          <div role="status" style={{ marginTop: '0.8rem', padding: '0.7rem 0.9rem', borderRadius: '8px', fontSize: '0.85rem',
                                      background: 'rgba(245, 158, 11, 0.12)', border: '1px solid rgba(245, 158, 11, 0.45)' }}>
            <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>No ticket was issued for this booking</div>
            <div>This booking has no ticket, so it can't be used to travel.
              {refund ? ` A full refund of ${money(b.currency, b.total_fare)} was requested automatically on ${dt(refund.requested_at)}.` : ''}</div>
            {refund && (
              <div style={{ marginTop: '0.35rem' }}>
                <span style={label}>Refund</span>{' '}
                <strong>{refund.status === 'resolved' ? `Refunded${refund.reviewed_at ? ` on ${dt(refund.reviewed_at)}` : ''}`
                  : refund.status === 'pending' ? 'In progress' : `Closed${refund.reviewed_at ? ` on ${dt(refund.reviewed_at)}` : ''}`}</strong>
              </div>
            )}
          </div>
        ) : (
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.8rem', alignItems: 'center' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => onViewTicket(b)}>📋 View / Print Ticket</button>
          {!cancelled && onRequestCancellation && (
            <button className="btn btn-secondary btn-sm" onClick={() => onRequestCancellation(b)}>Request Cancellation</button>
          )}
          {/* Manage booking (seats / extras after the ticket) is Travelport-only for now:
              on Amadeus bookings it is shown greyed out and says so. */}
          {b.source !== 'AD' ? (
            <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => onLiveStatus(b)}>Live status & manage booking</button>
          ) : (
            <button type="button" className="btn btn-secondary btn-sm" aria-disabled="true"
                    style={{ marginLeft: 'auto', opacity: 0.55, cursor: 'not-allowed' }}
                    title="Seats and extras are not available for this booking yet"
                    onClick={() => setAdManageNote(true)}>Seats &amp; extras</button>
          )}
          {b.source === 'AD' && adManageNote && (
            <div role="alert" style={{ flexBasis: '100%', fontSize: '0.8rem', color: '#9a3412' }}>
              Seats and extras are not available for this booking yet.
            </div>
          )}
        </div>
        )}
      </section>

      <section className="glass-panel" style={panel}>
        <h4 style={h4}>Flights</h4>
        {legs.map((leg, li) => (
          <div key={li} style={{ marginBottom: li < legs.length - 1 ? '0.7rem' : 0 }}>
            {legs.length > 1 && <div style={{ ...label, marginBottom: '0.2rem' }}>{li === 0 ? 'Departure' : li === legs.length - 1 && leg.arrival_airport === legs[0].departure_airport ? 'Return' : `Flight ${li + 1}`}</div>}
            {(leg.segments || []).map((s, si) => (
              <div key={si} style={line}>
                <span><strong>{s.flight_number}</strong> {s.departure_airport}{s.departure_terminal ? ` T${s.departure_terminal}` : ''} → {s.arrival_airport}{s.arrival_terminal ? ` T${s.arrival_terminal}` : ''}</span>
                <span style={{ color: 'var(--text-secondary)' }}>{dt(s.departure_time)} → {dt(s.arrival_time)}</span>
              </div>
            ))}
          </div>
        ))}
      </section>

      <div className="booking-detail-grid">
        <section className="glass-panel" style={panel}>
          <h4 style={h4}>Passengers</h4>
          {travelers.map((t, i) => (
            <div key={i} style={line}>
              <span><strong>{travelerName(t)}</strong> <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>{t.passenger_type || 'ADT'}</span></span>
              <span style={{ fontFamily: 'var(--font-body)', fontSize: '0.78rem' }}>{tickets[i] || (i === 0 && tickets.length === 0 ? (notTicketed ? 'Not ticketed' : 'Not ticketed yet') : '')}</span>
            </div>
          ))}
          {Array.isArray(b.emd_numbers) && b.emd_numbers.length > 0 && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
              EMDs: {b.emd_numbers.map(e => e.number || e).join(', ')}
            </div>
          )}
        </section>

        <section className="glass-panel" style={panel}>
          <h4 style={h4}>Fare</h4>
          {fb && <>
            <div style={line}><span>Base fare</span><span>{money(b.currency, fb.base_fare)}</span></div>
            {!!fb.fuel_surcharge && <div style={line}><span>Fuel surcharge</span><span>{money(b.currency, fb.fuel_surcharge)}</span></div>}
            {!!fb.other_taxes && <div style={line}><span>Taxes</span><span>{money(b.currency, fb.other_taxes)}</span></div>}
            {!!fb.total_fees && <div style={line}><span>Fees</span><span>{money(b.currency, fb.total_fees)}</span></div>}
          </>}
          {!!b.seat_charge && <div style={line}><span>Seats & extras</span><span>{money(b.currency, b.seat_charge)}</span></div>}
          <div style={{ ...line, borderBottom: 'none', fontWeight: 800 }}><span>{b.ticket_number || notTicketed ? 'Total paid' : 'Total'}</span><span>{money(b.currency, b.total_fare)}</span></div>
          {b.refund_amount !== null && b.refund_amount !== undefined && (
            <div style={{ ...line, borderBottom: 'none' }}><span>{b.status === 'Voided' ? 'Voided value' : 'Refund'}</span><span>{money(b.refund_currency, b.refund_amount)}</span></div>
          )}
        </section>
      </div>

      {(seats.length > 0 || extras.length > 0) && (
        <section className="glass-panel" style={panel}>
          <h4 style={h4}>Seats & extras you chose</h4>
          {seats.map((s, i) => (
            <div key={`s${i}`} style={line}>
              <span><strong>Seat {s.seat_number}</strong> <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>{[s.direction, s.flight_number].filter(Boolean).join(' · ')}</span></span>
              <span style={{ fontSize: '0.75rem' }}>{s.status === 'confirmed' ? 'Confirmed at booking' : s.status === 'pending' ? 'Requested' : `Not added${s.reason ? ` — ${s.reason}` : ''}`}</span>
            </div>
          ))}
          {extras.map((x, i) => (
            <div key={`x${i}`} style={line}>
              <span><strong>{x.name}</strong></span>
              <span style={{ fontSize: '0.75rem' }}>{x.status === 'confirmed' ? 'Confirmed at booking' : x.status === 'pending' ? 'Requested' : `Not added${x.reason ? ` — ${x.reason}` : ''}`}</span>
            </div>
          ))}
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>This is what was booked. The airline's current answer is under Live status & manage booking.</div>
        </section>
      )}

    </div>
  );
}
