// Admin › Amadeus Bookings (12/11/2026). Every booking made on Amadeus, and for
// one booking a live check with Cancel / Void / Refund. Each action has its
// own switch on Providers & Modules › Go-live controls (OFF until turned on).
// The Amadeus backend does the work (amadeus-backend/services/servicing_service.py):
//   Cancel  – flights cancelled, seats released; only when no ticket is valid.
//   Void    – every ticket and EMD voided (only on the day of issue), then the flights cancelled.
//   Refund  – Amadeus refunds the unused tickets minus the airline fee staff enter
//             from the fare rules; then the flights are cancelled. EMDs (seats/bags)
//             can't be refunded through Amadeus for our office: claim them from the airline.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { fmtDateTime } from './dateFormat.js';

const money = (a, c) => (a || a === 0 ? `${c || ''} ${Number(a).toLocaleString(undefined, { maximumFractionDigits: 2 })}`.trim() : '—');
const STATUS = {
  confirmed: ['Booked, not ticketed', 'warn'], ticketed: ['Ticketed', 'ok'], cancelled: ['Cancelled', 'muted'],
  voided: ['Voided', 'muted'], 'partly voided': ['Partly voided', 'bad'], refunded: ['Refunded', 'muted'],
};
const DOC_STATUS = { issued: ['Valid', 'ok'], voided: ['Voided', 'muted'], refunded: ['Refunded', 'muted'] };
const SWITCHES = [
  ['amadeus_cancel_enabled', 'Cancel'], ['amadeus_void_enabled', 'Void'], ['amadeus_refund_enabled', 'Refund'],
];
const FILTERS = [['all', 'All'], ['ticketed', 'Ticketed'], ['confirmed', 'Not ticketed'], ['done', 'Cancelled / voided / refunded']];

