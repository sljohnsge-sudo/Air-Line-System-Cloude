// Admin › Airline Rates Logs (was Supplier Alerts). One row per customer
// search: what each supplier (Travelport, later Amadeus) sent per channel
// (GDS / NDC), the lowest fare, and the supplier alerts it raised. Click a row
// for the full record — proof for a supplier who says "our fare was cheaper",
// and the place staff see a missing channel or an API error.
// Everything shown comes from the search log and the alerts the backend
// recorded (backend/insights.py rates_logs / rates_log_detail).
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { fmtDate, fmtDateTime, fmtTime } from './dateFormat.js';

const money = (amt, cur) => (amt || amt === 0 ? `${cur || ''} ${Math.round(Number(amt)).toLocaleString()}`.trim() : '—');
const SUP = { TP: 'Travelport', AD: 'Amadeus' };
const CH_STATE = {
  ok: ['✓', 'ok', 'fares received'],
  none: ['—', 'muted', 'no fares on this channel (normal for this route)'],
  missing: ['missing', 'bad', 'no fares, but this channel had fares on this route in the last 7 days'],
  error: ['error', 'bad', 'the supplier returned an error'],
  empty: ['no fares', 'warn', 'the supplier answered with no fares at all'],
  off: ['offline', 'muted', 'not connected yet (Amadeus is Phase 2)'],
  unknown: ['—', 'muted', 'not recorded'],
};
const cellLabel = (cell) => { const [s, ch] = (cell || '').split('_'); return `${SUP[s] || s} ${ch || ''}`.trim(); };

function ChannelChips({ states, supplier }) {
  return (
    <div className="rl-chips">
      {['GDS', 'NDC'].map(ch => {
        const st = (states || {})[ch] || 'unknown';
        const [label, cls, title] = CH_STATE[st] || [st, 'muted', st];
        return <span key={ch} className={`rl-chip ${cls}`} title={`${SUP[supplier]} ${ch}: ${title}`}>{ch} {label}</span>;
      })}
    </div>
  );
}

