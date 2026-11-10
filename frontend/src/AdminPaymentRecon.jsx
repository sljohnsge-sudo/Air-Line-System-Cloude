// Admin › Card Payment Reconciliation. Finance uploads the CyberSource
// Transactions export (Transaction Management → Transactions → Export, JSON or
// CSV, or the CSV saved as .xlsx). Every card sale is matched to our payment
// record, the booking / seats-extras payment it paid for and its ERPNext
// invoice (backend services/payment_reconciliation.py). Each run is kept, and
// can be downloaded as a CSV for Excel.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';

const money = (amt, cur) => (amt || amt === 0 ? `${cur || ''} ${Number(amt).toLocaleString(undefined, { maximumFractionDigits: 2 })}`.trim() : '—');
const PAGE = 200;

export default function AdminPaymentRecon({ API_BASE, adminToken, onSessionExpired }) {
  const expiredRef = useRef(onSessionExpired);
  expiredRef.current = onSessionExpired;
  const call = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/payment-recon${path}`, {
      ...opts, headers: { Authorization: `Bearer ${adminToken}`, ...(opts.headers || {}) },
    });
    if (res.status === 401) { expiredRef.current?.(); throw new Error('Your session expired — please log in again.'); }
    return res;
  }, [API_BASE, adminToken]);
  const json = useCallback(async (path, opts) => {
    const res = await call(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [call]);

  const [runs, setRuns] = useState([]);
  const [result, setResult] = useState(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('problem');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE);
  const fileRef = useRef(null);

  const loadRuns = useCallback(() => json('/runs').then(d => setRuns(d.runs || [])).catch(e => setError(e.message)), [json]);
  useEffect(() => { loadRuns(); }, [loadRuns]);

  const upload = async () => {
    if (!file) return;
    setBusy(true); setError('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const data = await json('/upload', { method: 'POST', body: fd });
      setResult(data); setFilter('problem'); setShown(PAGE);
      setFile(null); if (fileRef.current) fileRef.current.value = '';
      loadRuns();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const openRun = async (id) => {
    setBusy(true); setError('');
    try { setResult(await json(`/runs/${id}`)); setFilter('problem'); setShown(PAGE); } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const download = async () => {
    if (!result?.run_id) return;
    try {
      const res = await call(`/runs/${result.run_id}/csv`);
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url; a.download = `card-reconciliation-${result.run_id}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { setError(e.message); }
  };

  const text = result?.issue_text || {};
  const lines = useMemo(() => {
    const q = query.trim().toUpperCase();
    return (result?.lines || []).filter(l => {
      if (filter === 'problem' && l.status !== 'problem') return false;
      if (filter === 'matched' && l.status !== 'matched') return false;
      if (!['all', 'problem', 'matched'].includes(filter) && !l.issues.includes(filter)) return false;
      if (q && ![l.reference, l.locator, l.request_id, l.passenger, l.erp_invoice].some(v => (v || '').toUpperCase().includes(q))) return false;
      return true;
    });
  }, [result, filter, query]);

  const s = result?.summary;
  const lkrSettled = s?.totals?.LKR_settled;

  return (
    <div className="recon">
      <div className="recon-upload">
        <div>
          <strong>Upload a CyberSource export</strong>
          <p className="adm-sub recon-help">
            In the CyberSource Business Center: Transaction Management → Transactions → choose the dates → Export → JSON (or CSV).
            Excel (.xlsx) also works.
          </p>
        </div>
        <div className="recon-upload-row">
          <input ref={fileRef} type="file" accept=".json,.csv,.xlsx,application/json,text/csv"
                 onChange={e => setFile(e.target.files?.[0] || null)} />
          <button className="btn btn-primary btn-sm" disabled={!file || busy} onClick={upload}>
            {busy ? 'Checking…' : 'Check payments'}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && s && (
        <>
          <div className="recon-head">
            <div>
              <div className="recon-title">{result.file_name || 'Export'}</div>
              <div className="adm-sub">
                {s.period_from ? `${fmtDateTime(s.period_from)} – ${fmtDateTime(s.period_to)}` : ''}
                {result.created_at ? ` · checked ${fmtDateTime(result.created_at)}` : ''}{result.uploaded_by ? ` by ${result.uploaded_by}` : ''}
              </div>
            </div>
            <button className="btn btn-secondary btn-sm" onClick={download} disabled={!result.run_id}>Download for Excel (CSV)</button>
          </div>

          <div className="recon-tiles">
            <div className="recon-tile"><span>Card sales</span><b>{s.sales}</b><small>{s.settled} settled to the bank</small></div>
            <div className="recon-tile"><span>Settled amount</span><b>{lkrSettled != null ? money(lkrSettled, 'LKR') : '—'}</b><small>sent to the bank by CyberSource</small></div>
            <div className="recon-tile ok"><span>Matched</span><b>{s.matched}</b><small>paid, issued, invoiced, same amount</small></div>
            <div className={`recon-tile ${s.problems ? 'bad' : 'ok'}`}><span>To check</span><b>{s.problems}</b><small>see the list below</small></div>
            <div className="recon-tile muted"><span>Not money</span><b>{s.steps + s.declined}</b><small>{s.steps} 3-D Secure steps · {s.declined} declined</small></div>
            {s.refunds > 0 && <div className="recon-tile"><span>Refunds</span><b>{s.refunds}</b><small>refunds / reversals in the file</small></div>}
          </div>

          <div className="adm-chips recon-chips">
            <button className="adm-chip" aria-pressed={filter === 'problem'} onClick={() => { setFilter('problem'); setShown(PAGE); }}>To check <span>{s.problems}</span></button>
            <button className="adm-chip" aria-pressed={filter === 'matched'} onClick={() => { setFilter('matched'); setShown(PAGE); }}>Matched <span>{s.matched}</span></button>
            <button className="adm-chip" aria-pressed={filter === 'all'} onClick={() => { setFilter('all'); setShown(PAGE); }}>All <span>{(result.lines || []).length}</span></button>
            {Object.entries(s.issue_counts || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
              <button key={k} className="adm-chip" aria-pressed={filter === k} title={text[k]} onClick={() => { setFilter(k); setShown(PAGE); }}>
                {text[k] || k} <span>{n}</span>
              </button>
            ))}
          </div>
          <input className="recon-search" placeholder="Search PNR, reference, request ID, passenger or invoice"
                 value={query} onChange={e => { setQuery(e.target.value); setShown(PAGE); }} />

          <div className="adm-table-wrap">
            <table className="adm-table recon-table">
              <thead>
                <tr><th>Result</th><th>Date</th><th>PNR / reference</th><th>Passenger</th><th>Paid</th><th>Booking</th><th>ERPNext</th><th>Refund / exception</th><th>What to check</th></tr>
              </thead>
              <tbody>
                {lines.slice(0, shown).map((l, i) => (
                  <tr key={`${l.request_id || l.reference}-${i}`}>
                    <td>{l.status === 'matched' ? <span className="rl-chip ok">Matched</span> : <span className="rl-chip bad">Check</span>}</td>
                    <td className="nowrap">{l.date ? fmtDateTime(l.date) : '—'}</td>
                    <td>
                      <strong>{l.locator || l.reference || '—'}</strong>
                      {l.reference && l.reference !== l.locator && <div className="adm-sub" title={l.reference}>{l.reference.length > 22 ? `${l.reference.slice(0, 20)}…` : l.reference}</div>}
                      {l.what && <div className="adm-sub">{l.what}</div>}
                    </td>
                    <td>{l.passenger || '—'}{l.card && <div className="adm-sub">{l.card}{l.auth_code ? ` · auth ${l.auth_code}` : ''}</div>}</td>
                    <td className="mono nowrap">
                      {money(l.amount, l.currency)}
                      {l.source === 'cybersource' && <div className="adm-sub">{l.settled ? 'Settled' : 'Not settled'}</div>}
                    </td>
                    <td className="mono nowrap">
                      {money(l.expected_amount, l.expected_currency)}
                      {l.booking_status && <div className="adm-sub">{l.booking_status}</div>}
                    </td>
                    <td className="nowrap">{l.erp_invoice || '—'}</td>
                    <td>{(l.refund_requests || []).length ? l.refund_requests.map(r => <div key={r} className="adm-sub recon-ref">{r}</div>) : '—'}</td>
                    <td>
                      {l.issues.length ? l.issues.map(k => <div key={k} className="recon-issue">{text[k] || k}</div>) : <span className="adm-sub">—</span>}
                      {l.note && <div className="adm-sub">{l.note}</div>}
                    </td>
                  </tr>
                ))}
                {!lines.length && <tr><td colSpan={9} className="adm-sub">Nothing in this view.</td></tr>}
              </tbody>
            </table>
          </div>
          {lines.length > shown && (
            <button className="btn btn-secondary btn-sm recon-more" onClick={() => setShown(n => n + PAGE)}>Show more ({lines.length - shown} left)</button>
          )}
        </>
      )}

      <h4 className="recon-runs-title">Earlier checks</h4>
      {runs.length ? (
        <div className="adm-table-wrap">
          <table className="adm-table">
            <thead><tr><th>Checked</th><th>File</th><th>Period</th><th>Sales</th><th>Matched</th><th>To check</th><th>By</th><th></th></tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id}>
                  <td className="nowrap">{fmtDateTime(r.created_at)}</td>
                  <td>{r.file_name}</td>
                  <td className="nowrap">{r.period_from ? `${fmtDateTime(r.period_from)} – ${fmtDateTime(r.period_to)}` : '—'}</td>
                  <td>{r.sales}</td><td>{r.matched}</td><td>{r.problems}</td>
                  <td>{r.uploaded_by || '—'}</td>
                  <td><button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => openRun(r.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="adm-sub">No exports checked yet.</p>}
    </div>
  );
}
