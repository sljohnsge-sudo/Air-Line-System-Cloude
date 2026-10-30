// Customer Portal → My Bookings as compact line items (one row per booking)
// instead of boarding-pass cards. "Live status" opens LiveBookingDialog,
// which reads the booking straight from Travelport and the airline.
import { cleanPassengerName } from './BookingCard.jsx';

const CANCELLED = ['Cancelled', 'Voided', 'Refunded', 'Airline Cancelled'];

// A round trip is saved as CMB → CMB; its legs give the real destination.
const routeOf = (b) => {
  const legs = Array.isArray(b.legs) ? b.legs : [];
  if (legs.length === 2 && legs[1].arrival_airport === legs[0].departure_airport) {
    return `${legs[0].departure_airport} ⇄ ${legs[0].arrival_airport}`;
  }
  if (legs.length > 1) {
    return [legs[0].departure_airport, ...legs.map(l => l.arrival_airport)].join(' → ');
  }
  return `${b.departure_airport} → ${b.arrival_airport}`;
};

const cell = { padding: '0.5rem 0.6rem', borderBottom: '1px solid var(--border-color)', fontSize: '0.8rem', verticalAlign: 'middle' };
const head = { ...cell, fontSize: '0.62rem', fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', background: '#f8fafc', textAlign: 'left', whiteSpace: 'nowrap' };

// Same rule as the backend refresh: the trip's last flight is today or later.
const isUpcoming = (b) => {
  const legs = Array.isArray(b.legs) ? b.legs : [];
  const last = String((legs.length ? legs[legs.length - 1].departure_time : null) || b.departure_time || '');
  return last.slice(0, 10) >= new Date().toISOString().slice(0, 10);
};

const time = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return ''; } };

// liveStatuses: locator -> {checked, status, retrieved_at, error} from
// /customers/me/bookings/refresh-status (upcoming bookings only).
export default function BookingLineList({ bookings, liveStatuses = {}, refreshing = false, onLiveStatus, onViewTicket, onRequestCancellation }) {
  return (
    <div className="glass-panel" style={{ overflowX: 'auto', padding: 0 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '880px' }}>
        <thead>
          <tr>
            <th style={head}>Flight</th>
            <th style={head}>Route</th>
            <th style={head}>Departure</th>
            <th style={head}>Passenger</th>
            <th style={head}>PNR</th>
            <th style={head}>Ticket</th>
            <th style={head}>Status</th>
            <th style={{ ...head, textAlign: 'right' }}>Fare</th>
            <th style={{ ...head, textAlign: 'right' }}></th>
          </tr>
        </thead>
        <tbody>
          {bookings.map(b => {
            const live = liveStatuses[b.locator_code];
            const status = live?.checked ? live.status : b.status;
            const cancelled = CANCELLED.includes(status);
            return (
              <tr key={b.id} style={{ opacity: cancelled ? 0.75 : 1 }}>
                <td style={cell}>
                  <div style={{ fontWeight: 700 }}>{b.airline}</div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{b.flight_number}{b.cabin_class ? ` · ${b.cabin_class}` : ''}</div>
                </td>
                <td style={{ ...cell, fontWeight: 700, whiteSpace: 'nowrap' }}>{routeOf(b)}</td>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{(b.departure_time || '').replace('T', ' ').slice(0, 16)}</td>
                <td style={cell}>{cleanPassengerName(b.passenger_name)}</td>
                <td style={{ ...cell, fontWeight: 700, color: 'var(--gs-red, #c8102e)' }}>{b.locator_code}</td>
                <td style={{ ...cell, fontFamily: 'monospace', fontSize: '0.75rem' }}>{b.ticket_number || '—'}</td>
                <td style={cell}>
                  <span className={`pass-status-badge ${status?.toLowerCase()}`} style={{ fontSize: '0.62rem' }}>{status}</span>
                  <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: '0.15rem', whiteSpace: 'nowrap' }}>
                    {live?.checked ? <span title="Checked live with Travelport and the airline">✓ Live {time(live.retrieved_at)}</span>
                      : live?.missing ? <span style={{ color: '#b45309', fontWeight: 700 }} title="Travelport no longer holds this booking — please contact us">⚠ Not found on Travelport</span>
                      : live ? <span title={live.error}>Couldn't check live</span>
                      : refreshing && isUpcoming(b) && !CANCELLED.includes(b.status) ? 'Checking…' : ''}
                  </div>
                </td>
                <td style={{ ...cell, textAlign: 'right', whiteSpace: 'nowrap', fontWeight: 700 }}>
                  {b.currency} {Number(b.total_fare || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </td>
                <td style={{ ...cell, textAlign: 'right' }}>
                  <div style={{ display: 'flex', gap: '0.3rem', justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
                    <button className="btn btn-primary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.72rem', padding: '0.3rem 0.55rem' }}
                            title="Check seats, extras and ticket status live with Travelport and the airline"
                            onClick={() => onLiveStatus(b)}>Live status</button>
                    <button className="btn btn-secondary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.72rem', padding: '0.3rem 0.55rem' }}
                            onClick={() => onViewTicket(b)}>Ticket</button>
                    {!cancelled && onRequestCancellation && (
                      <button className="btn btn-secondary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.72rem', padding: '0.3rem 0.55rem' }}
                              onClick={() => onRequestCancellation(b)}>Cancel…</button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
