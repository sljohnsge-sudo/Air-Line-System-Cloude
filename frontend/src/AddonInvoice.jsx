// Detailed invoice for seats/extras paid after booking (Manage My Booking →
// card payment → EMD). Opened from the payment confirmation and from
// Booking Status; GET /api/manage-booking/invoice/{addon_id}. Everything on
// it comes from our payment record and the airline's documents — nothing is
// worked out here. "Print / Save as PDF" prints just the invoice.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import TickBookLogo from './TickBookLogo.jsx';
import { fmtDate, fmtDateTime } from './dateFormat.js';

const money = (cur, amt) => `${cur || ''} ${Number(amt || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();
const th = { textAlign: 'left', padding: '0.45rem 0.5rem', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#64748b', borderBottom: '2px solid #e2e8f0' };
const td = { padding: '0.5rem', fontSize: '0.8rem', borderBottom: '1px solid #f1f5f9', verticalAlign: 'top' };

export default function AddonInvoice({ addonId, apiBase, fetchWithRetry, authToken, onClose }) {
  const [inv, setInv] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const res = await fetchWithRetry(`${apiBase}/manage-booking/invoice/${encodeURIComponent(addonId)}`, {
          headers: { Authorization: `Bearer ${authToken}` },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The invoice could not be loaded.');
        setInv(data);
      } catch (e) { setError(e.message); }
    })();
  }, [addonId]); // eslint-disable-line react-hooks/exhaustive-deps

  return createPortal(
    <div className="addon-invoice-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="addon-invoice" role="dialog" aria-modal="true" aria-label="Invoice">
        <div className="addon-invoice-actions">
          <button type="button" className="btn btn-primary btn-sm" disabled={!inv} onClick={() => window.print()}>Print / Save as PDF</button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {!inv && !error && <div style={{ padding: '2rem 0', textAlign: 'center', color: '#64748b' }}>Loading your invoice…</div>}
        {inv && (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap', borderBottom: '3px solid #123B70', paddingBottom: '0.9rem' }}>
              <div>
                <TickBookLogo size={40} />
                <div style={{ fontSize: '0.72rem', color: '#64748b', marginTop: '0.35rem' }}>Customer support: 011 222 2222</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#123B70' }}>INVOICE</div>
                <div style={{ fontSize: '0.78rem', color: '#334155', lineHeight: 1.6 }}>
                  Invoice no. <strong>{inv.invoice_number}</strong><br />
                  Date: <strong>{inv.issued_at ? fmtDateTime(inv.issued_at) : '—'}</strong><br />
                  <span style={{ display: 'inline-block', marginTop: '0.2rem', padding: '0.1rem 0.6rem', borderRadius: '999px', background: '#dcfce7', color: '#166534', fontWeight: 800, fontSize: '0.72rem' }}>PAID</span>
                </div>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.9rem', margin: '1rem 0' }}>
              <div>
                <div style={th}>Billed to</div>
                <div style={{ fontSize: '0.82rem', padding: '0.4rem 0.5rem', lineHeight: 1.5 }}>
                  {inv.billed_to?.name && <strong>{inv.billed_to.name}<br /></strong>}
                  {inv.billed_to?.email}
                </div>
              </div>
              <div>
                <div style={th}>Booking</div>
                <div style={{ fontSize: '0.82rem', padding: '0.4rem 0.5rem', lineHeight: 1.5 }}>
                  Booking reference (PNR): <strong>{inv.locator}</strong><br />
                  {inv.airline_locator && <>Airline reference: <strong>{inv.airline_locator}</strong><br /></>}
                  {inv.tickets?.length > 0 && <>E-ticket: <strong>{inv.tickets.join(', ')}</strong></>}
                </div>
              </div>
            </div>

            <div style={{ fontSize: '0.85rem', fontWeight: 800, color: '#0f172a', margin: '0.3rem 0 0.2rem' }}>Seats and extras</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>#</th>
                    <th style={th}>Item</th>
                    <th style={th}>Passenger</th>
                    <th style={th}>Flight</th>
                    <th style={th}>Airline document (EMD)</th>
                    <th style={{ ...th, textAlign: 'right' }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {inv.lines.map((l, i) => (
                    <tr key={i}>
                      <td style={td}>{i + 1}</td>
                      <td style={td}>
                        <strong>{l.description}</strong>
                        {l.airline_description && l.airline_description.toUpperCase() !== (l.description || '').toUpperCase() && (
                          <div style={{ fontSize: '0.7rem', color: '#64748b' }}>{l.airline_description}</div>
                        )}
                        {l.refundable === false && <div style={{ fontSize: '0.7rem', color: '#9a3412' }}>Non-refundable</div>}
                      </td>
                      <td style={td}>{l.passenger || '—'}</td>
                      <td style={td}>
                        {l.flights.length ? l.flights.map((f, j) => (
                          <div key={j}>{f.flight} {f.from} → {f.to}{f.date ? <span style={{ color: '#64748b' }}> · {fmtDate(f.date)}</span> : null}</div>
                        )) : '—'}
                      </td>
                      <td style={td}>{l.emd || '—'}</td>
                      <td style={{ ...td, textAlign: 'right', whiteSpace: 'nowrap' }}>{money(inv.currency, l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={5} style={{ ...td, textAlign: 'right', fontWeight: 800, borderBottom: 'none', fontSize: '0.9rem' }}>Total paid</td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 800, borderBottom: 'none', fontSize: '0.9rem', whiteSpace: 'nowrap' }}>{money(inv.currency, inv.total)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.9rem', marginTop: '1rem' }}>
              <div>
                <div style={th}>Payment</div>
                <div style={{ fontSize: '0.82rem', padding: '0.4rem 0.5rem', lineHeight: 1.5 }}>
                  Paid by {inv.payment?.method?.toLowerCase() || 'card'} on {inv.issued_at ? fmtDateTime(inv.issued_at) : '—'}<br />
                  {inv.payment?.reference && <>Payment reference: <strong>{inv.payment.reference}</strong></>}
                </div>
              </div>
            </div>
            <div style={{ marginTop: '1rem', fontSize: '0.7rem', color: '#64748b', lineHeight: 1.5 }}>
              Amounts are the total charged in {inv.currency}. Each item is issued by the airline as an EMD (Electronic Miscellaneous
              Document) on your booking — keep this invoice with your e-ticket. This invoice covers seats and extras added after
              booking only; your flight ticket has its own receipt.
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
