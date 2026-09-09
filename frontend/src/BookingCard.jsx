// Shared boarding-pass card — used by both the guest "My Bookings" tab and
// the logged-in Customer Portal, so the markup only exists in one place.

const cleanPassengerName = (name) => {
  if (!name) return '';
  const parts = name.trim().toUpperCase().split(/\s+/);
  const titles = ['MR', 'MRS', 'MS', 'MSTR', 'DR', 'PROF', 'MISS'];

  let foundTitle = '';
  let cleanParts = [];

  for (let part of parts) {
    let matchedTitle = '';
    for (const title of titles) {
      if (part === title) {
        matchedTitle = title;
        break;
      }
    }
    if (matchedTitle) {
      foundTitle = matchedTitle;
    } else {
      cleanParts.push(part);
    }
  }

  const rejoined = cleanParts.join(' ');
  return foundTitle ? `${foundTitle} ${rejoined}` : rejoined;
};

export default function BookingCard({ booking: b, onViewTicket, onCancelBooking, onRequestCancellation }) {
  return (
    <div className={`boarding-pass glass-panel ${b.status === 'Cancelled' ? 'cancelled-pass' : ''} animate-fade`}>
      <div className="pass-row header-row">
        <div className="pass-airline">
          <span className="pass-airline-name">{b.airline}</span>
          <span className="pass-flight-badge">{b.flight_number}</span>
        </div>
        <div className={`pass-status-badge ${b.status?.toLowerCase()}`}>{b.status}</div>
      </div>
      <div className="pass-row route-row">
        <div className="route-endpoint"><span className="route-city">{b.departure_airport}</span><span className="route-label">DEPARTURE</span></div>
        <div className="route-arrow"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg></div>
        <div className="route-endpoint"><span className="route-city">{b.arrival_airport}</span><span className="route-label">ARRIVAL</span></div>
      </div>
      <div className="pass-details-grid">
        <div className="detail-item"><span className="detail-label">PASSENGER</span><span className="detail-val">{cleanPassengerName(b.passenger_name)}</span></div>
        <div className="detail-item"><span className="detail-label">PNR / LOCATOR</span><span className="detail-val highlight">{b.locator_code}</span></div>
        <div className="detail-item"><span className="detail-label">TICKET NO</span><span className="detail-val">{b.ticket_number || '—'}</span></div>
        <div className="detail-item"><span className="detail-label">DEPARTURE</span><span className="detail-val">{b.departure_time}</span></div>
        <div className="detail-item"><span className="detail-label">CABIN CLASS</span><span className="detail-val">{b.cabin_class}</span></div>
        <div className="detail-item"><span className="detail-label">FARE</span><span className="detail-val highlight">{b.currency} {b.total_fare?.toFixed(2)}</span></div>
      </div>
      <div className="pass-footer" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <div className="pass-barcode-box" style={{ flex: 1 }}><span className="barcode-label">PNR: {b.locator_code}</span></div>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => onViewTicket(b)}>📋 View/Print Ticket</button>
          {b.status !== 'Cancelled' && onCancelBooking && (
            <button className="btn btn-danger btn-sm" onClick={() => onCancelBooking(b.locator_code)}>Cancel Booking</button>
          )}
          {b.status !== 'Cancelled' && onRequestCancellation && (
            <button className="btn btn-secondary btn-sm" onClick={() => onRequestCancellation(b)}>Request Cancellation</button>
          )}
        </div>
      </div>
    </div>
  );
}
