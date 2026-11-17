// Customer Portal → My Bookings as compact line items (one row per booking)
// instead of boarding-pass cards. A row is a button: click / Enter / Space
// opens that booking's detail view (BookingDetailView). Layout is a CSS grid
// (.booking-lines in index.css) that stacks each row into two lines on a phone.
import { cleanPassengerName } from './BookingCard.jsx';
import { fmtDateTime } from './dateFormat.js';

export const CANCELLED = ['Cancelled', 'Voided', 'Refunded', 'Airline Cancelled'];

// Paid but not ticketed (Amadeus): the refund request's state, for the small
// line under the "Not ticketed" badge.
export const refundNote = (b) => (b.refund_request?.status === 'resolved' ? 'Refunded'
  : b.refund_request?.status === 'pending' ? 'Refund in progress' : '');

// A round trip is saved as CMB → CMB; its legs give the real destination.
export const routeOf = (b) => {
  const legs = Array.isArray(b.legs) ? b.legs : [];
  if (legs.length === 2 && legs[1].arrival_airport === legs[0].departure_airport) {
    return `${legs[0].departure_airport} ⇄ ${legs[0].arrival_airport}`;
  }
  if (legs.length > 1) {
    return [legs[0].departure_airport, ...legs.map(l => l.arrival_airport)].join(' → ');
  }
  return `${b.departure_airport} → ${b.arrival_airport}`;
};

// Same rule as the backend refresh: the trip's last flight is today or later.
export const isUpcoming = (b) => {
  const legs = Array.isArray(b.legs) ? b.legs : [];
  const last = String((legs.length ? legs[legs.length - 1].departure_time : null) || b.departure_time || '');
  return last.slice(0, 10) >= new Date().toISOString().slice(0, 10);
};

const time = (iso) => fmtDateTime(iso);

// 17/11/2026: a booking without a ticket comes from the server without its
// PNR (pay_ref instead) - it is listed as "No ticket yet" and opens the pay
// pop-up (PayTicketDialog), never its details.
export const isUnpaid = (b) => !!b.unpaid || !b.locator_code;
export const bookingKey = (b) => b.locator_code || b.pay_ref || b.id;
export const isClosed = (b, status) => CANCELLED.includes(status) || !!b.not_ticketed
  || b.payment_state === 'expired' || b.payment_state === 'refund';
export const unpaidNote = (b) => ({
  awaiting_payment: 'Pay to issue the ticket', paid_pending: 'Ticket being issued',
  offline_pending: 'Payment being confirmed', expired: 'Released by the airline',
}[b.payment_state] || (b.not_ticketed ? refundNote(b) : ''));

// liveStatuses: bookingKey -> {checked, status, retrieved_at, missing, error}
// from /customers/me/bookings/refresh-status (checked live on every visit).
export default function BookingLineList({ bookings, liveStatuses = {}, refreshing = false, onOpen }) {
  return (
    <div className="booking-lines glass-panel">
      <div className="booking-line booking-line-head" aria-hidden="true">
        <span className="bl-flight">Flight</span>
        <span className="bl-route">Route</span>
        <span className="bl-dep">Departure</span>
        <span className="bl-pnr">PNR / Ticket</span>
        <span className="bl-pax">Passenger</span>
        <span className="bl-fare">Fare</span>
        <span className="bl-status">Status</span>
        <span className="bl-go"></span>
      </div>
      {bookings.map(b => {
        const unpaid = isUnpaid(b);
        const live = liveStatuses[bookingKey(b)];
        const status = live?.checked && !unpaid ? live.status : b.status;
        // Paid but not ticketed (Amadeus) or an unpaid booking the airline
        // released: shown like a closed booking.
        const cancelled = isClosed(b, status);
        const open = () => onOpen(b);
        return (
          <div key={b.id} role="button" tabIndex={0}
               className={`booking-line ${cancelled ? 'is-cancelled' : ''}`}
               aria-label={unpaid
                 ? `Booking without a ticket, ${b.airline} ${b.flight_number}, ${routeOf(b)}, ${status}. Open to pay.`
                 : `Booking ${b.locator_code}, ${b.airline} ${b.flight_number}, ${routeOf(b)}, ${status}. Open details.`}
               onClick={open}
               onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}>
            <span className="bl-flight">
              <strong>{b.airline}</strong>
              <small>{b.flight_number}{b.cabin_class ? ` · ${b.cabin_class}` : ''}</small>
            </span>
            <span className="bl-route"><strong>{routeOf(b)}</strong></span>
            <span className="bl-dep">{fmtDateTime(b.departure_time)}</span>
            {unpaid
              ? <span className="bl-pnr"><strong className="bl-noticket">No ticket yet</strong><small className="bl-ticket">{unpaidNote(b)}</small></span>
              : <span className="bl-pnr"><strong>{b.locator_code}</strong><small className="bl-ticket">{b.ticket_number || 'Not ticketed'}</small></span>}
            <span className="bl-pax">{cleanPassengerName(b.passenger_name)}</span>
            <span className="bl-fare"><strong>{b.currency} {Number(b.total_fare || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>
            <span className="bl-status">
              <span className={`pass-status-badge ${cancelled ? 'cancelled' : unpaid ? 'unpaid' : status?.toLowerCase()}`}>{b.not_ticketed ? 'Not ticketed' : status}</span>
              <small>
                {unpaid ? (b.payment_state === 'awaiting_payment' ? <span className="bl-paylink">Pay now ›</span> : '')
                  : b.not_ticketed ? refundNote(b)
                  : live?.checked ? <span title={b.source === 'AD' ? 'Checked live in Amadeus' : 'Checked live with Travelport and the airline'}>✓ Live {time(live.retrieved_at)}</span>
                  : live?.missing ? <span style={{ color: '#b45309', fontWeight: 700 }} title="Travelport no longer holds this booking — please contact us">⚠ Not on Travelport</span>
                  : live ? <span title={live.error}>Couldn't check live</span>
                  : refreshing && isUpcoming(b) && !CANCELLED.includes(b.status) ? 'Checking…' : ''}
              </small>
            </span>
            <span className="bl-go" aria-hidden="true">›</span>
          </div>
        );
      })}
    </div>
  );
}
