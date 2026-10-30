// Customer Portal → My Bookings as compact line items (one row per booking)
// instead of boarding-pass cards. A row is a button: click / Enter / Space
// opens that booking's detail view (BookingDetailView). Layout is a CSS grid
// (.booking-lines in index.css) that stacks each row into two lines on a phone.
import { cleanPassengerName } from './BookingCard.jsx';

const CANCELLED = ['Cancelled', 'Voided', 'Refunded', 'Airline Cancelled'];

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
const isUpcoming = (b) => {
  const legs = Array.isArray(b.legs) ? b.legs : [];
  const last = String((legs.length ? legs[legs.length - 1].departure_time : null) || b.departure_time || '');
  return last.slice(0, 10) >= new Date().toISOString().slice(0, 10);
};

const time = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return ''; } };

// liveStatuses: locator -> {checked, status, retrieved_at, missing, error}
// from /customers/me/bookings/refresh-status (upcoming bookings only).
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
        const live = liveStatuses[b.locator_code];
        const status = live?.checked ? live.status : b.status;
        const cancelled = CANCELLED.includes(status);
        const open = () => onOpen(b);
        return (
          <div key={b.id} role="button" tabIndex={0}
               className={`booking-line ${cancelled ? 'is-cancelled' : ''}`}
               aria-label={`Booking ${b.locator_code}, ${b.airline} ${b.flight_number}, ${routeOf(b)}, ${status}. Open details.`}
               onClick={open}
               onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}>
            <span className="bl-flight">
              <strong>{b.airline}</strong>
              <small>{b.flight_number}{b.cabin_class ? ` · ${b.cabin_class}` : ''}</small>
            </span>
            <span className="bl-route"><strong>{routeOf(b)}</strong></span>
            <span className="bl-dep">{(b.departure_time || '').replace('T', ' ').slice(0, 16)}</span>
            <span className="bl-pnr"><strong>{b.locator_code}</strong><small className="bl-ticket">{b.ticket_number || 'Not ticketed'}</small></span>
            <span className="bl-pax">{cleanPassengerName(b.passenger_name)}</span>
            <span className="bl-fare"><strong>{b.currency} {Number(b.total_fare || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>
            <span className="bl-status">
              <span className={`pass-status-badge ${status?.toLowerCase()}`}>{status}</span>
              <small>
                {live?.checked ? <span title="Checked live with Travelport and the airline">✓ Live {time(live.retrieved_at)}</span>
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
