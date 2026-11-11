// Tick and Book business dashboard (/analytics). Its own sign-in accounts
// (created by a super admin in Admin Console -> Customers & Insights ->
// Analytics Logins) and its own analytics database, rebuilt from the booking
// system every 30 minutes or with Refresh (backend services/analytics_sync.py).
// Phone numbers arrive masked (last 3 digits); passport data is never sent.
// Charts are plain CSS bars / inline SVG — no chart package.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import TickBookLogo from './TickBookLogo.jsx';
import { fmtDate, fmtDateTime } from './dateFormat.js';
import './analytics.css';

const TOKEN_KEY = 'analyticsToken';
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
const saveToken = (v) => { try { if (v) localStorage.setItem(TOKEN_KEY, v); else localStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } };

let regionNames = null;
try { regionNames = new Intl.DisplayNames(['en'], { type: 'region' }); } catch { regionNames = null; }
const countryName = (code) => {
  if (!code) return 'Unknown';
  try { return regionNames?.of(code) || code; } catch { return code; }
};

const num = (n) => Number(n || 0).toLocaleString();
const money = (amt, cur) => (amt || amt === 0
  ? `${cur || ''} ${Number(amt).toLocaleString(undefined, { maximumFractionDigits: 0 })}`.trim() : '—');
const moneyList = (list) => (list && list.length ? list.map(r => money(r.amount, r.currency)).join(' · ') : '—');
const spendText = (obj) => Object.entries(obj || {}).map(([c, a]) => money(a, c)).join(' · ') || '—';
const pct = (v) => (v === null || v === undefined ? '—' : `${v}%`);

const STATUS_CHIP = (s) => {
  const v = (s || '').toLowerCase();
  if (['ticketed', 'confirmed', 'completed', 'paid', 'approved'].includes(v)) return 'ok';
  if (['cancelled', 'voided', 'failed', 'rejected', 'declined'].includes(v)) return 'bad';
  if (['pending', 'open', 'requested', 'held'].includes(v)) return 'warn';
  return 'muted';
};
const BOOKING_CHIP = (s) => ((s || '').toLowerCase() === 'confirmed' ? 'warn' : STATUS_CHIP(s));
const OUTCOME_TEXT = { paid: 'Paid', failed: 'Failed', open: 'Awaiting airline / payment', not_sold: 'Not sold (expired / replaced)' };
const OUTCOME_CHIP = { paid: 'ok', failed: 'bad', open: 'warn', not_sold: 'muted' };
const KIND_TEXT = { seat: 'Seats', extra: 'Extras (bags, meals…)' };
const SOURCE_TEXT = { booking: 'With the ticket', after_ticket: 'After ticketing' };

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'customers', label: 'Customers 360' },
  { id: 'airlines', label: 'Airlines' },
  { id: 'geo', label: 'Countries & Airports' },
  { id: 'extras', label: 'Extras & Add-ons' },
  { id: 'hotel', label: 'Hotels' },
  { id: 'visa', label: 'Visa' },
  { id: 'package', label: 'Tour Packages' },
];
const TAB_PATH = { overview: '/overview', airlines: '/airlines', geo: '/geography', extras: '/extras',
  hotel: '/verticals', visa: '/verticals', package: '/verticals' };

// ── Small chart pieces ─────────────────────────────────────────────────────────

function BarList({ rows, empty = 'No data yet.' }) {
  if (!rows || !rows.length) return <div className="an-empty">{empty}</div>;
  const max = Math.max(...rows.map(r => r.value || 0), 1);
  return (
    <div className="an-bars">
      {rows.map(r => (
        <div key={r.key ?? r.label} className="an-bar-row" title={`${r.label}: ${r.display ?? num(r.value)}${r.sub ? ` · ${r.sub}` : ''}`}>
          <div className="an-bar-label">
            <span>{r.label}</span>
            {r.sub && <small>{r.sub}</small>}
          </div>
          <div className="an-bar-track"><div className="an-bar-fill" style={{ width: `${Math.max(1.5, (100 * (r.value || 0)) / max)}%` }} /></div>
          <div className="an-bar-value">{r.display ?? num(r.value)}</div>
        </div>
      ))}
    </div>
  );
}

