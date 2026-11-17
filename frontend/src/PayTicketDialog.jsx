// Customer Portal (17/11/2026): what opens when the customer clicks a booking
// that has no ticket. Its PNR is never shown. The customer pays the ticket
// amount on the bank's payment page (POST /customers/me/bookings/{ref}/pay,
// which first checks the flights live with the airline); the ticket is issued
// from that payment only, when the browser comes back (App.jsx, ?pay=…).
// Seats, extras, the e-ticket and loyalty points come after the ticket.
import { useEffect, useState } from 'react';
import { routeOf } from './BookingLineList.jsx';
import { cleanPassengerName } from './BookingCard.jsx';
import { fmtDateTime } from './dateFormat.js';

const money = (currency, amount) =>
  `${currency || ''} ${Number(amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();

const TITLES = {
  awaiting_payment: 'Pay to issue your ticket',
  paid_pending: 'Payment received',
  offline_pending: 'Waiting for your payment',
  expired: 'Booking expired - not paid',
  contact_us: 'Payment needed',
  refund: 'No ticket was issued',
};

export default function PayTicketDialog({ booking: b, apiBase, fetchWithRetry, authToken, onClose, onSessionExpired }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const payable = b.payment_state === 'awaiting_payment' && !!b.pay_ref;

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const pay = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await fetchWithRetry(`${apiBase}/customers/me/bookings/${encodeURIComponent(b.pay_ref)}/pay`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.status === 401) { onSessionExpired?.(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.payment_page_url) {
        throw new Error(typeof data.detail === 'string' ? data.detail
          : 'The payment page could not be opened. Nothing has been charged - please try again.');
      }
      window.location.href = data.payment_page_url;
    } catch (e) {
      setError(e.message || String(e));
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay animate-fade-only pay-ticket-overlay" role="dialog" aria-modal="true" aria-labelledby="pay-ticket-title"
         onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="modal-content glass-panel animate-fade pay-ticket">
        <h3 id="pay-ticket-title">{TITLES[b.payment_state] || TITLES.awaiting_payment}</h3>
        <p className="pay-ticket-lead">
          {payable ? 'This booking has no ticket yet. Pay the ticket amount to issue the ticket.' : b.payment_message}
        </p>

        <div className="pay-ticket-trip">
          <div><span>Flight</span><strong>{b.airline} {b.flight_number}</strong></div>
          <div><span>Route</span><strong>{routeOf(b)}</strong></div>
          <div><span>Departure</span><strong>{fmtDateTime(b.departure_time)}</strong></div>
          <div><span>Passenger</span><strong>{cleanPassengerName(b.passenger_name)}</strong></div>
        </div>

        {payable && (
          <>
            <div className="pay-ticket-amount"><span>Ticket amount</span><strong>{money(b.currency, b.total_fare)}</strong></div>
            <p className="pay-ticket-note">
              You pay on the bank's secure payment page. We check your flights with the airline first, and the ticket is
              issued as soon as the bank confirms the payment. Your e-ticket, seats, extras and other services are
              available once the ticket is issued.
            </p>
          </>
        )}

        {error && <div className="error-banner" role="alert">{error}</div>}

        <div className="pay-ticket-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Close</button>
          {payable && (
            <button type="button" className="btn btn-primary" onClick={pay} disabled={busy}>
              {busy ? 'Opening the payment page…' : `Pay ${money(b.currency, b.total_fare)}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
