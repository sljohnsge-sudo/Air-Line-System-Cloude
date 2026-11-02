// Admin insight screens: booking journeys (every step passed/failed), search
// records (what each provider returned, ranked as the customer saw it), IP
// watch, customer 360 and birthday / passport reminders. Everything shown is
// what customers entered and what Travelport (later Amadeus) returned — the
// backend (backend/insights.py) never generates or estimates any of it.
import { useEffect, useState, useCallback } from 'react';
import { fmtDate, fmtDateTime } from './dateFormat.js';

const fmtDT = (v) => fmtDateTime(v, '—');
const fmtD = (v) => fmtDate(v, '—');
const money = (amt, cur) => (amt || amt === 0 ? `${cur || ''} ${Math.round(Number(amt)).toLocaleString()}`.trim() : '—');
const STATUS_LABEL = { ticketed: 'Ticketed', failed: 'Failed', booking: 'In progress', searching: 'Searching' };

function useAdminApi(API_BASE, adminToken) {
  return useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/insights${path}`, {
      ...opts,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}`, ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [API_BASE, adminToken]);
}

function Section({ title, intro, children, right }) {
  return (
    <section className="search-section glass-panel ins-section">
      <div className="ins-head">
        <h3 className="results-heading">{title}</h3>
        {right}
      </div>
      {intro && <p className="ins-intro">{intro}</p>}
      {children}
    </section>
  );
}

function Loading({ text }) {
  return <div className="loading-state"><div className="spinner"></div><p>{text}</p></div>;
}