function DayChart({ series, field, label, fmt = num }) {
  const [hover, setHover] = useState(null);
  if (!series || !series.length) return <div className="an-empty">No data yet.</div>;
  const W = 640, H = 170, PAD_L = 8, PAD_B = 22, PAD_T = 14;
  const max = Math.max(...series.map(d => d[field] || 0), 1);
  const slot = (W - PAD_L * 2) / series.length;
  const bw = Math.max(2, Math.min(22, slot - 2));
  const total = series.reduce((s, d) => s + (d[field] || 0), 0);
  const step = Math.ceil(series.length / 6);
  const shown = hover !== null ? series[hover] : null;
  return (
    <div className="an-daychart">
      <div className="an-daychart-head">
        <span>{label}</span>
        <b>{shown ? `${fmtDate(shown.day)}: ${fmt(shown[field])}` : `Total ${fmt(total)} · peak ${fmt(max)}`}</b>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} per day`} onMouseLeave={() => setHover(null)}>
        <line x1={PAD_L} x2={W - PAD_L} y1={H - PAD_B} y2={H - PAD_B} className="an-axis" />
        {series.map((d, i) => {
          const h = ((H - PAD_B - PAD_T) * (d[field] || 0)) / max;
          const x = PAD_L + i * slot + (slot - bw) / 2;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              <rect x={PAD_L + i * slot} y={PAD_T} width={slot} height={H - PAD_B - PAD_T} fill="transparent" />
              <rect x={x} y={H - PAD_B - h} width={bw} height={Math.max(h, d[field] ? 1 : 0)} rx={Math.min(4, bw / 2)}
                    className={`an-col${hover === i ? ' on' : ''}`}>
                <title>{`${fmtDate(d.day)}: ${fmt(d[field])}`}</title>
              </rect>
              {(i % step === 0 || i === series.length - 1) && (
                <text x={PAD_L + i * slot + slot / 2} y={H - 6} textAnchor="middle" className="an-tick">{fmtDate(d.day).slice(0, 5)}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function SplitBar({ a, b, la, lb }) {
  const total = (a || 0) + (b || 0);
  if (!total) return <div className="an-empty">No data yet.</div>;
  const pa = Math.round((100 * a) / total);
  return (
    <div className="an-split">
      <div className="an-split-track" role="img" aria-label={`${la} ${a}, ${lb} ${b}`}>
        {a > 0 && <div className="an-split-a" style={{ width: `${pa}%` }} title={`${la}: ${num(a)}`} />}
        {b > 0 && <div className="an-split-b" style={{ width: `${100 - pa}%` }} title={`${lb}: ${num(b)}`} />}
      </div>
      <div className="an-legend">
        <span><i className="an-sw-a" />{la} <b>{num(a)}</b> ({pa}%)</span>
        <span><i className="an-sw-b" />{lb} <b>{num(b)}</b> ({100 - pa}%)</span>
      </div>
    </div>
  );
}

const Tile = ({ label, value, sub, tone }) => (
  <div className={`recon-tile${tone ? ` ${tone}` : ''}`}><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>
);

// Short money for the KPI cards (LKR 89.85M); the full amount is in the caption.
const shortMoney = (list) => {
  if (!list || !list.length) return '—';
  return list.map(r => {
    const a = Number(r.amount || 0);
    const v = a >= 1e6 ? `${(a / 1e6).toFixed(a >= 1e8 ? 0 : 2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : a.toLocaleString();
    return `${r.currency} ${v}`;
  }).join(' · ');
};

const KPI_ICONS = {
  bookings: <path d="M3 8.5V6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.5a2.5 2.5 0 0 0 0 5V16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2.5a2.5 2.5 0 0 0 0-5zM14 5v12" />,
  tickets: <path d="M5 12.5l4.2 4.2L19 7" />,
  revenue: <path d="M12 3v18M16.5 7.5c0-1.9-2-3-4.5-3s-4.5 1.2-4.5 3.2c0 4.6 9 2.4 9 7 0 2-2 3.3-4.5 3.3S7.5 16 7.5 14" />,
  customers: <path d="M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c.9-3.4 3.4-5.3 6.5-5.3s5.6 1.9 6.5 5.3M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.9c1.8.7 3 2.5 3.5 5.1" />,
  extras: <path d="M7 7h10l1 13H6L7 7zM9.5 7V5.5a2.5 2.5 0 0 1 5 0V7" />,
  searches: <path d="M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM20 20l-4.8-4.8" />,
  conversion: <path d="M4 18l5-6 4 3.5L20 6M15 6h5v5" />,
};

const Kpi = ({ icon, label, value, sub, accent = 'blue', title }) => (
  <div className={`an-kpi an-kpi-${accent}`} title={title}>
    <div className="an-kpi-top">
      <span className="an-kpi-label">{label}</span>
      <span className="an-kpi-icon" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{KPI_ICONS[icon]}</svg>
      </span>
    </div>
    <div className="an-kpi-value">{(() => {
      const m = /^([A-Z]{3}) (.+)$/.exec(String(value));
      return m ? <><span className="an-kpi-cur">{m[1]}</span>{m[2]}</> : value;
    })()}</div>
    {sub && <div className="an-kpi-sub">{sub}</div>}
  </div>
);

const Card = ({ title, children, wide }) => (
  <section className={`an-card${wide ? ' wide' : ''}`}>
    {title && <div className="an-card-head"><h4>{title}</h4></div>}
    <div className="an-card-body">{children}</div>
  </section>
);

// ── Sections ───────────────────────────────────────────────────────────────────

