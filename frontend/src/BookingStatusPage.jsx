// Customer Portal → Booking Status: look a booking up by PNR or 13-digit
// ticket number plus the passenger's last name, and see it exactly as
// Travelport and the airline hold it (POST /api/booking-status/lookup) —
// flights, ticket coupons, seats, extras/EMDs, special requests. The booking
// doesn't have to be one made here or linked to this account. The last name
// must match a passenger on the booking, so a reference alone never reveals
// anyone's itinerary. Manage My Booking (ManageMyBooking.jsx) is in the
// Seats / Extras / Special requests tabs.
//
// Layout (Tick and Book look, styles in index.css under ".mmb"): hero →
// "Find your booking" card → booking summary (reference, status, route,
// dates) → notices (payment due, removed extras, invoices) → tabs.
import { useEffect, useState } from 'react';
import { tone, COUPON_LABELS } from './LiveBookingPanel.jsx';
import ManageMyBooking, { startAddonPayment, formatDeadline, flightDirections } from './ManageMyBooking.jsx';
import { fmtDate, fmtDateTime, fmtDateWeekday, fmtTime } from './dateFormat.js';
import AddonInvoice from './AddonInvoice.jsx';
import { IconSeat, IconBag, IconWheelchair, IconPlane, IconCalendar, IconUsers, IconUser, IconInfo } from './WizardIcons.jsx';

const Svg = ({ size = 18, children }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
const IconTicket = (p) => <Svg {...p}><path d="M3 8.5V6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.5a2.5 2.5 0 0 0 0 5V16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2.5a2.5 2.5 0 0 0 0-5z" /><path d="M14 5v12" strokeDasharray="2 2.2" /></Svg>;
const IconSearch = (p) => <Svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></Svg>;
const IconRefresh = (p) => <Svg {...p}><path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4" /></Svg>;
const IconCheck = (p) => <Svg {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Svg>;
const IconAlert = (p) => <Svg {...p}><path d="M12 8v5M12 16.5v.5" /><path d="M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></Svg>;
const IconList = (p) => <Svg {...p}><path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></Svg>;

const money = (cur, amt) => `${cur || ''} ${Number(amt || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();
const toneClass = (status) => {
  const t = tone(status);
  return t.fg === '#166534' ? 'ok' : t.fg === '#991b1b' ? 'bad' : 'warn';
};
const Chip = ({ status, children }) => <span className={`mmb-chip ${toneClass(status ?? children)}`}>{children}</span>;
// The airline's answer to a seat on the PNR, in the customer's words.
const SEAT_STATUS_LABELS = { confirmed: 'Confirmed', pending: 'Pending airline approval', requested: 'Pending airline approval',
                             rejected: 'Rejected by the airline', unable: 'Rejected by the airline', cancelled: 'Cancelled' };

const BOOKING_STATE = {
  ticketed: ['Ticketed', 'ok'], confirmed: ['Confirmed', 'ok'], cancelled: ['Cancelled', 'bad'],
  airline_cancelled: ['Cancelled by the airline', 'bad'], airline_problem: ['Needs attention', 'warn'],
};

const TABS = [
  { id: 'overview', label: 'Booking overview', icon: IconList },
  { id: 'seats', label: 'Choose seats', icon: IconSeat },
  { id: 'extras', label: 'Add extras', icon: IconBag },
  { id: 'requests', label: 'Special requests', icon: IconWheelchair },
];

function AirlineLogo({ code }) {
  const [failed, setFailed] = useState(false);
  if (!code) return null;
  return (
    <div className="mmb-airline" title={code}>
      {failed ? <strong>{code}</strong> : (
        <img src={`https://www.gstatic.com/flights/airline_logos/70px/${code}.png`} alt={code} onError={() => setFailed(true)} />
      )}
    </div>
  );
}