// ── Booking journeys ─────────────────────────────────────────────────────────
function Journeys({ api, openJourneyId, onOpened }) {
  const [days, setDays] = useState(7);
  const [status, setStatus] = useState('');
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);

  const load = useCallback(() => {
    setError('');
    api(`/journeys?days=${days}${status ? `&status=${status}` : ''}`).then(d => setRows(d.journeys)).catch(e => setError(e.message));
  }, [api, days, status]);
  useEffect(() => { load(); }, [load]);
  const openOne = useCallback((id) => {
    api(`/journeys/${encodeURIComponent(id)}`).then(setOpen).catch(e => setError(e.message));
  }, [api]);
  useEffect(() => { if (openJourneyId) { openOne(openJourneyId); onOpened?.(); } }, [openJourneyId, openOne, onOpened]);

  return (
    <Section title="Booking Journeys"
      intro="Every booking attempt, step by step — what passed, where it stopped, the message the customer saw and the real error behind it."
      right={<div className="ins-filters">
        <select className="form-input" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All</option><option value="failed">Failed</option><option value="ticketed">Ticketed</option><option value="booking">In progress / left</option>
        </select>
        <select className="form-input" value={days} onChange={e => setDays(Number(e.target.value))}>
          {[1, 7, 30, 90].map(d => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </select>
      </div>}>
      {error && <div className="error-banner">{error}</div>}
      {open && <JourneyDetail j={open} onClose={() => setOpen(null)} />}
      {!rows ? <Loading text="Loading journeys…" /> : rows.length === 0 ? <p className="ins-empty">No booking attempts in this period.</p> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>Last update</th><th>Customer</th><th>Trip</th><th>Fare</th><th>Status</th><th>Stopped at</th><th>IP</th></tr></thead>
          <tbody>{rows.map(r => (
            <tr key={r.journey_id} className="ins-click" onClick={() => openOne(r.journey_id)}>
              <td>{fmtDT(r.updated_at)}</td>
              <td>{r.customer_name || '—'}<div className="ins-sub">{r.customer_email || ''}</div></td>
              <td>{r.route || '—'}<div className="ins-sub">{r.airline || ''} {r.flight || ''}</div></td>
              <td>{money(r.amount, r.currency)}</td>
              <td><span className={`ins-pill ${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
              <td>{r.status === 'failed' ? r.failed_step : r.status === 'ticketed' ? (r.locator_code || '') : r.last_step}</td>
              <td className="ins-mono">{r.ip}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Section>
  );
}

function JourneyDetail({ j, onClose }) {
  return (
    <div className="ins-detail">
      <div className="ins-detail-head">
        <div>
          <strong>{j.customer_name || 'Customer'}</strong> · {j.route || ''} · {j.airline || ''} {j.flight || ''}
          <div className="ins-sub">{j.customer_email || ''} {j.customer_phone ? `· ${j.customer_phone}` : ''} · IP <span className="ins-mono">{j.ip}</span> · Journey <span className="ins-mono">{j.journey_id}</span></div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
      </div>
      <ol className="ins-steps">
        {j.steps.map((s, i) => (
          <li key={i} className={`ins-step ${s.status}`}>
            <span className="ins-step-icon">{s.status === 'pass' ? '✓' : '✗'}</span>
            <div>
              <div className="ins-step-name">{s.step} <span className="ins-sub">{fmtDT(s.at)}</span></div>
              {s.detail && <div className="ins-step-detail">{s.status === 'fail' ? 'Customer saw: ' : ''}{s.detail}</div>}
              {s.raw_error && <div className="ins-step-raw">Real error: {s.raw_error}</div>}
            </div>
          </li>
        ))}
      </ol>
      {j.status === 'failed' && (
        <p className="ins-explain">Stopped at <strong>{j.failed_step}</strong>. Everything above it passed.</p>
      )}
    </div>
  );
}

// ── Search records ───────────────────────────────────────────────────────────
function Searches({ api, presetIp, onPresetUsed }) {
  const [days, setDays] = useState(7);
  const [route, setRoute] = useState('');
  const [ip, setIp] = useState(presetIp || '');
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);

  useEffect(() => { if (presetIp) { setIp(presetIp); onPresetUsed?.(); } }, [presetIp, onPresetUsed]);
  const load = useCallback(() => {
    setError(''); setRows(null);
    const q = new URLSearchParams({ days: String(days) });
    if (route.trim()) q.set('route', route.trim());
    if (ip.trim()) q.set('ip', ip.trim());
    api(`/searches?${q}`).then(d => setRows(d.searches)).catch(e => setError(e.message));
  }, [api, days, route, ip]);
  useEffect(() => { load(); }, [days]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (presetIp !== undefined) load(); }, [ip]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Section title="Search Records"
      intro="Every customer flight search with what each provider returned and the results in the order shown (cheapest first) — proof of which fares were displayed."
      right={<div className="ins-filters">
        <input className="form-input" placeholder="Route e.g. CMB-DXB" value={route} onChange={e => setRoute(e.target.value)} />
        <input className="form-input" placeholder="IP address" value={ip} onChange={e => setIp(e.target.value)} />
        <select className="form-input" value={days} onChange={e => setDays(Number(e.target.value))}>
          {[1, 7, 30, 90].map(d => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </select>
        <button className="btn btn-primary btn-sm" onClick={load}>Apply</button>
      </div>}>
      {error && <div className="error-banner">{error}</div>}
      {open && <SearchDetail api={api} id={open} onClose={() => setOpen(null)} />}
      {!rows ? <Loading text="Loading searches…" /> : rows.length === 0 ? <p className="ins-empty">No searches match.</p> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>When</th><th>Route</th><th>Dates</th><th>Pax</th><th>Providers</th><th>Results</th><th>Cheapest shown</th><th>IP</th></tr></thead>
          <tbody>{rows.map(r => {
            const prov = (() => { try { return JSON.parse(r.providers_json || '{}'); } catch { return {}; } })();
            return (
              <tr key={r.id} className="ins-click" onClick={() => setOpen(r.id)}>
                <td>{fmtDT(r.searched_at)}{r.search_kind !== 'search' && <div className="ins-sub">{{ nearby: '±days check', recheck: 'fare re-check (booking)', admin_recheck: 'admin re-check' }[r.search_kind] || r.search_kind}</div>}</td>
                <td>{r.origin} → {r.destination}<div className="ins-sub">{r.trip_type}{r.cabin && r.cabin !== 'All' ? ` · ${r.cabin}` : ''}</div></td>
                <td>{fmtD(r.departure_date)}{r.return_date ? <div className="ins-sub">to {fmtD(r.return_date)}</div> : null}</td>
                <td>{r.adults}A{r.children ? ` ${r.children}C` : ''}{r.infants ? ` ${r.infants}I` : ''}{r.students ? ` ${r.students}S` : ''}</td>
                <td className="ins-small">{Object.entries(prov).map(([k, v]) => <div key={k}>{k}: {v}</div>)}</td>
                <td>{r.error_message ? <span className="ins-pill failed">Error</span> : r.result_count}</td>
                <td>{money(r.cheapest_price, r.cheapest_currency)}<div className="ins-sub">{r.cheapest_airline || ''}</div></td>
                <td className="ins-mono">{r.ip}</td>
              </tr>
            );
          })}</tbody>
        </table></div>
      )}
    </Section>
  );
}

function SearchDetail({ api, id, onClose }) {
  const [s, setS] = useState(null);
  const [error, setError] = useState('');
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [email, setEmail] = useState('');
  const [sendMsg, setSendMsg] = useState('');
  useEffect(() => { api(`/searches/${id}`).then(setS).catch(e => setError(e.message)); }, [api, id]);

  const recheck = () => {
    setChecking(true); setCheck(null); setSendMsg('');
    api(`/searches/${id}/recheck`, { method: 'POST' }).then(setCheck).catch(e => setError(e.message)).finally(() => setChecking(false));
  };
  const sendAlert = () => {
    setSendMsg('Sending…');
    api(`/searches/${id}/fare-alert`, { method: 'POST', body: JSON.stringify({ email }) })
      .then(d => setSendMsg(`Sent — now from ${money(d.now_price, d.currency)}`)).catch(e => setSendMsg(e.message));
  };
  if (error) return <div className="error-banner">{error}</div>;
  if (!s) return <Loading text="Loading search…" />;
  const byAirline = {};
  s.offers.forEach(o => { const k = `${o.airline} (${o.source})`; byAirline[k] = byAirline[k] || { n: 0, best: o.price, cur: o.currency, rank: o.rank }; byAirline[k].n += 1; });
  return (
    <div className="ins-detail">
      <div className="ins-detail-head">
        <div>
          <strong>{s.origin} → {s.destination}</strong> · {fmtD(s.departure_date)}{s.return_date ? ` – ${fmtD(s.return_date)}` : ''} · searched {fmtDT(s.searched_at)}
          <div className="ins-sub">IP <span className="ins-mono">{s.ip}</span>{s.customer_id ? ` · account #${s.customer_id}` : ''} · {s.duration_ms ? `${(s.duration_ms / 1000).toFixed(1)}s` : ''} · {s.user_agent?.slice(0, 80)}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
      </div>
      <div className="ins-provs">
        {Object.entries(s.providers || {}).map(([k, v]) => <span key={k} className="ins-chip"><strong>{k === 'TP' ? 'Travelport' : k === 'AD' ? 'Amadeus' : k}</strong>: {v}</span>)}
      </div>
      {s.error_message && <div className="error-banner">Provider error: {s.error_message}</div>}
      <h4 className="ins-h4">Airlines returned (best position in the list)</h4>
      <div className="ins-provs">
        {Object.entries(byAirline).sort((a, b) => a[1].rank - b[1].rank).map(([k, v]) => (
          <span key={k} className="ins-chip">#{v.rank} {k} · {v.n} fare{v.n > 1 ? 's' : ''} · from {money(v.best, v.cur)}</span>
        ))}
      </div>
      <div className="ins-actions">
        <button className="btn btn-primary btn-sm" disabled={checking} onClick={recheck}>{checking ? 'Searching live…' : 'Re-check live fare now'}</button>
        {check && (
          <span className={check.cheaper ? 'ins-good' : 'ins-sub'}>
            Now {money(check.now_price, check.now_currency)} ({check.now_airline || '—'}) vs {money(check.seen_price, check.seen_currency)} shown
            {check.cheaper ? ' — cheaper now' : ' — not cheaper'}
          </span>
        )}
      </div>
      {check?.cheaper && (
        <div className="ins-actions">
          <input className="form-input" type="email" placeholder="Customer email" value={email} onChange={e => setEmail(e.target.value)} />
          <button className="btn btn-primary btn-sm" disabled={!email} onClick={sendAlert}>Email the lower fare</button>
          {sendMsg && <span className="ins-sub">{sendMsg}</span>}
        </div>
      )}
      <h4 className="ins-h4">Results exactly as shown ({s.offers.length})</h4>
      <div className="ins-table-wrap ins-tall"><table className="ins-table">
        <thead><tr><th>#</th><th>Airline</th><th>Flights</th><th>Times</th><th>Stops</th><th>Fare</th><th>Price</th></tr></thead>
        <tbody>{s.offers.map(o => (
          <tr key={o.rank}>
            <td>{o.rank}</td>
            <td>{o.airline}<div className="ins-sub">{o.source} · {o.fare_source}</div></td>
            <td className="ins-mono">{o.flights.join(' / ')}</td>
            <td className="ins-small">{o.times.map((t, i) => <div key={i}>{t.replace(/T/g, ' ')}</div>)}</td>
            <td>{o.stops.join(' / ')}</td>
            <td className="ins-small">{o.brand || o.cabin || ''}</td>
            <td><strong>{money(o.price, o.currency)}</strong></td>
          </tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

// ── IP watch ─────────────────────────────────────────────────────────────────
const FLAG_LABEL = { price_watcher: 'Looking for a cheaper fare', volume: 'Unusual number of searches', spread: 'Many different routes', failures: 'Repeated failed bookings' };

function IpWatch({ api, onShowSearches }) {
  const [days, setDays] = useState(7);
  const [onlyFlagged, setOnlyFlagged] = useState(true);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setError('');
    api(`/ips?days=${days}`).then(d => setRows(d.ips)).catch(e => setError(e.message));
  }, [api, days]);
  useEffect(() => { load(); }, [load]);

  const block = (ip) => {
    const reason = window.prompt(`Block ${ip} from searching and booking? Reason (kept for the record):`);
    if (reason === null) return;
    api('/ips/block', { method: 'POST', body: JSON.stringify({ ip, reason }) }).then(load).catch(e => setError(e.message));
  };
  const unblock = (ip) => api('/ips/unblock', { method: 'POST', body: JSON.stringify({ ip }) }).then(load).catch(e => setError(e.message));
  const shown = (rows || []).filter(r => !onlyFlagged || r.flags.length || r.blocked);

  return (
    <Section title="IP Watch"
      intro="Who is searching, how often and for what. Repeat searches on one route without booking usually mean someone waiting for a cheaper fare — re-check it live from Search Records and email them. Unusual volume can mean a scraper; you can block that IP."
      right={<div className="ins-filters">
        <label className="ins-check"><input type="checkbox" checked={onlyFlagged} onChange={e => setOnlyFlagged(e.target.checked)} /> Flagged / blocked only</label>
        <select className="form-input" value={days} onChange={e => setDays(Number(e.target.value))}>
          {[1, 7, 30].map(d => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </select>
      </div>}>
      {error && <div className="error-banner">{error}</div>}
      {!rows ? <Loading text="Loading IPs…" /> : shown.length === 0 ? <p className="ins-empty">{onlyFlagged ? 'No flagged IPs.' : 'No searches in this period.'}</p> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>IP</th><th>Who</th><th>Searches</th><th>Most searched</th><th>Bookings</th><th>Flags</th><th></th></tr></thead>
          <tbody>{shown.map(r => (
            <tr key={r.ip}>
              <td className="ins-mono">{r.ip}<div className="ins-sub">last {fmtDT(r.last_seen)}</div></td>
              <td>{r.name || '—'}<div className="ins-sub">{r.email || 'not identified'}</div></td>
              <td>{r.searches}<div className="ins-sub">{r.searches_24h} in 24h · {r.routes} route{r.routes > 1 ? 's' : ''}</div></td>
              <td className="ins-small">{r.top_routes.slice(0, 3).map(t => (
                <div key={`${t.origin}${t.destination}`}>{t.origin}→{t.destination} ×{t.n} · lowest seen {money(t.lowest_seen, t.currency)}</div>
              ))}</td>
              <td>{r.ticketed} ticketed{r.failed ? <div className="ins-sub">{r.failed} failed</div> : null}</td>
              <td className="ins-small">{r.blocked ? <span className="ins-pill failed">Blocked</span> : null}
                {r.flags.map(f => <div key={f.kind} className={`ins-flag ${f.kind}`} title={f.text}>{FLAG_LABEL[f.kind]}: {f.text}</div>)}</td>
              <td className="ins-btns">
                <button className="btn btn-secondary btn-sm" onClick={() => onShowSearches(r.ip)}>Searches</button>
                {r.blocked
                  ? <button className="btn btn-secondary btn-sm" onClick={() => unblock(r.ip)}>Unblock</button>
                  : <button className="btn btn-primary btn-sm" onClick={() => block(r.ip)}>Block</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Section>
  );
}

// ── Customer 360 ─────────────────────────────────────────────────────────────
function Customers({ api }) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);
  const load = useCallback(() => {
    setError(''); setRows(null);
    api(`/customers${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`).then(d => setRows(d.customers)).catch(e => setError(e.message));
  }, [api, q]);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const openOne = (email) => api(`/customer?email=${encodeURIComponent(email)}`).then(setOpen).catch(e => setError(e.message));

  return (
    <Section title="Customer 360"
      intro="Each customer built from their own bookings, searches and account — routes, airlines, when they travel, how far ahead they book, who they travel with."
      right={<div className="ins-filters">
        <input className="form-input" placeholder="Name, email or phone" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && load()} />
        <button className="btn btn-primary btn-sm" onClick={load}>Search</button>
      </div>}>
      {error && <div className="error-banner">{error}</div>}
      {open && <CustomerDetail d={open} onClose={() => setOpen(null)} />}
      {!rows ? <Loading text="Loading customers…" /> : rows.length === 0 ? <p className="ins-empty">No customers match.</p> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>Customer</th><th>Bookings</th><th>Spend</th><th>Favourite route</th><th>Airline</th><th>Last travel</th><th>Account</th></tr></thead>
          <tbody>{rows.map(r => (
            <tr key={r.email} className="ins-click" onClick={() => openOne(r.email)}>
              <td>{r.name || '—'}<div className="ins-sub">{r.email}{r.phone ? ` · ${r.phone}` : ''}</div></td>
              <td>{r.ticketed} ticketed<div className="ins-sub">{r.bookings} total{r.cancelled ? ` · ${r.cancelled} cancelled` : ''}</div></td>
              <td>{Object.entries(r.spend).map(([c, v]) => <div key={c}>{money(v, c)}</div>)}</td>
              <td>{r.routes[0] ? `${r.routes[0][0]} ×${r.routes[0][1]}` : '—'}</td>
              <td>{r.airlines[0] ? r.airlines[0][0] : '—'}</td>
              <td>{fmtD(r.last_travel)}</td>
              <td>{r.customer_id ? `#${r.customer_id}` : 'guest'}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Section>
  );
}

function Stat({ label, value }) {
  return <div className="ins-stat"><div className="ins-stat-label">{label}</div><div className="ins-stat-value">{value}</div></div>;
}

function CustomerDetail({ d, onClose }) {
  const s = d.summary || {};
  const list = (pairs) => (pairs && pairs.length ? pairs.map(([k, v]) => `${k} (${v})`).join(', ') : '—');
  return (
    <div className="ins-detail">
      <div className="ins-detail-head">
        <div><strong>{s.name || s.email}</strong><div className="ins-sub">{s.email}{s.phone ? ` · ${s.phone}` : ''}{s.nationality ? ` · ${s.nationality}` : ''}{s.customer_id ? ` · account #${s.customer_id} since ${fmtD(s.account_since)}` : ' · guest'}</div></div>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
      </div>
      <div className="ins-stats">
        <Stat label="Ticketed" value={s.ticketed ?? 0} />
        <Stat label="Spend" value={Object.entries(s.spend || {}).map(([c, v]) => money(v, c)).join(' · ') || '—'} />
        <Stat label="Books ahead" value={s.avg_lead_days != null ? `${s.avg_lead_days} days` : '—'} />
        <Stat label="Birthday" value={s.date_of_birth ? fmtD(s.date_of_birth) : '—'} />
        <Stat label="Passport valid to" value={s.passport_expiry ? fmtD(s.passport_expiry) : '—'} />
        <Stat label="Searches (account)" value={s.searches ?? 0} />
      </div>
      <div className="ins-facts">
        <div><strong>Routes:</strong> {list(s.routes)}</div>
        <div><strong>Airlines:</strong> {list(s.airlines)}</div>
        <div><strong>Cabin:</strong> {list(s.cabins)}</div>
        <div><strong>Travel months:</strong> {list(s.months)}</div>
        <div><strong>Travels with:</strong> {s.companions?.length ? s.companions.join(', ') : '—'}</div>
      </div>
      <h4 className="ins-h4">Bookings</h4>
      <div className="ins-table-wrap"><table className="ins-table">
        <thead><tr><th>PNR</th><th>Route</th><th>Departure</th><th>Airline</th><th>Fare</th><th>Status</th></tr></thead>
        <tbody>{d.bookings.map(b => (
          <tr key={b.locator_code}><td className="ins-mono">{b.locator_code}</td><td>{b.departure_airport}→{b.arrival_airport}</td><td>{String(b.departure_time || '').replace('T', ' ').slice(0, 16)}</td><td>{b.airline}</td><td>{money(b.total_fare, b.currency)}</td><td>{b.status}</td></tr>
        ))}</tbody>
      </table></div>
      {d.journeys.length > 0 && <>
        <h4 className="ins-h4">Booking attempts</h4>
        <div className="ins-small">{d.journeys.map(j => <div key={j.journey_id}>{fmtDT(j.started_at)} · {j.route} · {j.airline || ''} · {STATUS_LABEL[j.status] || j.status}{j.failed_step ? ` at ${j.failed_step}` : ''}</div>)}</div>
      </>}
      {d.messages.length > 0 && <>
        <h4 className="ins-h4">Messages sent</h4>
        <div className="ins-small">{d.messages.map((m, i) => <div key={i}>{fmtDT(m.sent_at)} · {m.kind} · {m.channel} · {m.ok ? 'sent' : `failed: ${m.error}`}</div>)}</div>
      </>}
    </div>
  );
}

// ── Reminders + route demand ─────────────────────────────────────────────────
function Reminders({ api }) {
  const [data, setData] = useState(null);
  const [routes, setRoutes] = useState(null);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState({});
  const load = useCallback(() => {
    api('/reminders').then(setData).catch(e => setError(e.message));
    api('/routes?days=30').then(d => setRoutes(d.routes)).catch(() => {});
  }, [api]);
  useEffect(() => { load(); }, [load]);
  const send = (kind, row, channel) => {
    const key = `${kind}${row.email}${channel}`;
    setMsg(m => ({ ...m, [key]: 'Sending…' }));
    api('/reminders/send', { method: 'POST', body: JSON.stringify({ kind, email: row.email, channel }) })
      .then(() => { setMsg(m => ({ ...m, [key]: 'Sent' })); load(); })
      .catch(e => setMsg(m => ({ ...m, [key]: e.message })));
  };
  const buttons = (kind, r) => (
    <td className="ins-btns">
      {r.sent_at ? <span className="ins-sub">Sent {fmtDT(r.sent_at)}</span> : <>
        <button className="btn btn-secondary btn-sm" disabled={!r.email} onClick={() => send(kind, r, 'email')}>Email</button>
        <button className="btn btn-secondary btn-sm" disabled={!r.phone} onClick={() => send(kind, r, 'sms')}>SMS</button>
      </>}
      {['email', 'sms'].map(c => msg[`${kind}${r.email}${c}`] && <div key={c} className="ins-sub">{c}: {msg[`${kind}${r.email}${c}`]}</div>)}
    </td>
  );
  return (
    <>
      <Section title="Birthdays & Passport Reminders"
        intro={data ? (data.auto_enabled
          ? 'Automatic sending is ON: birthday wishes on the day, passport reminders 6, 3 and 1 month before expiry — each sent once.'
          : 'Automatic sending is OFF (set AUTO_REMINDERS=true on the server to turn it on). Send by hand below. Birthday wishes are marketing — under the PDPA send them only to customers who agreed to receive messages.') : ''}>
        {error && <div className="error-banner">{error}</div>}
        {!data ? <Loading text="Loading reminders…" /> : <>
          <h4 className="ins-h4">Birthdays in the next 30 days ({data.birthdays.length})</h4>
          {data.birthdays.length === 0 ? <p className="ins-empty">None.</p> : (
            <div className="ins-table-wrap"><table className="ins-table">
              <thead><tr><th>Traveller</th><th>Birthday</th><th>Contact</th><th>Last booking</th><th></th></tr></thead>
              <tbody>{data.birthdays.map(r => (
                <tr key={`${r.email}${r.name}`}><td>{r.name}</td><td>{fmtD(r.birthday)}<div className="ins-sub">{r.in_days === 0 ? 'today' : `in ${r.in_days} days`} · turning {r.turning}</div></td>
                  <td>{r.email}<div className="ins-sub">{r.phone || ''}</div></td><td className="ins-mono">{r.last_booking}</td>{buttons('birthday', r)}</tr>
              ))}</tbody>
            </table></div>
          )}
          <h4 className="ins-h4">Passports expiring within 6 months ({data.passports.length})</h4>
          {data.passports.length === 0 ? <p className="ins-empty">None.</p> : (
            <div className="ins-table-wrap"><table className="ins-table">
              <thead><tr><th>Traveller</th><th>Passport</th><th>Expiry</th><th>Contact</th><th></th></tr></thead>
              <tbody>{data.passports.map(r => (
                <tr key={`${r.email}${r.passport_last4}${r.name}`}><td>{r.name}</td><td className="ins-mono">…{r.passport_last4}</td>
                  <td>{fmtD(r.passport_expiry)}<div className={r.expired ? 'ins-bad' : 'ins-sub'}>{r.expired ? `expired ${-r.days_left} days ago` : `${r.days_left} days left`}</div></td>
                  <td>{r.email}<div className="ins-sub">{r.phone || ''}</div></td>{buttons('passport', r)}</tr>
              ))}</tbody>
            </table></div>
          )}
        </>}
      </Section>
      <Section title="Route Demand (last 30 days)" intro="Searched vs ticketed per route — where customers look and where they buy.">
        {!routes ? <Loading text="Loading routes…" /> : routes.length === 0 ? <p className="ins-empty">No searches yet.</p> : (
          <div className="ins-table-wrap"><table className="ins-table">
            <thead><tr><th>Route</th><th>Searches</th><th>Different IPs</th><th>Lowest seen</th><th>Ticketed</th><th>Search → ticket</th></tr></thead>
            <tbody>{routes.map(r => (
              <tr key={`${r.origin}${r.destination}`}><td>{r.origin} → {r.destination}</td><td>{r.searches}</td><td>{r.people}</td><td>{money(r.lowest, r.currency)}</td><td>{r.ticketed}</td><td>{r.conversion}%</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Section>
    </>
  );
}

// ── Pop-up for new failures / flagged IPs ────────────────────────────────────
export function InsightAlerts({ API_BASE, adminToken, onOpenJourney, onOpenIps }) {
  const api = useAdminApi(API_BASE, adminToken);
  const [alerts, setAlerts] = useState(null);
  const [hiddenIps, setHiddenIps] = useState('');
  useEffect(() => {
    let alive = true;
    const poll = () => api('/alerts').then(d => alive && setAlerts(d)).catch(() => {});
    poll();
    const t = setInterval(poll, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [api]);
  if (!alerts) return null;
  const ipKey = alerts.flagged_ips.map(i => i.ip).join(',');
  const showIps = alerts.flagged_ips.length > 0 && ipKey !== hiddenIps;
  if (!alerts.failed_journeys.length && !showIps) return null;
  const dismiss = () => {
    api('/alerts/seen', { method: 'POST' }).catch(() => {});
    setHiddenIps(ipKey);
    setAlerts(a => ({ ...a, failed_journeys: [] }));
  };
  return (
    <div className="ins-popup" role="alert">
      <div className="ins-popup-head"><strong>Needs attention</strong><button className="toast-close" aria-label="Dismiss" onClick={dismiss}>×</button></div>
      {alerts.failed_journeys.slice(0, 5).map(j => (
        <button key={j.journey_id} className="ins-popup-row" onClick={() => onOpenJourney(j.journey_id)}>
          <span className="ins-pill failed">Failed</span> {j.customer_name || 'Customer'} · {j.route || ''} · stopped at {j.failed_step}
        </button>
      ))}
      {showIps && alerts.flagged_ips.slice(0, 3).map(i => (
        <button key={i.ip} className="ins-popup-row" onClick={onOpenIps}>
          <span className="ins-pill booking">IP</span> <span className="ins-mono">{i.ip}</span> · {i.flags[0]?.text}
        </button>
      ))}
    </div>
  );
}

export default function AdminInsights({ section, API_BASE, adminToken, openJourneyId, onJourneyOpened, onNavigate }) {
  const api = useAdminApi(API_BASE, adminToken);
  const [ipFilter, setIpFilter] = useState(undefined);
  if (section === 'insight-journeys') return <Journeys api={api} openJourneyId={openJourneyId} onOpened={onJourneyOpened} />;
  if (section === 'insight-searches') return <Searches api={api} presetIp={ipFilter} onPresetUsed={() => {}} />;
  if (section === 'insight-ips') return <IpWatch api={api} onShowSearches={(ip) => { setIpFilter(ip); onNavigate('insight-searches'); }} />;
  if (section === 'insight-customers') return <Customers api={api} />;
  if (section === 'insight-reminders') return <Reminders api={api} />;
  return null;
}
