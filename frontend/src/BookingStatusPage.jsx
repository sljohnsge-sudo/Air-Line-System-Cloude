// Customer Portal → Booking Status: look a booking up by PNR or 13-digit
// ticket number plus the passenger's last name, and see it exactly as
// Travelport and the airline hold it (POST /api/booking-status/lookup) —
// flights, ticket coupons, seats, extras/EMDs, special requests. The booking
// doesn't have to be one made here or linked to this account. The last name
// must match a passenger on the booking, so a reference alone never reveals
// anyone's itinerary. Manage My Booking (ManageMyBooking.jsx) opens below.
import { useEffect, useState } from 'react';
import { LiveStatusDetails } from './LiveBookingPanel.jsx';
import ManageMyBooking, { startAddonPayment, formatDeadline } from './ManageMyBooking.jsx';
import { fmtDateTime } from './dateFormat.js';
import AddonInvoice from './AddonInvoice.jsx';

// prefill: {query, lastName, key} — set when opened from a My Bookings detail page; looks it up at once.
export default function BookingStatusPage({ apiBase, fetchWithRetry, authToken, prefill }) {
  const [query, setQuery] = useState('');
  const [lastName, setLastName] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [paying, setPaying] = useState(false);
  const [invoiceId, setInvoiceId] = useState(null);

  // Seats/extras the airline confirmed after they were requested, on a
  // ticketed booking: price fixed server-side, then the usual card payment.
  const payDue = async () => {
    setPaying(true);
    setError('');
    try {
      const res = await fetchWithRetry(`${apiBase}/manage-booking/pay-due`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` },
        body: JSON.stringify({ manage_token: data.manage_token }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not start the payment');
      await startAddonPayment({ apiBase, fetchWithRetry, addon: body });
    } catch (err) {
      setError(err.message || String(err));
      setPaying(false);
    }
  };

  const lookup = async (e, q = query, ln = lastName) => {
    e?.preventDefault?.();
    setLoading(true);
    setError('');
    try {
      const res = await fetchWithRetry(`${apiBase}/booking-status/lookup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` },
        body: JSON.stringify({ query: q.trim(), last_name: ln.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not reach Travelport');
      setData(body);
    } catch (err) {
      setError(err.message || String(err));
      setData(null);
      setManageOpen(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (prefill?.query && prefill?.lastName) {
      setQuery(prefill.query);
      setLastName(prefill.lastName);
      lookup(null, prefill.query, prefill.lastName);
    } else if (prefill?.query) {
      setQuery(prefill.query);
    }
  }, [prefill?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  const retrieved = data?.retrieved_at ? fmtDateTime(data.retrieved_at) : '';

  return (
    <div>
      <section className="search-section glass-panel" style={{ padding: '1rem 1.1rem', marginBottom: '0.9rem' }}>
        <h3 className="results-heading" style={{ marginTop: 0 }}>Booking Status</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '-0.4rem 0 0.8rem' }}>
          See any booking live from the airline: flights, ticket, seats, extras and special requests. Enter the booking
          reference (PNR) or 13-digit ticket number, and the last name of a passenger on it — the last name keeps other
          people's bookings private.
        </p>
        <form onSubmit={lookup} style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ flex: '1 1 200px', margin: 0 }}>
            <label className="form-label">PNR or ticket number</label>
            <input className="form-input" required value={query} onChange={e => setQuery(e.target.value.toUpperCase())}
                   placeholder="e.g. HNHLRC or 6032903683733" maxLength={20} />
          </div>
          <div className="form-group" style={{ flex: '1 1 180px', margin: 0 }}>
            <label className="form-label">Passenger last name</label>
            <input className="form-input" required value={lastName} onChange={e => setLastName(e.target.value)} placeholder="As on the ticket" maxLength={60} />
          </div>
          <button className="btn btn-primary" type="submit" disabled={loading}>{loading ? 'Checking…' : 'See my status'}</button>
        </form>
        {error && <div className="error-banner" style={{ marginTop: '0.8rem' }}>{error}</div>}
      </section>

      {data && (
        <section className="glass-panel" style={{ padding: '0.9rem 1.1rem', marginBottom: '0.9rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.6rem', flexWrap: 'wrap' }}>
            <div>
              <h4 style={{ margin: 0 }}>Booking {data.locator_code}</h4>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>Straight from Travelport and the airline{retrieved ? ` · checked ${retrieved}` : ''}</div>
            </div>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button className="btn btn-secondary btn-sm" disabled={loading} onClick={() => lookup()}>Check again</button>
              {(data.can_add_special_requests || data.can_add_seats_extras) && (
                <button className="btn btn-primary btn-sm" onClick={() => setManageOpen(o => !o)}>{manageOpen ? 'Close Manage My Booking' : 'Manage my booking'}</button>
              )}
            </div>
          </div>
          {data.released_extras?.length > 0 && (
            <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '8px', padding: '0.7rem 0.9rem', margin: '0.7rem 0 0.2rem', fontSize: '0.82rem', color: '#991b1b' }}>
              <strong>Removed from your booking:</strong> {data.released_extras.join(', ')} — not paid by the deadline, so the airline no longer
              has {data.released_extras.length > 1 ? 'them' : 'it'} for you. You have not been charged. You can add it again from Manage my booking.
            </div>
          )}
          {data.payment_due && (
            <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '8px', padding: '0.7rem 0.9rem', margin: '0.7rem 0 0.2rem', fontSize: '0.82rem', color: '#78350f' }}>
              <strong>Payment due:</strong> the airline has confirmed{' '}
              {data.payment_due.items.map(i => `${i.name} (${data.payment_due.currency} ${Number(i.price).toLocaleString(undefined, { minimumFractionDigits: 2 })})`).join(', ')}.
              Pay to keep {data.payment_due.items.length > 1 ? 'them' : 'it'} — the airline then issues the document.
              {data.payment_due.deadline_utc && (
                <div style={{ marginTop: '0.35rem' }}>
                  Pay by <strong>{formatDeadline(data.payment_due.deadline_utc)}</strong>. If {data.payment_due.items.length > 1 ? 'they aren’t' : 'it isn’t'} paid
                  by then, {data.payment_due.items.length > 1 ? 'they are' : 'it is'} removed from your booking and can’t be used at the airport.
                </div>
              )}
              <div style={{ marginTop: '0.45rem' }}>
                <button className="btn btn-primary btn-sm" disabled={paying} onClick={payDue}>
                  {paying ? 'Opening payment…' : `Pay ${data.payment_due.currency} ${Number(data.payment_due.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
                </button>
              </div>
            </div>
          )}
          {data.extras_invoices?.length > 0 && (
            <div style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px', padding: '0.6rem 0.9rem', margin: '0.7rem 0 0.2rem', fontSize: '0.82rem', color: '#166534' }}>
              <strong>Paid seats and extras</strong>
              {data.extras_invoices.map(i => (
                <div key={i.invoice_number} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', marginTop: '0.35rem' }}>
                  <span>Invoice {i.invoice_number} · {i.currency} {Number(i.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}{i.paid_at ? ` · paid ${fmtDateTime(i.paid_at)}` : ''}</span>
                  <button className="btn btn-secondary btn-sm" onClick={() => setInvoiceId(i.invoice_number)}>View invoice</button>
                </div>
              ))}
            </div>
          )}
          <LiveStatusDetails data={data} />
        </section>
      )}

      {data && manageOpen && (
        <section className="glass-panel" style={{ padding: '0.9rem 1.1rem' }}>
          <h4 style={{ margin: 0 }}>Manage my booking</h4>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.74rem' }}>Add special requests, choose seats or add extras. Changes go straight to the airline.</div>
          <ManageMyBooking manageToken={data.manage_token} apiBase={apiBase} fetchWithRetry={fetchWithRetry} authToken={authToken}
                           onChanged={() => lookup()} />
        </section>
      )}
      {invoiceId && (
        <AddonInvoice addonId={invoiceId} apiBase={apiBase} fetchWithRetry={fetchWithRetry} authToken={authToken} onClose={() => setInvoiceId(null)} />
      )}
    </div>
  );
}