function Detail({ api, row }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!row.id) return;
    api(`/rates-logs/${row.id}`).then(setD).catch(e => setError(e.message));
  }, [api, row.id]);
  if (!row.id) {
    return (
      <div className="rl-detail">
        <div className="rl-sec-title">Supplier alert</div>
        <p>{row.summary || 'The supplier did not return fares for this search.'}</p>
        <p className="adm-sub">This alert came from a search that isn't in the search log (a date-strip check, or before the log started), so there are no fares to compare.</p>
      </div>
    );
  }
  if (error) return <div className="error-banner">{error}</div>;
  if (!d) return <div className="loading-state"><div className="spinner"></div><p>Loading the full record…</p></div>;
  const s = d.search;
  const cols = ['TP_GDS', 'TP_NDC', 'AD_GDS', 'AD_NDC'];
  const first = d.display.first_shown, lowest = d.display.lowest, picked = d.display.customer_picked;
  const pax = [s.adults && `${s.adults} adult${s.adults > 1 ? 's' : ''}`, s.children && `${s.children} child`, s.infants && `${s.infants} infant`, s.students && `${s.students} student`].filter(Boolean).join(', ');
  return (
    <div className="rl-detail">
      <section>
        <div className="rl-sec-title">Complete search details</div>
        <div className="rl-grid">
          <span>Search ID</span><strong className="ins-mono">#{s.id}</strong>
          <span>Searched</span><strong>{fmtDateTime(s.searched_at)}</strong>
          <span>Trip</span><strong>{s.origin} → {s.destination} · {s.trip_type} · {fmtDate(s.departure_date)}{s.return_date ? ` – ${fmtDate(s.return_date)}` : ''}</strong>
          <span>Passengers / cabin</span><strong>{pax || '—'} · {s.cabin || 'All'}</strong>
          <span>Fares shown</span><strong>{s.result_count}{s.duration_ms ? ` · answered in ${(s.duration_ms / 1000).toFixed(1)} s` : ''}</strong>
        </div>
      </section>

      <section>
        <div className="rl-sec-title">Airline-wise GDS &amp; NDC fare comparison</div>
        {d.comparison.length === 0 ? <p className="adm-sub">No fares came back for this search.</p> : (
          <div className="rl-table-wrap"><table className="rl-table">
            <thead><tr><th>Airline</th><th>Travelport GDS</th><th>Travelport NDC</th><th>Amadeus GDS</th><th>Amadeus NDC</th><th>Lowest</th></tr></thead>
            <tbody>{d.comparison.map(r => (
              <tr key={r.code || r.airline}>
                <td><strong>{r.airline}</strong>{r.code ? <span className="adm-sub"> ({r.code})</span> : null}</td>
                {cols.map(c => (
                  <td key={c} className={r.lowest?.cell === c ? 'rl-best' : undefined}>
                    {r.fares[c] ? money(r.fares[c].price, r.fares[c].currency) : <span className="adm-sub">{c.startsWith('AD') && d.channels.AD.GDS === 'off' ? 'offline' : '—'}</span>}
                  </td>
                ))}
                <td className="rl-best">{r.lowest ? <>{money(r.lowest.price, r.lowest.currency)}<div className="adm-sub">{cellLabel(r.lowest.cell)}</div></> : '—'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      <section>
        <div className="rl-sec-title">Customer fare display verification</div>
        <div className="rl-grid">
          <span>Shown first</span>
          <strong>{first ? <>{first.airline} · {money(first.price, first.currency)} <span className="adm-sub">({cellLabel(`${first.source}_${(first.fare_source || 'GDS').toUpperCase()}`)})</span></> : '—'}</strong>
          <span>Lowest received</span>
          <strong>{lowest ? <>{lowest.airline} · {money(lowest.price, lowest.currency)} <span className="adm-sub">({cellLabel(`${lowest.source}_${(lowest.fare_source || 'GDS').toUpperCase()}`)})</span></> : '—'}</strong>
          <span>Check</span>
          <span>{first ? (d.display.lowest_shown_first
            ? <span className="rl-chip ok">✓ Lowest fare received was shown first</span>
            : <span className="rl-chip bad">✗ The first fare shown was not the lowest received</span>) : '—'}</span>
          <span>Customer picked</span>
          <strong>{picked ? <>{picked.selected || `${picked.airline || ''} ${picked.flight || ''}`}{picked.amount ? ` · ${money(picked.amount, picked.currency)}` : ''}
            <span className="adm-sub"> · {picked.status}{picked.locator_code ? ` · PNR ${picked.locator_code}` : ''}</span></> : <span className="adm-sub">No flight picked from this search</span>}</strong>
        </div>
      </section>

      <section>
        <div className="rl-sec-title">Supplier errors &amp; technical details</div>
        <div className="rl-grid">
          {['TP', 'AD'].flatMap(sup => ['GDS', 'NDC'].map(ch => {
            const st = d.channels[sup][ch];
            const [label, cls, title] = CH_STATE[st] || [st, 'muted', st];
            return (
              <Fragment key={sup + ch}>
                <span>{SUP[sup]} {ch}</span>
                <span><span className={`rl-chip ${cls}`}>{st === 'ok' ? 'Success' : label}</span> <span className="adm-sub">{title}</span></span>
              </Fragment>
            );
          }))}
          {s.error_message && <><span>Error</span><span className="rl-err">{s.error_message}</span></>}
          {Object.entries(d.messages || {}).map(([sup, list]) => (
            <Fragment key={sup}><span>{SUP[sup] || sup} messages</span><span>{list.map((m, i) => <div key={i} className="adm-sub">{m}</div>)}</span></Fragment>
          ))}
        </div>
      </section>

      <section>
        <div className="rl-sec-title">Staff notifications &amp; delivery</div>
        {d.alerts.length === 0 ? <p className="adm-sub">No alert was needed for this search.</p> : d.alerts.map(a => {
          const dl = a.delivery;
          return (
            <div key={a.id} className="rl-alert">
              <div><span className={`rl-chip ${a.status === 'new' ? 'warn' : 'ok'}`}>{a.status === 'new' ? 'Pending' : 'Acknowledged'}</span> <strong>{a.kind_label}</strong> — {a.summary}</div>
              <div className="adm-action">→ {a.action}</div>
              <div className="rl-delivery">
                {dl ? (dl.result === 'sent' ? <>Email sent to {dl.recipients.join(', ')}</>
                  : dl.result === 'skipped_recent' ? <>No email — {dl.note || 'one already went out in the last 15 minutes'} (to {dl.recipients.join(', ')})</>
                  : dl.result === 'failed' ? <span className="rl-err">Email to {dl.recipients.join(', ')} failed: {dl.note}</span>
                  : <>No email — no active recipients</>)
                  : <>{a.email_sent ? 'Email sent to the recipients' : 'No email (one already went out in the last 15 minutes, or no active recipients)'} <span className="adm-sub">(older alert: recipients not recorded)</span></>}
                <span className="adm-sub"> · SMS to staff is not set up</span>
              </div>
            </div>
          );
        })}
      </section>
    </div>
  );
}

export default function AdminRatesLogs({ API_BASE, adminToken, onSessionExpired, onAlertsChanged }) {
  const expiredRef = useRef(onSessionExpired);
  expiredRef.current = onSessionExpired;
  const api = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin${path.startsWith('/notifications') ? '' : '/insights'}${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}`, ...(opts.headers || {}) },
    });
    if (res.status === 401) { expiredRef.current?.(); throw new Error('Your session expired — please log in again.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [API_BASE, adminToken]);

  const [days, setDays] = useState(7);
  const [filter, setFilter] = useState('all'); // 'all' | 'problems' | 'TP' | 'AD'
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [openKey, setOpenKey] = useState(null);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(''); setData(null); setSelected([]);
    const q = new URLSearchParams({ days: String(days) });
    if (filter === 'problems') q.set('problems', 'true');
    if (filter === 'TP' || filter === 'AD') q.set('supplier', filter);
    api(`/rates-logs?${q}`).then(setData).catch(e => setError(e.message));
  }, [api, days, filter]);
  useEffect(() => { load(); }, [load]);

  const acknowledge = async (ids) => {
    if (!ids.length) return;
    setBusy(true);
    try {
      await api('/notifications/acknowledge-bulk', { method: 'POST', body: JSON.stringify({ ids }) });
      load(); onAlertsChanged?.();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const rows = data?.rows || [];
  const pendingIds = (r) => r.alerts.filter(a => a.status === 'new').map(a => a.id);
  const allPending = rows.flatMap(pendingIds);
  const keyOf = (r) => r.id ? `s${r.id}` : `a${r.alerts[0]?.id}`;
  const chip = (id, label, count) => (
    <button key={id} type="button" className="adm-chip" aria-pressed={filter === id} onClick={() => setFilter(id)}>
      {label}{count != null && <span>{count}</span>}
    </button>
  );

  return (
    <>
      <div className="rl-top">
        <div className="adm-chips">
          {chip('all', 'All searches', data?.counts?.searches)}
          {chip('problems', 'Problems only', data?.counts?.problems)}
          {chip('TP', 'Travelport')}
          {chip('AD', 'Amadeus')}
        </div>
        <select className="form-input rl-days" value={days} onChange={e => setDays(Number(e.target.value))}>
          {[1, 7, 30, 90].map(d => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </select>
      </div>
      {data?.search_mode === 'travelport_only' && (
        <p className="adm-sub" style={{ margin: '0 0 0.6rem' }}>Amadeus is offline for now (Phase 2). Its columns are kept ready and fill in automatically once it is connected.</p>
      )}
      {error && <div className="error-banner">{error}</div>}
      {!data ? <div className="loading-state"><div className="spinner"></div><p>Loading airline rates logs…</p></div> : (
        <>
          <div className="adm-bulk">
            <label>
              <input type="checkbox" checked={allPending.length > 0 && allPending.every(id => selected.includes(id))}
                     onChange={e => setSelected(e.target.checked ? allPending : [])} disabled={!allPending.length} />
              Select all pending shown ({allPending.length})
            </label>
            <button className="btn btn-primary btn-sm" disabled={!selected.length || busy} onClick={() => acknowledge(selected)}>
              {busy ? 'Acknowledging…' : `Acknowledge selected (${selected.length})`}
            </button>
          </div>
          <div className="adm-table-wrap">
            <table className="adm-table rl-list">
              <thead><tr><th aria-label="Select" /><th>Date</th><th>Route</th><th>Travelport</th><th>Amadeus</th><th>Lowest fare</th><th>Action</th></tr></thead>
              <tbody>{rows.map(r => {
                const k = keyOf(r), open = openKey === k, pend = pendingIds(r);
                return (
                  <Fragment key={k}>
                    <tr className={`adm-row ${r.problem ? 'tp' : 'normal'}${open ? ' open' : ''}`} onClick={() => setOpenKey(open ? null : k)}>
                      <td onClick={e => e.stopPropagation()}>
                        {pend.length > 0 && <input type="checkbox" aria-label="Select alerts of this search" checked={pend.every(id => selected.includes(id))}
                          onChange={e => setSelected(sel => e.target.checked ? [...new Set([...sel, ...pend])] : sel.filter(id => !pend.includes(id)))} />}
                      </td>
                      <td className="nowrap">{fmtDate(r.searched_at)}<div className="adm-sub">{fmtTime(r.searched_at)}</div></td>
                      <td className="nowrap"><strong className="adm-route">{r.origin || '?'}–{r.destination || '?'}</strong>
                        <div className="adm-sub">{r.departure_date ? fmtDate(r.departure_date) : ''}{r.return_date ? ` – ${fmtDate(r.return_date)}` : ''}</div></td>
                      <td><ChannelChips states={r.channels.TP} supplier="TP" /></td>
                      <td><ChannelChips states={r.channels.AD} supplier="AD" /></td>
                      <td className="nowrap">{r.lowest ? <><strong>{money(r.lowest.price, r.lowest.currency)}</strong><div className="adm-sub">{r.lowest.airline} · {SUP[r.lowest.supplier]} {r.lowest.channel}</div></> : <span className="adm-sub">—</span>}</td>
                      <td onClick={e => e.stopPropagation()} className="nowrap">
                        {r.action === 'pending' ? <><span className="rl-chip warn">Pending</span> <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => acknowledge(pend)}>Acknowledge</button></>
                          : r.action === 'acknowledged' ? <span className="rl-chip ok">Acknowledged</span> : <span className="adm-sub">—</span>}
                      </td>
                    </tr>
                    {open && <tr className="adm-detail"><td /><td colSpan={6}><Detail api={api} row={r} /></td></tr>}
                  </Fragment>
                );
              })}</tbody>
            </table>
            {rows.length === 0 && <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0.8rem' }}>Nothing in this period.</p>}
          </div>
          <p className="adm-hint">Click a search to see every airline's fare by supplier and channel, what the customer was shown, the supplier's errors and who was alerted. Showing up to 300 searches, newest first.</p>
        </>
      )}
    </>
  );
}