// The trip at a glance: airline, route, departure / return, passengers, cabin.
function TripSummary({ data }) {
  const flown = flightDirections(data.flights || []);
  if (!flown.length) return null;
  const first = flown[0];
  const back = flown.find(f => f.dir === 'Return');
  const outbound = flown.filter(f => f.dir !== 'Return');
  const end = back ? outbound[outbound.length - 1] : flown[flown.length - 1];
  const pax = (data.travelers || []).length;
  const fact = (Icon, label, value, sub) => (
    <div className="mmb-fact">
      <Icon size={18} />
      <div><small>{label}</small><strong>{value}</strong>{sub && <small>{sub}</small>}</div>
    </div>
  );
  return (
    <div className="mmb-trip">
      <AirlineLogo code={(first.flight || '').slice(0, 2)} />
      <div className="mmb-route">
        <b>{first.from} <span aria-hidden="true">{back ? '⇄' : '→'}</span> {end?.to}</b>
        <span>{back ? 'Round trip' : flown.length > 1 ? 'One way' : 'Direct flight'} · {flown.length} flight{flown.length > 1 ? 's' : ''}</span>
      </div>
      {fact(IconCalendar, 'Departs', fmtDateWeekday(first.departure), fmtTime(first.departure))}
      {back && fact(IconCalendar, 'Returns', fmtDateWeekday(back.departure), fmtTime(back.departure))}
      {pax > 0 && fact(IconUsers, 'Passengers', `${pax} passenger${pax > 1 ? 's' : ''}`)}
      {first.cabin && fact(IconSeat, 'Cabin', `${first.cabin}${first.class ? ` (${first.class})` : ''}`)}
    </div>
  );
}

