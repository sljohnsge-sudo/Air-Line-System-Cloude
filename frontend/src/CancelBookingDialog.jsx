// Staff-only, two-step cancellation of a Travelport booking — used by the
// All Bookings list (App.jsx) and Cancellation Requests (AdminPortal.jsx).
// "Check refund" asks the backend what Travelport would do (void a same-day
// GDS ticket, NDC Refund Quote, or cancel a held booking) without changing
// anything; "Confirm cancel" then does it. If Travelport's answer moved in
// between, the backend refuses with the new quote and staff confirm again.
import { useState } from 'react';

const ACTION_LABELS = {
  void: 'Void ticket — full refund',
  refund: 'Cancel ticket and refund',
  cancel: 'Cancel held booking',
  not_supported: 'Not possible online',
};

const CONFIRM_LABELS = {
  void: 'Confirm void',
  refund: 'Confirm cancel & refund',
  cancel: 'Confirm cancel',
};

const money = (currency, amount) =>
  amount === null || amount === undefined
    ? '—'
    : `${currency || ''} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();

const detailText = (data, fallback) => {
  const d = data?.detail;
  if (!d) return fallback;
  return typeof d === 'string' ? d : (d.message || JSON.stringify(d));
};

export default function CancelBookingDialog({ locator, apiBase, fetchWithRetry, authToken, cancellationRequestId, onClose, onCancelled }) {
  const [step, setStep] = useState('idle'); // idle | checking | quoted | confirming | done
  const [quote, setQuote] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` };
  const readJson = async (res) => { try { return await res.json(); } catch { return {}; } };

  const checkRefund = async () => {
    setStep('checking');
    setError('');
    setNotice('');
    try {
      const res = await fetchWithRetry(`${apiBase}/bookings/${locator}/cancel/quote`, { method: 'POST', headers });
      const data = await readJson(res);
      if (!res.ok) throw new Error(detailText(data, 'Could not get an answer from Travelport'));
      setQuote(data);
      setStep('quoted');
    } catch (err) {
      setError(err.message || String(err));
      setStep(quote ? 'quoted' : 'idle');
    }
  };

  const confirmCancel = async () => {
    setStep('confirming');
    setError('');
    setNotice('');
    try {
      const res = await fetchWithRetry(`${apiBase}/bookings/${locator}/cancel`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ action: quote.action, amount: quote.amount, cancellation_request_id: cancellationRequestId || null }),
      });
      const data = await readJson(res);
      if (res.status === 409 && data?.detail?.quote) {
        setQuote(data.detail.quote);
        setNotice(data.detail.message);
        setStep('quoted');
        return;
      }
      if (!res.ok) throw new Error(detailText(data, 'Cancellation failed'));
      setResult(data);
      setStep('done');
      onCancelled?.(data);
    } catch (err) {
      setError(err.message || String(err));
      setStep('quoted');
    }
  };

  const busy = step === 'checking' || step === 'confirming';
  const documents = quote ? [
    ...(quote.tickets || []).map(t => ({ ...t, kind: 'Ticket' })),
    ...(quote.emds || []).map(e => ({ ...e, kind: 'EMD' })),
  ] : [];

  return (
    <div className="modal-overlay animate-fade-only" onClick={() => !busy && onClose()}>
      <div className="modal-content glass-panel animate-fade" style={{ maxWidth: '620px' }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Cancel booking {locator}</h3>
          <button type="button" className="close-btn" disabled={busy} onClick={onClose}>×</button>
        </div>

        <div style={{ padding: '1.25rem 1.5rem' }}>
          {error && <div className="error-banner" style={{ marginBottom: '1rem' }}>{error}</div>}
          {notice && (
            <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '8px', padding: '0.6rem 0.85rem', fontSize: '0.82rem', color: '#78350f', marginBottom: '1rem' }}>
              {notice}
            </div>
          )}

          {step === 'idle' && (
            <p style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', margin: 0 }}>
              Check what Travelport would do first: void, refund amount, or a held-booking cancel.
              Nothing changes until you confirm.
            </p>
          )}

          {busy && (
            <div className="loading-state" style={{ padding: '1.5rem 0' }}>
              <div className="spinner"></div>
              <p>{step === 'checking' ? 'Asking Travelport…' : 'Cancelling on Travelport — keep this window open.'}</p>
            </div>
          )}

          {step === 'quoted' && quote && (
            <>
              <div className="modal-flight-summary" style={{ margin: '0 0 1rem' }}>
                <div className="summary-col">
                  <span className="label">Content</span>
                  <span className="val">{quote.content_source}</span>
                </div>
                <div className="summary-col">
                  <span className="label">Travelport says</span>
                  <span className="val">{quote.refund?.void_period ? ACTION_LABELS.void : (ACTION_LABELS[quote.action] || quote.action)}</span>
                </div>
                <div className="summary-col">
                  <span className="label">{quote.action === 'void' ? 'Voided value' : 'Refund'}</span>
                  <span className="val highlight">{quote.action === 'cancel' ? 'Nothing paid' : money(quote.currency, quote.amount)}</span>
                </div>
              </div>

              <p style={{
                fontSize: '0.85rem', margin: '0 0 1rem', lineHeight: 1.5,
                color: quote.can_cancel ? 'var(--text-secondary)' : '#78350f',
                ...(quote.can_cancel ? {} : { background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '8px', padding: '0.6rem 0.85rem' }),
              }}>
                {quote.message}
              </p>

              {documents.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', marginBottom: '1rem' }}>
                  {documents.map(d => (
                    <div key={d.number} style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem', fontSize: '0.8rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '0.45rem 0.7rem' }}>
                      <span><strong>{d.kind}</strong> {d.number} · {d.passenger_type}{d.description ? ` · ${d.description}` : ''}</span>
                      <span style={{ color: 'var(--text-muted)' }}>
                        {d.ticketed_date ? `Issued ${d.ticketed_date} · ` : ''}
                        {d.coupon_statuses ? `${d.coupon_statuses.join(', ')} · ` : ''}
                        {money(d.currency || quote.currency, d.amount)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {quote.trace_id && (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Travelport TraceId {quote.trace_id}</div>
              )}
            </>
          )}

          {step === 'done' && result && (
            <div style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px', padding: '0.85rem 1rem', fontSize: '0.88rem', color: '#166534' }}>
              <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>
                {result.status} on Travelport{result.amount !== null && result.amount !== undefined ? ` — ${money(result.currency, result.amount)}` : ''}
              </div>
              <div>
                The booking record now shows {result.status}.
                {cancellationRequestId ? ' The cancellation request was marked resolved.' : ''}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>Travelport TraceId {result.trace_id}</div>
            </div>
          )}

          <div className="modal-actions">
            {step === 'done' || (step === 'quoted' && quote && !quote.can_cancel) ? (
              <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
            ) : (
              <>
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={step === 'quoted' ? checkRefund : onClose}>
                  {step === 'quoted' ? 'Check again' : 'Close'}
                </button>
                {step === 'quoted' && quote?.can_cancel ? (
                  <button className="btn btn-danger btn-sm" disabled={busy} onClick={confirmCancel}>
                    {CONFIRM_LABELS[quote.action] || 'Confirm cancel'}
                  </button>
                ) : (
                  <button className="btn btn-primary btn-sm" disabled={busy} onClick={checkRefund}>Check refund</button>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