function Overview({ d }) {
  const v = d.verticals || {};
  return (
    <>
      <div className="an-kpis">
        <Kpi icon="revenue" accent="navy" label="Ticket revenue" value={shortMoney(d.revenue)} sub={moneyList(d.revenue)} title="Fares of tickets in force" />
        <Kpi icon="tickets" accent="green" label="Tickets issued" value={num(d.ticketed)} sub={`${num(d.cancelled)} cancelled / voided`} />
        <Kpi icon="bookings" accent="blue" label="Bookings" value={num(d.bookings)} sub={`${num(d.passengers)} passengers`} />
        <Kpi icon="extras" accent="coral" label="Seats & extras" value={shortMoney(d.extras_revenue)} sub={`${num(d.extras_items)} paid · ${moneyList(d.extras_revenue)}`} />
        <Kpi icon="customers" accent="violet" label="Customers" value={num(d.customers)} sub={`${num(d.customer_accounts)} with an account · ${num(d.repeat_customers)} repeat`} />
        <Kpi icon="searches" accent="sky" label="Searches" value={num(d.searches)} sub={`${num(d.searches_no_results)} with no results`} />
        <Kpi icon="conversion" accent="amber" label="Search → booking" value={pct(d.conversion_pct)} sub={`last ${d.period_days} days: ${pct(d.period_conversion_pct)}`} />
      </div>
      <div className="an-grid">
        <Card title={`Searches per day (last ${d.period_days} days)`}>
          <DayChart series={d.daily} field="searches" label="Searches" />
        </Card>
        <Card title={`Bookings per day (last ${d.period_days} days)`}>
          <DayChart series={d.daily} field="bookings" label="Bookings" />
        </Card>
        <Card title="GDS vs NDC bookings">
          <SplitBar a={d.gds} b={d.ndc} la="GDS" lb="NDC" />
        </Card>
        <Card title="Booking status">
          <BarList rows={(d.by_status || []).map(r => ({ label: r.label, value: r.n }))} />
        </Card>
        <Card title="Cabin">
          <BarList rows={(d.by_cabin || []).map(r => ({ label: r.label, value: r.n }))} />
        </Card>
        <Card title="Trip type and payment">
          <BarList rows={[
            ...(d.by_trip || []).map(r => ({ key: `t-${r.label}`, label: { oneway: 'One way', return: 'Return', multicity: 'Multi-city' }[r.label] || r.label, value: r.n })),
            ...(d.by_payment || []).map(r => ({ key: `p-${r.label}`, label: `Paid by ${r.label}`, value: r.n })),
          ]} />
        </Card>
        <Card title="Hotels, visa and tour packages">
          <div className="recon-tiles an-tiles-sm">
            {[['hotel', 'Hotel bookings'], ['visa', 'Visa consultations'], ['package', 'Tour package requests']].map(([k, l]) => (
              <Tile key={k} label={l} value={v[k]?.total ? num(v[k].total) : 'No data yet'}
                    sub={v[k]?.total ? Object.entries(v[k].by_status).map(([s, n]) => `${n} ${s}`).join(' · ') : undefined}
                    tone={v[k]?.total ? undefined : 'muted'} />
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}

function CustomerDetail({ api, email, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    setD(null); setErr('');
    api(`/customer?email=${encodeURIComponent(email)}`).then(x => live && setD(x)).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [api, email]);
  if (err) return <div className="error-banner">{err}</div>;
  if (!d) return <div className="loading-state">Loading customer…</div>;
  return <CustomerView d={d} onClose={onClose} />;
}

function CustomerView({ d, onClose }) {
  const c = d.customer;
  return (
    <div className="an-detail">
      <div className="an-detail-head">
        <div>
          <div className="recon-title">{c.full_name || c.email}</div>
          <div className="adm-sub">{c.email}{c.phone_masked ? ` · ${c.phone_masked}` : ''}
            {c.has_account ? ` · account since ${fmtDate(c.account_since)}` : ' · no account (guest)'}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
      </div>
      <div className="recon-tiles an-tiles-sm">
        <Tile label="Bookings" value={num(c.bookings)} sub={`${num(c.ticketed)} ticketed · ${num(c.cancelled)} cancelled`} />
        <Tile label="Spend" value={spendText(c.spend)} sub="tickets in force" />
        <Tile label="Extras" value={spendText(c.extras_spend)} sub={`${num(c.extras_items)} seats / extras paid`} />
        <Tile label="Last trip" value={c.last_trip_route || '—'} sub={c.last_trip_date ? fmtDate(c.last_trip_date) : undefined} />
        <Tile label="Next trip" value={c.next_trip_date ? fmtDate(c.next_trip_date) : '—'} />
        <Tile label="Loyalty points" value={c.loyalty_points != null ? num(c.loyalty_points) : '—'} />
      </div>
      <div className="an-grid">
        <Card title="Airlines flown">
          <BarList rows={Object.entries(c.airlines || {}).map(([k, n]) => ({ label: k, value: n }))} />
        </Card>
        <Card title="Top routes">
          <BarList rows={(d.top_routes || []).map(r => ({ label: r.label, value: r.n }))} />
        </Card>
      </div>
      <h4 className="an-h">Bookings ({d.bookings.length})</h4>
      <div className="adm-table-wrap an-scroll">
        <table className="adm-table">
          <thead><tr><th>Booked</th><th>PNR</th><th>Flight</th><th>Route</th><th>Departs</th><th>Source</th><th>Fare</th><th>Status</th></tr></thead>
          <tbody>
            {d.bookings.map(b => (
              <tr key={b.locator_code}>
                <td className="nowrap">{fmtDateTime(b.booking_date)}</td>
                <td><strong>{b.locator_code}</strong><div className="adm-sub">{b.pax_count} pax · {b.cabin_class || '—'}</div></td>
                <td>{b.flight_number}<div className="adm-sub">{b.airline_name}</div></td>
                <td className="nowrap">{b.origin} → {b.destination}<div className="adm-sub">{b.trip_type === 'return' ? 'Return' : b.trip_type === 'multicity' ? 'Multi-city' : 'One way'}</div></td>
                <td className="nowrap">{fmtDateTime(b.departure_at, '—')}</td>
                <td>{b.fare_source}</td>
                <td className="mono nowrap">{money(b.total_fare, b.currency)}<div className="adm-sub">{b.payment_method}</div></td>
                <td><span className={`rl-chip ${BOOKING_CHIP(b.status)}`}>{b.status}</span></td>
              </tr>
            ))}
            {!d.bookings.length && <tr><td colSpan={8} className="adm-sub">No flight bookings.</td></tr>}
          </tbody>
        </table>
      </div>
      {d.extras.length > 0 && (
        <>
          <h4 className="an-h">Seats and extras ({d.extras.length})</h4>
          <div className="adm-table-wrap an-scroll">
            <table className="adm-table">
              <thead><tr><th>Date</th><th>PNR</th><th>Item</th><th>Airline</th><th>Price</th><th>Result</th></tr></thead>
              <tbody>
                {d.extras.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap">{fmtDateTime(e.created_at)}</td>
                    <td>{e.locator_code}<div className="adm-sub">{SOURCE_TEXT[e.source]}</div></td>
                    <td>{e.item_name}<div className="adm-sub">{e.kind === 'seat' ? 'Seat' : 'Extra'}</div></td>
                    <td>{e.airline_code}</td>
                    <td className="mono nowrap">{money(e.amount, e.currency)}</td>
                    <td><span className={`rl-chip ${OUTCOME_CHIP[e.outcome]}`}>{OUTCOME_TEXT[e.outcome] || e.outcome}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {d.other.length > 0 && (
        <>
          <h4 className="an-h">Hotels, visa and tour packages ({d.other.length})</h4>
          <div className="adm-table-wrap">
            <table className="adm-table">
              <thead><tr><th>Requested</th><th>Type</th><th>What</th><th>Date</th><th>Amount</th><th>Status</th></tr></thead>
              <tbody>
                {d.other.map((o, i) => (
                  <tr key={i}>
                    <td className="nowrap">{fmtDateTime(o.requested_at)}</td>
                    <td>{{ hotel: 'Hotel', visa: 'Visa', package: 'Tour package' }[o.vertical]}</td>
                    <td>{o.title}{o.place && <div className="adm-sub">{o.place}</div>}</td>
                    <td className="nowrap">{fmtDate(o.event_date, '—')}</td>
                    <td className="mono nowrap">{o.amount != null ? money(o.amount, o.currency) : '—'}</td>
                    <td><span className={`rl-chip ${STATUS_CHIP(o.status)}`}>{o.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Customers({ api }) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('spend');
  const [d, setD] = useState(null);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState(null);
  const detailRef = useRef(null);
  useEffect(() => {
    let live = true;
    const tm = setTimeout(() => {
      api(`/customers?sort=${sort}${query.trim() ? `&q=${encodeURIComponent(query.trim())}` : ''}`)
        .then(x => { if (live) { setD(x); setErr(''); } }).catch(e => live && setErr(e.message));
    }, 250);
    return () => { live = false; clearTimeout(tm); };
  }, [api, query, sort]);
  useEffect(() => { if (open && detailRef.current) detailRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, [open]);
  return (
    <>
      <div className="an-toolbar">
        <input className="recon-search" placeholder="Search by name or email" value={query} onChange={e => setQuery(e.target.value)} />
        <div className="adm-chips">
          {[['spend', 'Top spend'], ['bookings', 'Most bookings'], ['recent', 'Latest booking'], ['name', 'Name']].map(([k, l]) => (
            <button key={k} className="adm-chip" aria-pressed={sort === k} onClick={() => setSort(k)}>{l}</button>
          ))}
        </div>
      </div>
      {err && <div className="error-banner">{err}</div>}
      {!d && !err && <div className="loading-state">Loading customers…</div>}
      {d && (
        <>
          <div className="adm-sub an-count">{d.customers.length} of {d.total} customers · click a customer for their 360 view</div>
          <div className="adm-table-wrap an-scroll">
            <table className="adm-table an-click">
              <thead><tr><th>Customer</th><th>Phone</th><th>Bookings</th><th>Spend</th><th>Extras</th><th>Last booking</th><th>Last / next trip</th><th>Other</th></tr></thead>
              <tbody>
                {d.customers.map(c => (
                  <tr key={c.email} onClick={() => setOpen(c.email)} aria-selected={open === c.email} tabIndex={0}
                      onKeyDown={e => { if (e.key === 'Enter') setOpen(c.email); }}>
                    <td><strong>{c.full_name || '—'}</strong><div className="adm-sub">{c.email}</div>
                      {c.has_account ? <span className="rl-chip ok">Account</span> : <span className="rl-chip muted">Guest</span>}</td>
                    <td className="nowrap mono">{c.phone_masked || '—'}</td>
                    <td>{num(c.bookings)}<div className="adm-sub">{num(c.ticketed)} ticketed · {num(c.cancelled)} cancelled</div></td>
                    <td className="mono nowrap">{spendText(c.spend)}</td>
                    <td className="mono nowrap">{c.extras_items ? <>{spendText(c.extras_spend)}<div className="adm-sub">{c.extras_items} items</div></> : '—'}</td>
                    <td className="nowrap">{fmtDate(c.last_booking, '—')}</td>
                    <td className="nowrap">{c.last_trip_route ? `${c.last_trip_route} ${fmtDate(c.last_trip_date)}` : '—'}
                      {c.next_trip_date && <div className="adm-sub">next {fmtDate(c.next_trip_date)}</div>}</td>
                    <td className="nowrap adm-sub">{[c.visa_requests && `${c.visa_requests} visa`, c.package_requests && `${c.package_requests} package`, c.hotel_requests && `${c.hotel_requests} hotel`].filter(Boolean).join(' · ') || '—'}</td>
                  </tr>
                ))}
                {!d.customers.length && <tr><td colSpan={8} className="adm-sub">No customer matches.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
      {open && <div ref={detailRef}><CustomerDetail api={api} email={open} onClose={() => setOpen(null)} /></div>}
    </>
  );
}

function Airlines({ d }) {
  const rows = d.airlines || [];
  return (
    <>
      <div className="an-grid">
        <Card title="Tickets by airline">
          <BarList rows={rows.filter(r => r.ticketed).slice(0, 15).map(r => ({ key: r.airline_code, label: r.airline_name || r.airline_code, sub: r.airline_code, value: r.ticketed }))} />
        </Card>
        <Card title="Ticket revenue by airline">
          <BarList rows={[...rows].sort((a, b) => b.revenue - a.revenue).filter(r => r.revenue).slice(0, 15)
            .map(r => ({ key: r.airline_code, label: r.airline_name || r.airline_code, sub: r.airline_code, value: r.revenue, display: money(r.revenue, r.currency) }))} />
        </Card>
      </div>
      <h4 className="an-h">All airlines ({rows.length})</h4>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th>Airline</th><th>Bookings</th><th>Tickets</th><th>Passengers</th><th>Revenue</th><th>Avg fare</th><th>GDS / NDC</th><th>Cancelled</th><th>Extras paid</th><th>Cheapest in searches</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.airline_code || r.airline_name}>
                <td><strong>{r.airline_name || '—'}</strong><div className="adm-sub">{r.airline_code} · {r.destinations} destinations</div></td>
                <td>{num(r.bookings)}</td>
                <td>{num(r.ticketed)}</td>
                <td>{num(r.passengers)}</td>
                <td className="mono nowrap">{money(r.revenue, r.currency)}</td>
                <td className="mono nowrap">{r.avg_fare ? money(r.avg_fare, r.currency) : '—'}</td>
                <td className="nowrap"><span className="an-gds">{r.gds} GDS</span> · <span className="an-ndc">{r.ndc} NDC</span></td>
                <td>{num(r.cancelled)}</td>
                <td className="mono nowrap">{r.extras_paid ? <>{num(r.extras_paid)}<div className="adm-sub">{money(r.extras_amount, r.currency)}</div></> : '—'}
                  {r.extras_failed > 0 && <div className="adm-sub">{r.extras_failed} failed</div>}</td>
                <td>{num(r.cheapest_in_searches)}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={10} className="adm-sub">No data yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Geography({ d }) {
  const routes = d.searched_routes || [];
  return (
    <>
      <div className="an-grid">
        <Card title="Top destinations (bookings)">
          <BarList rows={(d.destinations || []).slice(0, 15).map(r => ({ key: r.code, label: `${r.city || r.code} (${r.code})`, sub: countryName(r.country), value: r.bookings, display: `${num(r.bookings)} · ${num(r.ticketed)} tkt` }))} />
        </Card>
        <Card title="Destination countries (bookings)">
          <BarList rows={(d.countries || []).slice(0, 15).map(r => ({ key: r.country || '-', label: countryName(r.country), sub: `${r.airports} airport${r.airports === 1 ? '' : 's'}`, value: r.bookings, display: `${num(r.bookings)} · ${money(r.revenue, r.currency)}` }))} />
        </Card>
        <Card title="Departure airports">
          <BarList rows={(d.origins || []).map(r => ({ key: r.code, label: `${r.city || r.code} (${r.code})`, sub: countryName(r.country), value: r.bookings }))} />
        </Card>
        <Card title="Search demand by destination country">
          <BarList rows={(d.search_countries || []).map(r => ({ key: r.country || '-', label: countryName(r.country), value: r.searches }))} />
        </Card>
      </div>
      <h4 className="an-h">Most searched routes</h4>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th>Route</th><th>Searches</th><th>Bookings</th><th>Search → booking</th><th>No results</th><th>Avg results</th><th>Lowest fare seen</th><th>Last searched</th></tr></thead>
          <tbody>
            {routes.map(r => (
              <tr key={`${r.origin}-${r.destination}`}>
                <td className="nowrap"><strong>{r.origin} → {r.destination}</strong><div className="adm-sub">{r.city}</div></td>
                <td>{num(r.searches)}</td>
                <td>{num(r.bookings)}</td>
                <td>{pct(r.conversion_pct)}</td>
                <td>{r.no_results ? <span className="rl-chip warn">{r.no_results}</span> : '0'}</td>
                <td>{r.avg_results ?? '—'}</td>
                <td className="mono nowrap">{r.min_price ? money(r.min_price, r.currency) : '—'}</td>
                <td className="nowrap">{fmtDateTime(r.last_searched)}</td>
              </tr>
            ))}
            {!routes.length && <tr><td colSpan={8} className="adm-sub">No searches yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <h4 className="an-h">Countries</h4>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th>Country</th><th>Airports</th><th>Bookings</th><th>Tickets</th><th>Ticket revenue</th></tr></thead>
          <tbody>
            {(d.countries || []).map(r => (
              <tr key={r.country || '-'}>
                <td><strong>{countryName(r.country)}</strong><div className="adm-sub">{r.country || '—'}</div></td>
                <td>{r.airports}</td><td>{num(r.bookings)}</td><td>{num(r.ticketed)}</td>
                <td className="mono nowrap">{money(r.revenue, r.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Extras({ d }) {
  const outcomes = useMemo(() => {
    const o = {};
    (d.by_kind || []).forEach(r => {
      const x = o[r.outcome] || (o[r.outcome] = { n: 0, amount: 0, currency: r.currency });
      x.n += r.n; x.amount += r.amount || 0;
    });
    return o;
  }, [d]);
  const kinds = useMemo(() => {
    const k = {};
    (d.by_kind || []).forEach(r => { (k[r.kind] || (k[r.kind] = {}))[r.outcome] = r; });
    return k;
  }, [d]);
  return (
    <>
      <div className="recon-tiles">
        {['paid', 'failed', 'open', 'not_sold'].map(k => (
          <Tile key={k} label={OUTCOME_TEXT[k]} value={num(outcomes[k]?.n)} sub={outcomes[k] ? money(outcomes[k].amount, outcomes[k].currency) : undefined}
                tone={k === 'paid' ? 'ok' : k === 'failed' && outcomes[k]?.n ? 'bad' : k === 'not_sold' ? 'muted' : undefined} />
        ))}
      </div>
      <div className="an-grid">
        <Card title="Seats vs extras (paid)">
          <SplitBar a={kinds.seat?.paid?.n || 0} b={kinds.extra?.paid?.n || 0} la="Seats" lb="Extras" />
          <div className="adm-table-wrap an-mt">
            <table className="adm-table an-compact">
              <thead><tr><th>Kind</th><th>Paid</th><th>Failed</th><th>Awaiting</th><th>Not sold</th></tr></thead>
              <tbody>
                {Object.entries(kinds).map(([k, o]) => (
                  <tr key={k}>
                    <td><strong>{KIND_TEXT[k] || k}</strong></td>
                    {['paid', 'failed', 'open', 'not_sold'].map(x => (
                      <td key={x} className="mono nowrap">{num(o[x]?.n)}{o[x] && <div className="adm-sub">{money(o[x].amount, o[x].currency)}</div>}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Most bought extras">
          <BarList rows={(d.top_items || []).map(r => ({ key: r.item_name, label: r.item_name || '—', sub: `avg ${money(r.avg_price, r.currency)}`, value: r.n, display: `${num(r.n)} · ${money(r.amount, r.currency)}` }))} />
        </Card>
        <Card title="When customers buy them">
          <BarList rows={(d.by_source || []).filter(r => r.outcome === 'paid').map(r => ({ key: r.source, label: SOURCE_TEXT[r.source] || r.source, value: r.n, display: `${num(r.n)} · ${money(r.amount, r.currency)}` }))} />
        </Card>
      </div>
      <h4 className="an-h">By airline</h4>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th>Airline</th><th>Paid</th><th>Seats</th><th>Extras</th><th>Paid amount</th><th>Failed</th><th>Awaiting</th><th>Not sold</th></tr></thead>
          <tbody>
            {(d.by_airline || []).map(r => (
              <tr key={r.airline_code || '-'}>
                <td><strong>{r.airline_name || '—'}</strong><div className="adm-sub">{r.airline_code}</div></td>
                <td>{num(r.paid)}</td><td>{num(r.seats)}</td><td>{num(r.extras)}</td>
                <td className="mono nowrap">{money(r.paid_amount, r.currency)}</td>
                <td>{r.failed ? <span className="rl-chip bad">{r.failed}</span> : '0'}</td>
                <td>{num(r.open_items)}</td><td>{num(r.not_sold)}</td>
              </tr>
            ))}
            {!(d.by_airline || []).length && <tr><td colSpan={8} className="adm-sub">No data yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Vertical({ d, kind }) {
  const v = d[kind] || { total: 0, rows: [] };
  const label = { hotel: 'hotel bookings', visa: 'visa consultation requests', package: 'tour package requests' }[kind];
  if (!v.total) {
    return <div className="an-empty an-empty-big">No data yet — no {label} have been made. They will show here after the next refresh once they start.</div>;
  }
  return (
    <>
      <div className="recon-tiles">
        <Tile label="Total" value={num(v.total)} />
        {Object.entries(v.by_status).map(([s, n]) => <Tile key={s} label={s} value={num(n)} tone={STATUS_CHIP(s) === 'ok' ? 'ok' : undefined} />)}
        {v.amount.length > 0 && <Tile label={kind === 'package' ? 'Package value (price × travellers)' : 'Booking value'} value={moneyList(v.amount)} />}
      </div>
      <div className="an-grid">
        <Card title={kind === 'visa' ? 'Destinations asked about' : kind === 'hotel' ? 'Cities' : 'Packages by destination'}>
          <BarList rows={v.places.map(p => ({ label: p.label, value: p.n }))} />
        </Card>
      </div>
      <h4 className="an-h">Latest {label}</h4>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th>Requested</th><th>Customer</th><th>What</th><th>{kind === 'hotel' ? 'Check-in' : kind === 'visa' ? 'Slot' : 'Preferred date'}</th><th>{kind === 'hotel' ? 'Rooms' : 'Travellers'}</th><th>Amount</th><th>Status</th></tr></thead>
          <tbody>
            {v.rows.map(r => (
              <tr key={r.source_id}>
                <td className="nowrap">{fmtDateTime(r.requested_at)}</td>
                <td>{r.customer_name || '—'}<div className="adm-sub">{r.customer_email}</div></td>
                <td>{r.title}{r.place && <div className="adm-sub">{r.place}</div>}</td>
                <td className="nowrap">{fmtDate(r.event_date, '—')}</td>
                <td>{r.travellers ?? '—'}</td>
                <td className="mono nowrap">{r.amount != null ? money(r.amount, r.currency) : '—'}</td>
                <td><span className={`rl-chip ${STATUS_CHIP(r.status)}`}>{r.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

function Login({ API_BASE, onSignedIn, notice }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      const res = await fetch(`${API_BASE}/analytics/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Sign-in failed');
      onSignedIn(data);
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  };
  return (
    <div className="an-login">
      <div className="an-login-brand">
        <TickBookLogo light size={46} />
        <div className="an-login-pitch">
          <span className="an-login-kicker">Business Analytics</span>
          <h1>Your travel business, at a glance.</h1>
          <ul>
            <li>Bookings, tickets and revenue</li>
            <li>Customers 360 and repeat travellers</li>
            <li>Airlines, routes, countries and airports</li>
            <li>Seats, extras, hotels, visa and tour packages</li>
          </ul>
        </div>
        <small>Own analytics database · refreshed every 30 minutes</small>
      </div>
      <form className="an-login-card" onSubmit={submit}>
        <div className="adm-eyebrow">Tick and Book</div>
        <h2>Sign in</h2>
        <p className="adm-sub an-wrap">Use your analytics account. Accounts are created by the Tick and Book main admin.</p>
        {notice && <div className="an-notice">{notice}</div>}
        {err && <div className="error-banner">{err}</div>}
        <label className="form-label" htmlFor="an-user">Username</label>
        <input id="an-user" className="form-input" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required />
        <label className="form-label" htmlFor="an-pass">Password</label>
        <input id="an-pass" className="form-input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required />
        <button className="btn btn-primary" type="submit" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  );
}

export default function AnalyticsDashboard({ API_BASE = '/api' }) {
  const [token, setToken] = useState(readToken);
  const [user, setUser] = useState('');
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState('overview');
  const [cache, setCache] = useState({});
  const [lastSync, setLastSync] = useState(null);
  const [err, setErr] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const signOut = useCallback((msg = '') => {
    saveToken(''); setToken(''); setUser(''); setCache({}); setLastSync(null); setNotice(msg);
  }, []);

  const api = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/analytics${path}`, {
      ...opts, headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 || res.status === 403) {
      signOut(typeof data.detail === 'string' ? data.detail : 'Please sign in again.');
      throw new Error('Please sign in again.');
    }
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [API_BASE, token, signOut]);

  useEffect(() => {
    if (!token) return;
    api('/me').then(d => setUser(d.username)).catch(() => {});
  }, [token, api]);

  const path = TAB_PATH[tab];
  useEffect(() => {
    if (!token || !path || cache[path]) return;
    let live = true;
    setErr('');
    api(path).then(d => {
      if (!live) return;
      setCache(c => ({ ...c, [path]: d }));
      if (d.last_sync !== undefined) setLastSync(d.last_sync);
    }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [token, path, cache, api]);

  // the header needs the last refresh time even when another tab opens first
  useEffect(() => {
    if (!token || tab === 'overview' || lastSync || cache['/overview']) return;
    api('/overview').then(d => { setCache(c => ({ ...c, '/overview': d })); setLastSync(d.last_sync); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const refresh = async () => {
    setRefreshing(true); setErr('');
    try {
      const d = await api('/refresh', { method: 'POST' });
      setLastSync(d.last_sync);
      setCache({});
    } catch (e) { setErr(e.message); }
    setRefreshing(false);
  };

  if (!token) {
    return (
      <div className="tab-content animate-fade adm an-page">
        <Login API_BASE={API_BASE} notice={notice}
               onSignedIn={(d) => { saveToken(d.access_token); setNotice(''); setCache({}); setToken(d.access_token); setUser(d.username); }} />
      </div>
    );
  }

  const data = path ? cache[path] : null;
  return (
    <div className="tab-content animate-fade adm an-page an-app">
      <header className="an-appbar">
        <div className="an-appbar-row">
          <div className="an-brand">
            <TickBookLogo light size={34} />
            <span className="an-brand-divider" aria-hidden="true" />
            <span className="an-brand-title">Business Analytics</span>
          </div>
          <div className="an-appbar-right">
            <span className="an-sync" title="The analytics database is rebuilt from the booking system every 30 minutes">
              <i className={`an-dot${lastSync?.refreshed_at ? ' on' : ''}`} />
              {lastSync?.refreshed_at ? `Updated ${fmtDateTime(lastSync.refreshed_at)}` : 'Not refreshed yet'}
            </span>
            <button className="an-btn" onClick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : '↻ Refresh'}</button>
            {user && <span className="an-user" title={`Signed in as ${user}`}><b>{user.slice(0, 1).toUpperCase()}</b>{user}</span>}
            <button className="an-btn an-btn-ghost" onClick={() => signOut('')}>Sign out</button>
          </div>
        </div>
        <nav className="an-nav" role="tablist" aria-label="Dashboard sections">
          {TABS.map(x => (
            <button key={x.id} role="tab" className="an-nav-item" aria-selected={tab === x.id} onClick={() => setTab(x.id)}>{x.label}</button>
          ))}
        </nav>
      </header>

      <div className="an-pagehead">
        <div>
          <h2>{TABS.find(x => x.id === tab)?.label}</h2>
          <p>Tick and Book · live figures from the booking system{lastSync?.refreshed_at ? ` · as of ${fmtDateTime(lastSync.refreshed_at)}` : ''}</p>
        </div>
      </div>

      {err && <div className="error-banner">{err}</div>}

      {tab === 'customers' ? <Customers key={lastSync?.refreshed_at || 'c'} api={api} />
        : !data ? (!err && <div className="loading-state">Loading…</div>)
          : tab === 'overview' ? <Overview d={data} />
            : tab === 'airlines' ? <Airlines d={data} />
              : tab === 'geo' ? <Geography d={data} />
                : tab === 'extras' ? <Extras d={data} />
                  : <Vertical d={data} kind={tab} />}

      <p className="adm-sub an-foot an-wrap">
        Figures come from Tick and Book's own booking records, copied into the separate analytics database. Revenue counts tickets
        currently in force (cancelled and voided tickets are excluded). Phone numbers show only their last 3 digits; passport details are never shown.
      </p>
    </div>
  );
}
