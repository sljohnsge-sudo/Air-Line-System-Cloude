// "Live status" for one booking: everything here is read live from
// Travelport and the airline (GET /api/bookings/{pnr}/live) — flights with
// the airline's own segment status, tickets with coupon status, and every
// seat, extra and special request with the airline's confirmation. Nothing
// comes from our booking record.
import { useEffect, useState } from 'react';

const tone = (status) => {
  const s = (status || '').toLowerCase();
  if (['confirmed', 'openforuse', 'ok', 'hk', 'used', 'flown'].some(k => s === k)) return { bg: '#f0fdf4', fg: '#166534', bd: '#bbf7d0' };
  if (['cancelled', 'void', 'voided', 'refunded', 'rejected', 'unable'].some(k => s.includes(k))) return { bg: '#fef2f2', fg: '#991b1b', bd: '#fecaca' };
  return { bg: '#fffbeb', fg: '#92400e', bd: '#fde68a' };
};

const Chip = ({ children, status }) => {
  const t = tone(status ?? children);
  return (
    <span style={{ display: 'inline-block', fontSize: '0.66rem', fontWeight: 700, padding: '0.08rem 0.45rem', borderRadius: '999px',
                   background: t.bg, color: t.fg, border: `1px solid ${t.bd}`, whiteSpace: 'nowrap' }}>{children}</span>
  );
};

const COUPON_LABELS = { OpenForUse: 'Open for use', Void: 'Void', Refunded: 'Refunded', Used: 'Used', Flown: 'Flown', Exchanged: 'Exchanged' };

const section = { fontSize: '0.62rem', fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.9rem 0 0.35rem' };
const row = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
              fontSize: '0.8rem', padding: '0.4rem 0.6rem', border: '1px solid var(--border-color)', borderRadius: '6px', background: '#f8fafc', marginBottom: '0.3rem' };
const muted = { color: 'var(--text-muted)', fontSize: '0.72rem' };
// " · flight · passenger · EMD n" without dangling dots for missing parts.
const details = (...parts) => parts.filter(Boolean).map(p => ` · ${p}`).join('');

export default function LiveBookingDialog({ locator, apiBase, fetchWithRetry, authToken, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetchWithRetry(`${apiBase}/bookings/${locator}/live`, { headers: { Authorization: `Bearer ${authToken}` } });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not reach Travelport');
      setData(body);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [locator]); // eslint-disable-line react-hooks/exhaustive-deps

  const retrieved = data?.retrieved_at ? new Date(data.retrieved_at).toLocaleString() : '';
  const empty = (list) => !list || list.length === 0;

  return (
    <div className="modal-overlay animate-fade-only" onClick={() => !loading && onClose()}>
      <div className="modal-content glass-panel animate-fade" style={{ maxWidth: '760px', width: '95%' }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3 style={{ margin: 0 }}>Live status · {locator}</h3>
            <div style={muted}>Straight from Travelport and the airline{retrieved ? ` · checked ${retrieved}` : ''}</div>
          </div>
          <button type="button" className="close-btn" onClick={onClose}>×</button>
        </div>

        <div style={{ padding: '0.5rem 1.25rem 1.1rem', maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' }}>
          {loading && <div className="loading-state" style={{ padding: '1.5rem 0' }}><div className="spinner"></div><p>Asking Travelport and the airline…</p></div>}
          {error && !loading && <div className="error-banner">{error}</div>}

          {data && !loading && (
            <>
              {data.booking_status === 'cancelled' && (
                <div className="error-banner" style={{ marginTop: '0.5rem' }}>This booking is cancelled on Travelport{data.message ? ` — ${data.message}` : ''}.</div>
              )}
              {data.booking_status === 'airline_cancelled' && (
                <div className="error-banner" style={{ marginTop: '0.5rem' }}>The airline has cancelled the flights on this booking — please contact us.</div>
              )}
              {data.booking_status === 'airline_problem' && (
                <div className="error-banner" style={{ marginTop: '0.5rem' }}>The airline has not confirmed every flight on this booking — please contact us.</div>
              )}

              {!empty(data.airline_locators) && (
                <div style={{ ...muted, marginTop: '0.5rem' }}>
                  Airline reference: {data.airline_locators.map(a => <strong key={a.locator} style={{ color: 'var(--gs-dark)' }}>{a.airline} {a.locator} </strong>)}
                </div>
              )}

              {!empty(data.flights) && <div style={section}>Flights</div>}
              {(data.flights || []).map(f => (
                <div key={`${f.flight}-${f.sequence}`} style={row}>
                  <span><strong>{f.flight}</strong> {f.from} → {f.to} <span style={muted}>· {f.departure?.slice(0, 16)}{f.cabin ? ` · ${f.cabin}${f.class ? ` (${f.class})` : ''}` : ''}</span></span>
                  <Chip status={f.state}>{f.airline_status || 'No airline status'}{f.airline_status_code ? ` · ${f.airline_status_code}` : ''}</Chip>
                </div>
              ))}

              <div style={section}>Tickets</div>
              {empty(data.tickets) ? <div style={muted}>No ticket issued yet.</div> : data.tickets.map(t => (
                <div key={t.number} style={{ ...row, alignItems: 'flex-start' }}>
                  <span><strong style={{ fontFamily: 'monospace' }}>{t.number}</strong> <span style={muted}>{details(t.passenger, t.issued && `issued ${t.issued}`)}</span></span>
                  <span style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                    {t.error ? <span style={muted}>Coupon status not available ({t.error})</span>
                      : (t.coupons || []).map((c, i) => (
                        <Chip key={i} status={c.status}>{c.flight} {COUPON_LABELS[c.status] || c.status}</Chip>
                      ))}
                  </span>
                </div>
              ))}

              <div style={section}>Seats</div>
              {empty(data.seats) ? <div style={muted}>No seats on the booking with the airline.</div> : data.seats.map((s, i) => (
                <div key={i} style={row}>
                  <span><strong>Seat {s.seat}</strong> <span style={muted}>{details(s.flights.join(', '), s.passenger, s.emd && `EMD ${s.emd}`)}</span></span>
                  <Chip status={s.status}>{s.status}</Chip>
                </div>
              ))}

              <div style={section}>Extras</div>
              {empty(data.extras) ? <div style={muted}>No extras on the booking with the airline.</div> : data.extras.map((x, i) => (
                <div key={i} style={row}>
                  <span><strong>{x.description}</strong> <span style={muted}>{details(x.flights.join(', '), x.passenger, x.emd && `EMD ${x.emd}`)}</span></span>
                  <Chip status={x.status}>{x.status}</Chip>
                </div>
              ))}

              {!empty(data.special_services) && <div style={section}>Special requests</div>}
              {(data.special_services || []).map((s, i) => (
                <div key={i} style={row}>
                  <span><strong>{s.description}</strong> <span style={muted}>{details(s.flights.join(', '), s.passenger)}</span></span>
                  <Chip status={s.status}>{s.status}</Chip>
                </div>
              ))}

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.9rem' }}>
                <span style={{ ...muted, fontSize: '0.66rem' }}>Travelport TraceId {data.trace_id}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Check again</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