// Overview tab: the booking exactly as the airline holds it.
function Overview({ data, onGo }) {
  const flown = flightDirections(data.flights || []);
  const list = (items, empty, render) => (items?.length ? items.map(render) : <div className="mmb-muted">{empty}</div>);
  return (
    <div className="mmb-card">
      <div className="mmb-card-head">
        <span className="mmb-icon"><IconList size={20} /></span>
        <div>
          <h3>Booking overview</h3>
          <p className="mmb-sub">Your booking as the airline holds it right now.</p>
        </div>
      </div>

      {flown.length > 0 && (
        <>
          <h4 className="mmb-section-title"><IconPlane size={16} /> Flights <span className="mmb-count">{flown.length}</span></h4>
          <div className="mmb-flights" role="table" aria-label="Flights">
            <div className="mmb-flight mmb-flight-head" role="row">
              <span /><span>Flight</span><span>Departure</span><span>Cabin</span><span>Airline status</span>
            </div>
            {flown.map(f => (
              <div key={`${f.flight}-${f.sequence}`} className="mmb-flight" role="row">
                <span className="mmb-flight-icon"><IconPlane size={15} /></span>
                <span className="mmb-flight-main"><strong>{f.flight}</strong> <span>{f.from} → {f.to}</span><small>{f.dir}</small></span>
                <span className="mmb-flight-when">{fmtDateWeekday(f.departure)} · {fmtTime(f.departure)}{f.arrival ? ` – ${fmtTime(f.arrival)}` : ''}</span>
                <span className="mmb-flight-cabin">{f.cabin ? `${f.cabin}${f.class ? ` (${f.class})` : ''}` : f.class ? `Class ${f.class}` : '–'}</span>
                <span><Chip status={f.state}>{f.airline_status || (f.airline_status_code ? 'Airline status' : 'No airline status')}{f.airline_status_code ? ` · ${f.airline_status_code}` : ''}</Chip></span>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="mmb-grid">
        <div className="mmb-mini">
          <h4><IconTicket size={16} /> Tickets</h4>
          {list(data.tickets, 'No ticket issued yet.', t => (
            <div key={t.number} className="mmb-mini-item">
              <div className="mmb-mini-row"><strong className="mmb-mono">{t.number}</strong><span className="mmb-muted">{t.issued ? `issued ${fmtDate(t.issued)}` : ''}</span></div>
              {t.passenger && <div className="mmb-muted">{t.passenger}</div>}
              <div className="mmb-chips">
                {t.document_status && <Chip status={t.document_status === 'issued' ? 'ok' : t.document_status}>{t.status_label || t.document_status}</Chip>}
                {t.error ? <span className="mmb-muted">{t.error}</span> : (t.coupons || []).map((c, i) => (
                  <Chip key={i} status={c.status}>{c.flight} {COUPON_LABELS[c.status] || c.status}</Chip>
                ))}
              </div>
              {t.coupons_note && !(t.coupons || []).length && <div className="mmb-muted">{t.coupons_note}</div>}
            </div>
          ))}
        </div>

        <div className="mmb-mini">
          <h4><IconUser size={16} /> Passengers <span className="mmb-count">{(data.travelers || []).length}</span></h4>
          {list(data.travelers, 'No passengers returned.', t => (
            <div key={t.id} className="mmb-mini-row"><strong>{t.name}</strong>{t.type && <span className="mmb-muted">{t.type}</span>}</div>
          ))}
        </div>

        <div className="mmb-mini">
          <h4><IconSeat size={16} /> Seats</h4>
          {/* Seats on the PNR with the airline's answer, then requests the
              airline refused (they never reach the PNR) with its reason. */}
          {(data.seats || []).map((s, i) => (
            <div key={`p${i}`} className="mmb-mini-row">
              <span><strong>{s.seat}</strong> <span className="mmb-muted">{(s.flights || []).join(', ')}{s.passenger ? ` · ${s.passenger}` : ''}{s.emd ? ` · EMD ${s.emd}` : ''}</span></span>
              <Chip status={s.status}>{SEAT_STATUS_LABELS[(s.status || '').toLowerCase()] || s.status}</Chip>
            </div>
          ))}
          {(data.seat_requests || []).map((r, i) => (
            <div key={`r${i}`} className="mmb-mini-item">
              <div className="mmb-mini-row">
                <span><strong>{r.seat}</strong> <span className="mmb-muted">{r.flight || ''}{r.passenger ? ` · ${r.passenger}` : ''}</span></span>
                <Chip status={r.state === 'rejected' ? 'rejected' : 'pending'}>{r.label}</Chip>
              </div>
              {r.reason && <div className="mmb-muted mmb-seat-reason">{r.reason}</div>}
            </div>
          ))}
          {!(data.seats || []).length && !(data.seat_requests || []).length && <div className="mmb-muted">{data.seats_note || 'No seats on the booking yet.'}</div>}
          {(data.seat_rules || []).map(c => (
            <div key={c.flight} className={`mmb-seat-rule${c.closed ? ' closed' : ''}`}>
              <IconInfo size={14} />
              <span>{c.closed
                ? `${c.flight}: online seat selection has closed — the airline stops it ${c.hours} hours before departure. Choose your seat with the airline at check-in.`
                : `${c.flight}: the airline accepts seat requests until ${c.hours} hours before departure.`}</span>
            </div>
          ))}
          {data.can_add_seats_extras && !(data.seat_rules || []).every(c => c.closed) &&
            <button type="button" className="mmb-link" onClick={() => onGo('seats')}><IconSeat size={14} /> Choose seats</button>}
        </div>

        <div className="mmb-mini">
          <h4><IconBag size={16} /> Extras</h4>
          {list(data.extras, 'No extras on the booking yet.', (x, i) => (
            <div key={i} className="mmb-mini-row">
              <span><strong>{x.description}</strong> <span className="mmb-muted">{(x.flights || []).join(', ')}{x.passenger ? ` · ${x.passenger}` : ''}{x.emd ? ` · EMD ${x.emd}` : ''}</span></span>
              <Chip status={x.status === 'issued' ? 'ok' : x.status}>{x.status_label || x.status}</Chip>
            </div>
          ))}
          {data.can_add_seats_extras && <button type="button" className="mmb-link" onClick={() => onGo('extras')}><IconBag size={14} /> Add extras</button>}
        </div>

        <div className="mmb-mini mmb-mini-wide">
          <h4><IconWheelchair size={16} /> Special requests</h4>
          {list(data.special_services, 'No special requests on the booking yet.', (s, i) => (
            <div key={i} className="mmb-mini-row">
              <span><strong>{s.description}</strong> <span className="mmb-muted">{(s.flights || []).join(', ')}{s.passenger ? ` · ${s.passenger}` : ''}</span></span>
              <Chip status={s.status}>{s.status || 'Airline status'}{s.status_code ? ` · ${s.status_code}` : ''}</Chip>
            </div>
          ))}
          {data.can_add_special_requests && <button type="button" className="mmb-link" onClick={() => onGo('requests')}><IconWheelchair size={14} /> Add a special request</button>}
        </div>
      </div>
    </div>
  );
}

// prefill: {query, lastName, key} — set when opened from a My Bookings detail page; looks it up at once.
export default function BookingStatusPage({ apiBase, fetchWithRetry, authToken, prefill }) {
  const [query, setQuery] = useState('');
  const [lastName, setLastName] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('overview');
  const [manageMounted, setManageMounted] = useState(false);
  const [paying, setPaying] = useState(false);
  const [invoiceId, setInvoiceId] = useState(null);
  const [tabNote, setTabNote] = useState('');

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
    const fresh = !!e || q.trim().toUpperCase() !== (data?.locator_code || '');
    setLoading(true);
    setError('');
    setTabNote('');
    try {
      const res = await fetchWithRetry(`${apiBase}/booking-status/lookup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` },
        body: JSON.stringify({ query: q.trim(), last_name: ln.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not reach the airline');
      if (fresh && body.locator_code !== data?.locator_code) { setTab('overview'); setManageMounted(false); }
      setData(body);
    } catch (err) {
      setError(err.message || String(err));
      setData(null);
      setTab('overview');
      setManageMounted(false);
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

  const go = (id) => {
    setTab(id);
    if (id !== 'overview') setManageMounted(true);
  };

  const retrieved = data?.retrieved_at ? fmtDateTime(data.retrieved_at) : '';
  const [stateLabel, stateTone] = data ? (data.ticketed ? BOOKING_STATE.ticketed : BOOKING_STATE[data.booking_status] || [String(data.booking_status || 'Unknown').replace(/_/g, ' '), 'warn']) : [];
  const tabAllowed = (id) => id === 'overview' || (id === 'requests' ? data?.can_add_special_requests : data?.can_add_seats_extras);
  const pdItems = data?.payment_due?.items || [];

  return (
    <div className="mmb">
      <section className="mmb-hero">
        <h2>Manage My Booking</h2>
        <p>View your booking, choose seats, add extras and manage special requests.</p>
      </section>

      <section className="mmb-card mmb-find">
        <div className="mmb-card-head">
          <span className="mmb-icon"><IconTicket size={20} /></span>
          <div>
            <h3>Find your booking</h3>
            <p className="mmb-sub">Enter your booking reference (PNR) or 13-digit e-ticket number and the last name of a passenger on it.
              The last name keeps other people's bookings private.</p>
          </div>
        </div>
        <form onSubmit={lookup} className="mmb-find-form">
          <div className="mmb-field">
            <label htmlFor="mmb-query">Booking reference or ticket number</label>
            <input id="mmb-query" className="form-input" required value={query} onChange={e => setQuery(e.target.value.toUpperCase())}
                   placeholder="e.g. HNHLRC or 6032903683733" maxLength={20} autoComplete="off" />
          </div>
          <div className="mmb-field">
            <label htmlFor="mmb-last">Passenger last name</label>
            <input id="mmb-last" className="form-input" required value={lastName} onChange={e => setLastName(e.target.value)} placeholder="As on the ticket" maxLength={60} />
          </div>
          <button className="btn btn-primary" type="submit" disabled={loading}>
            <IconSearch size={17} /> {loading ? 'Checking…' : 'Find my booking'}
          </button>
        </form>
        {error && <div className="error-banner" style={{ marginTop: '0.8rem' }}>{error}</div>}
      </section>

      {data && (
        <>
          <section className="mmb-card mmb-summary">
            <div className="mmb-summary-top">
              <div className="mmb-summary-id">
                <span className={`mmb-state ${stateTone}`}>{stateTone === 'ok' ? <IconCheck size={20} /> : <IconAlert size={19} />}</span>
                <div>
                  <div className="mmb-ref">
                    <h3>Booking {data.locator_code}</h3>
                    <span className={`mmb-chip ${stateTone}`}>{stateLabel}</span>
                  </div>
                  <div className="mmb-sub">
                    Live from the airline{retrieved ? ` · checked ${retrieved}` : ''}
                    {data.airline_locators?.length > 0 && <> · Airline reference {data.airline_locators.map(a => <strong key={a.locator}>{a.airline} {a.locator} </strong>)}</>}
                  </div>
                </div>
              </div>
              <button className="btn btn-secondary btn-sm mmb-again" disabled={loading} onClick={() => lookup()}>
                <IconRefresh size={15} /> {loading ? 'Checking…' : 'Check again'}
              </button>
            </div>
            <TripSummary data={data} />
          </section>

          {data.booking_status === 'cancelled' && <div className="mmb-alert bad">This booking is cancelled{data.message ? ` — ${data.message}` : ''}.</div>}
          {data.booking_status === 'airline_cancelled' && <div className="mmb-alert bad">The airline has cancelled the flights on this booking — please contact us.</div>}
          {data.booking_status === 'airline_problem' && <div className="mmb-alert warn">The airline has not confirmed every flight on this booking — please contact us.</div>}

          {data.released_extras?.length > 0 && (
            <div className="mmb-alert bad">
              <strong>Removed from your booking:</strong> {data.released_extras.join(', ')} — not paid by the deadline, so the airline no longer
              has {data.released_extras.length > 1 ? 'them' : 'it'} for you. You have not been charged. You can add it again from Choose seats / Add extras.
            </div>
          )}
          {data.payment_due && (
            <div className="mmb-alert warn mmb-due">
              <div>
                <strong>Payment due:</strong> the airline has confirmed{' '}
                {pdItems.map(i => `${i.name} (${money(data.payment_due.currency, i.price)})`).join(', ')}.
                Pay to keep {pdItems.length > 1 ? 'them' : 'it'} — the airline then issues the document.
                {data.payment_due.deadline_utc && (
                  <div style={{ marginTop: '0.3rem' }}>
                    Pay by <strong>{formatDeadline(data.payment_due.deadline_utc)}</strong>. If {pdItems.length > 1 ? 'they aren’t' : 'it isn’t'} paid
                    by then, {pdItems.length > 1 ? 'they are' : 'it is'} removed from your booking and can’t be used at the airport.
                  </div>
                )}
              </div>
              <button className="btn btn-primary btn-sm" disabled={paying} onClick={payDue}>
                {paying ? 'Opening payment…' : `Pay ${money(data.payment_due.currency, data.payment_due.amount)}`}
              </button>
            </div>
          )}
          {data.extras_invoices?.length > 0 && (
            <div className="mmb-alert ok">
              <strong>Paid seats and extras</strong>
              {data.extras_invoices.map(i => (
                <div key={i.invoice_number} className="mmb-invoice">
                  <span>Invoice {i.invoice_number} · {money(i.currency, i.amount)}{i.paid_at ? ` · paid ${fmtDateTime(i.paid_at)}` : ''}</span>
                  <button className="btn btn-secondary btn-sm" onClick={() => setInvoiceId(i.invoice_number)}>View invoice</button>
                </div>
              ))}
            </div>
          )}

          <nav className="mmb-tabs" role="tablist" aria-label="Manage my booking">
            {TABS.map(t => {
              const allowed = tabAllowed(t.id);
              const Icon = t.icon;
              return (
                <button key={t.id} type="button" role="tab" className={`mmb-tab${allowed ? '' : ' is-off'}`} aria-selected={tab === t.id}
                        aria-disabled={allowed ? undefined : 'true'}
                        title={allowed ? undefined : (data.manage_note || 'Not available online for this booking')}
                        onClick={() => { if (allowed) { setTabNote(''); go(t.id); } else setTabNote(data.manage_note || 'Not available online for this booking.'); }}>
                  <Icon size={17} /> {t.label}
                </button>
              );
            })}
          </nav>
          {tabNote && <div className="mmb-alert warn" role="alert">{tabNote}</div>}

          {tab === 'overview' && <Overview data={data} onGo={go} />}
          {manageMounted && (
            <ManageMyBooking key={data.locator_code} view={tab} manageToken={data.manage_token} apiBase={apiBase}
                             fetchWithRetry={fetchWithRetry} authToken={authToken} onChanged={() => lookup()} />
          )}
          {tab === 'overview' && (
            <p className="mmb-footnote"><IconInfo size={14} /> Everything above is read live from the airline.{TABS.some(t => t.id !== 'overview' && tabAllowed(t.id)) ? ' Changes you make in the other tabs go straight to the airline.' : ''}</p>
          )}
        </>
      )}
      {invoiceId && (
        <AddonInvoice addonId={invoiceId} apiBase={apiBase} fetchWithRetry={fetchWithRetry} authToken={authToken} onClose={() => setInvoiceId(null)} />
      )}
    </div>
  );
}