function useAdminApi(API_BASE, adminToken, onSessionExpired) {
  return useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}`, ...(opts.headers || {}) },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [API_BASE, adminToken, onSessionExpired]);
}

export default function AdminAmadeusBookings({ API_BASE, adminToken, onSessionExpired, onOpenSwitches }) {
  const api = useAdminApi(API_BASE, adminToken, onSessionExpired);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(null); // booking row being looked at

  const load = useCallback(() => {
    setError('');
    return api('/amadeus-bookings').then(setData).catch(e => { setError(e.message); setData(d => d || { bookings: [], switches: {} }); });
  }, [api]);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => {
    const term = q.trim().toUpperCase();
    return (data?.bookings || []).filter(b => {
      if (filter === 'done' && !['cancelled', 'voided', 'refunded', 'partly voided'].includes(b.status)) return false;
      if (filter !== 'all' && filter !== 'done' && b.status !== filter) return false;
      if (!term) return true;
      return [b.order_id, b.passenger_name, b.ticket_numbers, b.emd_numbers].some(v => (v || '').toUpperCase().includes(term));
    });
  }, [data, filter, q]);

  const sw = data?.switches || {};

  return (
    <div className="amb">
      <div className="amb-switches">
        {SWITCHES.map(([k, l]) => (
          <span key={k} className={`rl-chip ${sw[k] ? 'ok' : 'muted'}`}>{l}: {sw[k] ? 'ON' : 'OFF'}</span>
        ))}
        <span className="adm-sub">Turn these on or off in{' '}
          <button type="button" className="amb-link" onClick={onOpenSwitches}>Providers &amp; Modules › Go-live controls</button>.</span>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <div className="adm-chips">
        {FILTERS.map(([k, l]) => (
          <button key={k} className="adm-chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
        ))}
        <input className="form-input amb-search" placeholder="PNR, name or ticket no." value={q} onChange={e => setQ(e.target.value)} />
        <button className="adm-chip" onClick={load}>↻ Refresh</button>
      </div>
      {!data ? <div className="loading-state"><div className="spinner"></div></div> : rows.length === 0 ? (
        <p className="adm-sub">No Amadeus bookings here.</p>
      ) : (
        <div className="adm-table-wrap">
          <table className="adm-table">
            <thead><tr><th>PNR</th><th>Route &amp; date</th><th>Passenger</th><th>Tickets / EMDs</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map(b => {
                const [label, cls] = STATUS[b.status] || [b.status || '—', 'muted'];
                return (
                  <tr key={b.order_id}>
                    <td><strong>{b.order_id}</strong><div className="adm-sub">{fmtDateTime(b.created_at)}</div></td>
                    <td className="nowrap">{b.origin} → {b.destination}<div className="adm-sub">{b.departure_date || '—'}{b.return_date ? ` · back ${b.return_date}` : ''}</div></td>
                    <td>{b.passenger_name || '—'}{b.passenger_email && <div className="adm-sub">{b.passenger_email}</div>}</td>
                    <td className="mono">
                      {(b.ticket_numbers || '').split(',').filter(Boolean).map(n => <div key={n}>{n}</div>)}
                      {(b.emd_numbers || '').split(',').filter(Boolean).map(n => <div key={n} className="adm-sub">EMD {n}</div>)}
                      {!b.ticket_numbers && !b.emd_numbers && '—'}
                    </td>
                    <td><span className={`rl-chip ${cls}`}>{label}</span>
                      {b.servicing_note && <div className="adm-sub amb-note">{b.servicing_note}</div>}
                      {b.servicing_at && <div className="adm-sub">{fmtDateTime(b.servicing_at)}</div>}</td>
                    <td><button className="btn btn-secondary btn-sm" onClick={() => setOpen(b)}>Check &amp; manage</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {open && <AmadeusBookingPopup api={api} booking={open} switches={sw} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}

function AmadeusBookingPopup({ api, booking, switches, onClose, onChanged }) {
  const loc = booking.order_id;
  const [live, setLive] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [step, setStep] = useState(null);       // 'cancel' | 'void' | 'refund'
  const [result, setResult] = useState(null);   // {kind, data}
  const [confirmEmds, setConfirmEmds] = useState(false);
  const [chosen, setChosen] = useState({});     // refund: number -> true
  const [fees, setFees] = useState({});         // refund: number -> fee
  const [quote, setQuote] = useState(null);

  const check = useCallback(() => {
    setError(''); setLive(null);
    return api(`/amadeus-bookings/${loc}`).then(d => {
      setLive(d);
      const pick = {};
      (d.documents || []).filter(x => x.status === 'issued' && x.kind === 'ticket').forEach(x => { pick[x.number] = true; });
      setChosen(pick);
    }).catch(e => setError(e.message));
  }, [api, loc]);
  useEffect(() => { check(); }, [check]);

  const run = async (kind, path, body) => {
    setBusy(kind); setError('');
    try {
      const d = await api(`/amadeus-bookings/${loc}/${path}`, { method: 'POST', body: JSON.stringify(body || {}) });
      if (kind === 'quote') { setQuote(d); } else { setResult({ kind, data: d }); setStep(null); setQuote(null); onChanged?.(); check(); }
    } catch (e) { setError(e.message); }
    setBusy('');
  };

  const docs = live?.documents || [];
  const validDocs = docs.filter(d => d.status === 'issued');
  const refundDocs = Object.keys(chosen).filter(n => chosen[n]);
  const feeMap = Object.fromEntries(refundDocs.map(n => [n, Number(fees[n]) || 0]));
  const quoteOk = quote && quote.ok && quote.lines?.every(l => refundDocs.includes(l.number));

  return createPortal(
    <div className="modal-overlay itp-overlay" onClick={onClose}>
      <div className="itp amb-pop" role="dialog" aria-label={`Amadeus booking ${loc}`} onClick={e => e.stopPropagation()}>
        <div className="itp-head">
          <div>
            <div className="itp-kicker">Amadeus booking · live from Amadeus</div>
            <h3>{loc} <span>{booking.origin} → {booking.destination} · {booking.passenger_name || ''}</span></h3>
          </div>
          <button type="button" className="itp-x" aria-label="Close" onClick={onClose}>×</button>
        </div>

        {error && <div className="error-banner">{error}</div>}
        {result && <ResultBox result={result} />}
        {!live && !error && <div className="itp-check wait"><span className="spinner itp-spin"></span> Reading the booking from Amadeus…</div>}

        {live && (<>
          <div className="itp-live">
            <div className="itp-live-head"><strong>Flights</strong><button className="adm-chip" onClick={check}>↻ Check again</button></div>
            {live.segments?.length ? (
              <ul className="amb-list">{live.segments.map((s, i) => (
                <li key={i}><strong>{s.flight_number}</strong> {s.departure_airport} → {s.arrival_airport} · {fmtDateTime(s.departure_time)}
                  {' '}<span className={`rl-chip ${s.status === 'HK' ? 'ok' : 'warn'}`}>{s.status}</span></li>
              ))}</ul>
            ) : <p className="adm-sub">No flights on this booking — they were cancelled{booking.servicing_note ? ` (${booking.servicing_note})` : ''}.</p>}
          </div>

          <div className="itp-live">
            <div className="itp-live-head"><strong>Tickets &amp; EMDs</strong></div>
            {docs.length ? (
              <div className="adm-table-wrap"><table className="adm-table amb-docs">
                <thead><tr><th>Number</th><th>Type</th><th>Passenger</th><th>Amount</th><th>Issued</th><th>Status</th></tr></thead>
                <tbody>{docs.map(d => {
                  const [l, c] = DOC_STATUS[d.status] || [d.status, 'muted'];
                  return (<tr key={d.number}><td className="mono nowrap">{d.number}</td><td>{d.kind === 'emd' ? 'EMD (seat/bag)' : 'Ticket'}</td>
                    <td>{d.passenger || '—'}</td><td className="mono nowrap">{money(d.amount, d.currency)}</td><td className="nowrap">{d.issued_on || '—'}</td>
                    <td><span className={`rl-chip ${c}`}>{l}</span></td></tr>);
                })}</tbody>
              </table></div>
            ) : <p className="adm-sub">No tickets or EMDs on the booking now.</p>}
          </div>

          {!step && (
            <div className="amb-actions">
              <ActionButton label="Cancel booking" on={switches.amadeus_cancel_enabled} allowed={live.can_cancel}
                            why={!live.has_itinerary ? 'No flights left to cancel.' : live.valid_tickets ? 'Void or refund the tickets first.' : ''}
                            onClick={() => { setStep('cancel'); setConfirmEmds(false); setResult(null); }} />
              <ActionButton label="Void tickets & EMDs" on={switches.amadeus_void_enabled} allowed={live.can_void}
                            why={!validDocs.length ? 'No valid tickets or EMDs.' : 'Void is only possible on the day of issue — use Refund.'}
                            onClick={() => { setStep('void'); setResult(null); }} />
              <ActionButton label="Refund tickets" on={switches.amadeus_refund_enabled} allowed={live.can_refund && live.valid_tickets > 0}
                            why="No valid tickets to refund."
                            onClick={() => { setStep('refund'); setQuote(null); setResult(null); }} />
            </div>
          )}

          {step === 'cancel' && (
            <div className="amb-step">
              <strong>Cancel the flights of {loc}?</strong>
              <p className="adm-sub">The airline releases the seats. This can’t be undone — the customer would need a new booking.</p>
              {live.valid_emds?.length > 0 && (
                <label className="amb-check"><input type="checkbox" checked={confirmEmds} onChange={e => setConfirmEmds(e.target.checked)} />
                  EMD {live.valid_emds.join(', ')} (seat/baggage) is still valid. I will claim its refund from the airline.</label>
              )}
              <div className="ofp-actions">
                <button className="btn btn-primary btn-sm" disabled={!!busy || (live.valid_emds?.length > 0 && !confirmEmds)}
                        onClick={() => run('cancel', 'cancel', { confirm_emds: confirmEmds })}>{busy === 'cancel' ? 'Cancelling…' : 'Yes, cancel the flights'}</button>
                <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => setStep(null)}>No</button>
              </div>
            </div>
          )}

          {step === 'void' && (
            <div className="amb-step">
              <strong>Void {validDocs.length} document{validDocs.length === 1 ? '' : 's'} and cancel the flights?</strong>
              <ul className="amb-list">{validDocs.map(d => <li key={d.number} className="mono">{d.number} · {d.kind === 'emd' ? 'EMD' : 'ticket'} · {d.passenger} · {money(d.amount, d.currency)}</li>)}</ul>
              <p className="adm-sub">A void cancels the tickets as if they were never sold (full money back, no airline fee). It only works on the day of issue. Then the flights are cancelled.</p>
              <div className="ofp-actions">
                <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run('void', 'void')}>{busy === 'void' ? 'Voiding…' : 'Yes, void'}</button>
                <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => setStep(null)}>No</button>
              </div>
            </div>
          )}

          {step === 'refund' && (
            <div className="amb-step">
              <strong>Refund — choose the tickets and enter the airline fee from the fare rules</strong>
              <div className="adm-table-wrap"><table className="adm-table amb-docs">
                <thead><tr><th></th><th>Ticket</th><th>Passenger</th><th>Paid</th><th>Airline fee</th><th>Refund (Amadeus)</th></tr></thead>
                <tbody>{validDocs.map(d => {
                  const line = quote?.lines?.find(l => l.number === d.number);
                  const isEmd = d.kind === 'emd';
                  return (
                    <tr key={d.number}>
                      <td><input type="checkbox" disabled={isEmd} checked={!!chosen[d.number]}
                                 onChange={e => { setChosen(c => ({ ...c, [d.number]: e.target.checked })); setQuote(null); }} /></td>
                      <td className="mono nowrap">{d.number}{isEmd && <div className="adm-sub">EMD — claim from the airline</div>}</td>
                      <td>{d.passenger || '—'}</td>
                      <td className="mono nowrap">{money(d.amount, d.currency)}</td>
                      <td>{!isEmd && <input className="form-input amb-fee" type="number" min="0" step="1" value={fees[d.number] ?? ''} placeholder="0"
                                             onChange={e => { setFees(f => ({ ...f, [d.number]: e.target.value })); setQuote(null); }} />}</td>
                      <td className="mono nowrap">{line ? (line.error ? <span className="recon-issue">{line.error}</span> : money(line.refundable, line.refund_currency)) : '—'}</td>
                    </tr>
                  );
                })}</tbody>
              </table></div>
              {quote && <div className={`itp-check ${quoteOk ? 'ok' : 'bad'}`}>
                <strong>{quoteOk ? `Amadeus will refund ${money(quote.total_refund, quote.currency)}` : 'Amadeus cannot refund all of the chosen documents'}</strong>
                {quoteOk && <span>Nothing is refunded until you press Refund. The flights are cancelled after the refund.</span>}
              </div>}
              <div className="ofp-actions">
                <button className="btn btn-secondary btn-sm" disabled={!!busy || !refundDocs.length}
                        onClick={() => run('quote', 'refund-quote', { documents: refundDocs, fees: feeMap })}>{busy === 'quote' ? 'Asking Amadeus…' : 'Get refund amount'}</button>
                <button className="btn btn-primary btn-sm" disabled={!!busy || !quoteOk}
                        onClick={() => run('refund', 'refund', { documents: refundDocs, fees: feeMap })}>
                  {busy === 'refund' ? 'Refunding…' : quoteOk ? `Refund ${money(quote.total_refund, quote.currency)}` : 'Refund'}</button>
                <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => { setStep(null); setQuote(null); }}>Back</button>
              </div>
            </div>
          )}
        </>)}
        <div className="itp-foot"><button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button></div>
      </div>
    </div>,
    document.body,
  );
}

function ActionButton({ label, on, allowed, why, onClick }) {
  const title = !on ? 'Switched off in Go-live controls' : !allowed ? why : '';
  return (
    <div className="amb-action">
      <button className="btn btn-secondary btn-sm" disabled={!on || !allowed} title={title} onClick={onClick}>{label}</button>
      {title && <span className="adm-sub">{title}</span>}
    </div>
  );
}

function ResultBox({ result }) {
  const d = result.data || {};
  const ok = d.success !== false;
  return (
    <div className={`itp-check ${ok ? 'ok' : 'bad'}`}>
      <strong>{d.message || (ok ? 'Done.' : 'Not completed.')}</strong>
      {result.kind === 'void' && d.results?.length > 0 && (
        <ul>{d.results.map(r => <li key={r.number}>{r.number} ({r.kind}) — {r.voided ? 'voided' : `not voided: ${r.error}`}</li>)}</ul>
      )}
      {result.kind === 'refund' && d.lines?.length > 0 && (
        <ul>{d.lines.map(l => <li key={l.number}>{l.number} — refund {money(l.refundable, l.refund_currency)}{l.airline_fee ? ` (airline fee ${money(l.airline_fee, l.refund_currency)})` : ''}</li>)}</ul>
      )}
      {d.emds_still_valid?.length > 0 && <span>EMD {d.emds_still_valid.join(', ')} is still valid — claim its refund from the airline.</span>}
    </div>
  );
}
