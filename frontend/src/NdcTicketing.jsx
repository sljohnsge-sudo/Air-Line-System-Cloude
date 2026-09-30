import { useState, useEffect, useRef } from 'react';

// Same-host convention as App.jsx's API_BASE — see that file for why.
const API_HOST = `http://${window.location.hostname}:8000`;
const API = `${API_HOST}/api/ndc`;
const PAY_API = `${API_HOST}/api/payments`;

const emptyTraveler = (type) => ({
  passenger_type: type, first_name: '', last_name: '', date_of_birth: '', gender: 'Male',
  passport_number: '', passport_expiry: '', nationality: 'LK', passport_issue_country: 'LK',
  email: '', phone: '',
});

const TYPE_LABEL = { ADT: 'Adult', CNN: 'Child', INF: 'Infant' };

// A future date of birth or an already-expired passport are both
// impossible/invalid — Travelport rejects them outright (e.g. "TRAVELER
// DATE OF BIRTH IS MISSING OR INVALID"), and only after a PNR is created
// and cancelled again. These two unlabeled-look-alike date fields are easy
// to mix up, so bound each picker to what's actually valid as a cheap
// first line of defense before that round trip even happens.
const TODAY = new Date().toISOString().split('T')[0];

async function post(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : JSON.stringify(d) || `Request failed (${res.status})`);
  }
  return data;
}

const fmt = (n, c) => `${c || ''} ${Number(n).toLocaleString()}`.trim();

export default function NdcTicketing() {
  const [q, setQ] = useState({ origin: 'CMB', destination: 'DXB', departure_date: '', return_date: '', adult_count: 1, child_count: 0, infant_count: 0 });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState(null);
  const [selected, setSelected] = useState(null);
  const [fareIdx, setFareIdx] = useState(0);
  const [travelers, setTravelers] = useState([]);
  const [airline, setAirline] = useState('ALL');
  const [error, setError] = useState('');

  // step: null (searching/selecting) -> 'confirmed' (PNR held or prepay pending) -> 'ticket'
  const [step, setStep] = useState(null);
  const [booking, setBooking] = useState(false); // POST /book in flight
  const [paying, setPaying] = useState(false);   // redirecting to PayCorp
  const [resuming, setResuming] = useState(true); // checking for a return-from-PayCorp on mount
  const [confirmedInfo, setConfirmedInfo] = useState(null); // {locator_code,...} or {pending_id, amount, currency, carrier}
  const [result, setResult] = useState(null);
  const [verify, setVerify] = useState(null);
  const resumedRef = useRef(false);

  // ── Resume after PayCorp redirects back here ────────────────────────────
  useEffect(() => {
    if (resumedRef.current) return;
    resumedRef.current = true;
    const params = new URLSearchParams(window.location.search);
    const reqid = params.get('reqid');
    const locator = params.get('ndc_locator');
    const pendingId = params.get('ndc_pending');
    const cancelled = params.get('payment') === 'cancelled';

    if (!reqid && !cancelled) { setResuming(false); return; }
    window.history.replaceState({}, '', window.location.pathname);

    (async () => {
      try {
        if (cancelled) {
          setError('Payment was cancelled. Your selection was not booked — search again to retry.');
          return;
        }
        if (locator) {
          const res = await fetch(`${API_HOST}/api/bookings/${locator}/issue-ticket`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reqid }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Ticket issuance failed (${res.status})`);
          const t = data.ticket;
          setResult({
            success: !!t.ticket_number, locator_code: locator, airline_pnr: t.airline_pnr, status: t.status,
            ticket_numbers: t.ticket_numbers || (t.ticket_number ? [{ passenger_type: 'ADT', number: t.ticket_number }] : []),
            total_fare: t.total_fare, currency: t.currency,
            message: t.ticket_number ? null : 'Payment succeeded but Travelport did not return a ticket number. Contact support with this PNR.',
          });
          setStep('ticket');
        } else if (pendingId) {
          setResult(await post('/instant-pay/complete', { pending_id: pendingId, reqid }));
          setStep('ticket');
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setResuming(false);
      }
    })();
  }, []);

  const num = (k) => (e) => setQ((s) => ({ ...s, [k]: Math.max(0, parseInt(e.target.value || '0', 10)) }));
  const txt = (k) => (e) => setQ((s) => ({ ...s, [k]: e.target.value }));

  const search = async (e) => {
    e.preventDefault();
    if (q.infant_count > q.adult_count) { setError('NDC allows only one infant per adult.'); return; }
    setError(''); setResults(null); setSelected(null); setStep(null); setResult(null); setVerify(null); setSearching(true);
    try {
      const body = { ...q, origin: q.origin.toUpperCase(), destination: q.destination.toUpperCase(), return_date: q.return_date || null };
      setAirline('ALL'); setResults(await post('/search', body));
    } catch (err) { setError(err.message); } finally { setSearching(false); }
  };

  const choose = (offer) => {
    setSelected(offer); setFareIdx(0); setStep(null); setResult(null); setVerify(null); setConfirmedInfo(null);
    const list = [];
    for (let i = 0; i < q.adult_count; i++) list.push(emptyTraveler('ADT'));
    for (let i = 0; i < q.infant_count; i++) list.push(emptyTraveler('INF'));
    for (let i = 0; i < q.child_count; i++) list.push(emptyTraveler('CNN'));
    setTravelers(list);
  };

  const setT = (i, k, v) => setTravelers((list) => list.map((t, idx) => (idx === i ? { ...t, [k]: v } : t)));

  // ── Step 1: create the booking (PNR, or a pre-payment hold for Emirates) ─
  const doBook = async (e) => {
    e.preventDefault();
    setError(''); setBooking(true);
    try {
      const res = await post('/book', { raw_offering: selected.fare_options[fareIdx].raw_offering, travelers });
      if (res.requires_prepay) {
        setConfirmedInfo({ pending_id: res.pending_id, amount: selected.fare_options[fareIdx].price ?? selected.price, currency: selected.currency });
      } else {
        setConfirmedInfo({ locator_code: res.locator_code, airline_pnr: res.airline_pnr, total_fare: res.total_fare, currency: res.currency, status: res.status });
      }
      setStep('confirmed');
    } catch (err) { setError(err.message); } finally { setBooking(false); }
  };

  // ── Step 2: "Issue Ticket" — send to PayCorp, ticket is issued on return ─
  const goToPayment = async () => {
    setError(''); setPaying(true);
    try {
      const baseUrl = `${window.location.origin}${window.location.pathname}`;
      const isPrepay = !!confirmedInfo.pending_id;
      const returnParams = isPrepay ? `ndc_pending=${confirmedInfo.pending_id}` : `ndc_locator=${confirmedInfo.locator_code}`;
      const clientRef = isPrepay ? confirmedInfo.pending_id : confirmedInfo.locator_code;
      const amount = isPrepay ? confirmedInfo.amount : confirmedInfo.total_fare;
      const currency = isPrepay ? confirmedInfo.currency : (confirmedInfo.currency || 'LKR');

      const res = await fetch(`${PAY_API}/init`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          amount, currency,
          return_url: `${baseUrl}?tab=ndc&${returnParams}`,
          cancel_url: `${baseUrl}?tab=ndc&${returnParams}&payment=cancelled`,
          client_ref: clientRef,
          comment: `George Steuart Travel - NDC ${clientRef}`,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to start payment');
      window.location.href = data.payment_page_url;
    } catch (err) { setError(err.message); setPaying(false); }
  };

  const verifyTickets = async () => {
    try {
      const nums = result.ticket_numbers.map((t) => t.number).join(',');
      const res = await fetch(`${API}/tickets/${result.locator_code}?numbers=${nums}`);
      setVerify(await res.json());
    } catch (err) { setError(err.message); }
  };

  if (resuming) {
    return (
      <div className="tab-content animate-fade" style={{ maxWidth: '980px', margin: '0 auto' }}>
        <section className="glass-panel" style={{ padding: '2rem', textAlign: 'center' }}>Verifying payment…</section>
      </div>
    );
  }

  return (
    <div className="tab-content animate-fade" style={{ maxWidth: '980px', margin: '0 auto' }}>
      {step !== 'confirmed' && step !== 'ticket' && (
        <section className="search-section glass-panel">
          <h2 className="section-title" style={{ marginBottom: '0.25rem' }}>NDC Ticketing</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: 0 }}>
            Search NDC fares, confirm the booking, then pay to issue the ticket.
          </p>
          {error && <div className="error-banner">{error}</div>}

          <form onSubmit={search} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.75rem' }}>
            <div className="form-group"><label className="form-label">From</label>
              <input className="form-input" required maxLength={3} value={q.origin} onChange={txt('origin')} /></div>
            <div className="form-group"><label className="form-label">To</label>
              <input className="form-input" required maxLength={3} value={q.destination} onChange={txt('destination')} /></div>
            <div className="form-group"><label className="form-label">Depart</label>
              <input type="date" className="form-input" required value={q.departure_date} onChange={txt('departure_date')} /></div>
            <div className="form-group"><label className="form-label">Return (optional)</label>
              <input type="date" className="form-input" value={q.return_date} onChange={txt('return_date')} /></div>
            <div className="form-group"><label className="form-label">Adults</label>
              <input type="number" min="1" className="form-input" value={q.adult_count} onChange={num('adult_count')} /></div>
            <div className="form-group"><label className="form-label">Children</label>
              <input type="number" min="0" className="form-input" value={q.child_count} onChange={num('child_count')} /></div>
            <div className="form-group"><label className="form-label">Infants</label>
              <input type="number" min="0" className="form-input" value={q.infant_count} onChange={num('infant_count')} /></div>
            <div className="form-group" style={{ alignSelf: 'end' }}>
              <button className="btn btn-primary" disabled={searching}>{searching ? 'Searching…' : 'Search NDC'}</button></div>
          </form>
        </section>
      )}

      {results && !selected && step !== 'confirmed' && step !== 'ticket' && (
        <section className="glass-panel" style={{ marginTop: '1rem', padding: '1rem' }}>
          <h3 className="section-title">{results.count} NDC offers</h3>
          {results.carrier_notes.length > 0 && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Carrier notes from Travelport: {results.carrier_notes.join(' · ')}</p>
          )}
          <div className="form-group" style={{ maxWidth: '260px' }}>
            <label className="form-label">Airline</label>
            <select className="form-input" value={airline} onChange={(e) => setAirline(e.target.value)}>
              <option value="ALL">All airlines</option>
              {[...new Set(results.flights.map((f) => f.airline_code))].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          {airline === 'EK' && (
            <p style={{ fontSize: '0.8rem', color: '#92400e' }}>
              Emirates requires payment before Travelport confirms the PNR (Instant Pay) — you'll pay right after clicking Confirm Booking, before a PNR exists.
            </p>
          )}
          {(() => {
            const filtered = results.flights.filter((f) => airline === 'ALL' || f.airline_code === airline);
            if (airline !== 'ALL') return filtered.slice(0, 30);
            // Travelport returns offers grouped by carrier, not interleaved — a flat
            // slice(0, N) here would silently hide entire airlines (e.g. show only
            // the first carrier's offers) depending on which carrier happens to come
            // first in that response. Cap per carrier instead so every airline the
            // dropdown lists is guaranteed to have at least one visible offer.
            const perCarrier = {};
            const capped = [];
            for (const f of filtered) {
              const n = perCarrier[f.airline_code] || 0;
              if (n >= 10) continue;
              perCarrier[f.airline_code] = n + 1;
              capped.push(f);
            }
            return capped;
          })().map((f, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.6rem 0', borderTop: '1px solid #e5e7eb' }}>
              <div>
                <strong>{f.airline}</strong> {f.flight_number} · {f.departure_airport}{f.departure_terminal && ` (Terminal ${f.departure_terminal})`} → {f.arrival_airport}{f.arrival_terminal && ` (Terminal ${f.arrival_terminal})`}
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  {f.departure_time} · {f.stops} stop(s){f.is_round_trip ? ' · round trip' : ''}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontWeight: 700 }}>{fmt(f.price, f.currency)}</div>
                <button className="btn btn-secondary btn-sm" onClick={() => choose(f)}>Select</button>
              </div>
            </div>
          ))}
        </section>
      )}

      {selected && step !== 'confirmed' && step !== 'ticket' && (
        <section className="glass-panel" style={{ marginTop: '1rem', padding: '1rem' }}>
          <h3 className="section-title">
            {selected.airline} {selected.flight_number} · {fmt(selected.price, selected.currency)}
          </h3>
          <button className="btn btn-secondary btn-sm" onClick={() => setSelected(null)}>← Back to results</button>
          {selected.fare_options.length > 1 && (
            <div className="form-group" style={{ marginTop: '0.75rem' }}>
              <label className="form-label">Fare</label>
              <select className="form-input" value={fareIdx} onChange={(e) => setFareIdx(Number(e.target.value))}>
                {selected.fare_options.map((fo, i) => (
                  <option key={i} value={i}>{fo.brand_name || 'Fare'} — {fmt(fo.price, fo.currency)}</option>
                ))}
              </select>
            </div>
          )}

          <form onSubmit={doBook}>
            {travelers.map((t, i) => (
              <div key={i} style={{ borderTop: '1px solid #e5e7eb', marginTop: '0.75rem', paddingTop: '0.75rem' }}>
                <strong>{TYPE_LABEL[t.passenger_type]} {i + 1}</strong>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.6rem', marginTop: '0.4rem' }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">First Name *</label>
                    <input className="form-input" required minLength={2} placeholder="First name" value={t.first_name} onChange={(e) => setT(i, 'first_name', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Last Name *</label>
                    <input className="form-input" required minLength={2} placeholder="Last name" value={t.last_name} onChange={(e) => setT(i, 'last_name', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Date of Birth *</label>
                    <input type="date" className="form-input" required max={TODAY} value={t.date_of_birth} onChange={(e) => setT(i, 'date_of_birth', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Gender</label>
                    <select className="form-input" value={t.gender} onChange={(e) => setT(i, 'gender', e.target.value)}>
                      <option>Male</option><option>Female</option>
                    </select>
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Passport No. *</label>
                    <input className="form-input" required minLength={5} placeholder="Passport no." value={t.passport_number} onChange={(e) => setT(i, 'passport_number', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Passport Expiry *</label>
                    <input type="date" className="form-input" required min={TODAY} value={t.passport_expiry} onChange={(e) => setT(i, 'passport_expiry', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Email *</label>
                    <input type="email" className="form-input" required placeholder="Email" value={t.email} onChange={(e) => setT(i, 'email', e.target.value)} />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Phone *</label>
                    <input type="tel" className="form-input" required minLength={7} placeholder="Phone" value={t.phone} onChange={(e) => setT(i, 'phone', e.target.value)} />
                  </div>
                </div>
              </div>
            ))}
            {error && <div className="error-banner" style={{ marginTop: '1rem' }}>{error}</div>}
            <button className="btn btn-primary" style={{ marginTop: '1rem' }} disabled={booking}>
              {booking ? 'Booking…' : 'Confirm Booking'}
            </button>
          </form>
        </section>
      )}

      {step === 'confirmed' && confirmedInfo && (
        <section className="glass-panel" style={{ marginTop: '1rem', padding: '1.25rem' }}>
          {confirmedInfo.locator_code ? (
            <>
              <h3 className="section-title">✅ Your booking is confirmed</h3>
              <p>PNR <strong>{confirmedInfo.locator_code}</strong>{confirmedInfo.airline_pnr ? ` · Airline ref ${confirmedInfo.airline_pnr}` : ''} · Status {confirmedInfo.status}</p>
              {confirmedInfo.total_fare != null && <p>Amount due: <strong>{fmt(confirmedInfo.total_fare, confirmedInfo.currency)}</strong></p>}
              <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Your seat is held on Travelport. Pay now to have Travelport issue the ticket.</p>
            </>
          ) : (
            <>
              <h3 className="section-title">Booking ready — payment required first</h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                This is an Emirates NDC fare: Travelport only creates the PNR at the same moment it issues the ticket, so payment has to be confirmed before either exists.
              </p>
              <p>Amount due: <strong>{fmt(confirmedInfo.amount, confirmedInfo.currency)}</strong></p>
            </>
          )}
          {error && <div className="error-banner">{error}</div>}
          <button className="btn btn-primary" onClick={goToPayment} disabled={paying}>
            {paying ? 'Redirecting to payment…' : 'Issue Ticket'}
          </button>
        </section>
      )}

      {step === 'ticket' && result && (
        <section className="glass-panel" style={{ marginTop: '1rem', padding: '1rem' }}>
          <h3 className="section-title">{result.success ? '✅ Ticket issued' : '⚠️ Payment processed, ticket pending'}</h3>
          <p>PNR <strong>{result.locator_code}</strong>{result.airline_pnr ? ` · Airline ref ${result.airline_pnr}` : ''} · Status {result.status}</p>
          {result.message && <div className="error-banner">{result.message}</div>}
          {result.ticket_numbers.map((t, i) => (
            <div key={i}>{TYPE_LABEL[t.passenger_type] || t.passenger_type}: <strong>{t.number}</strong></div>
          ))}
          {result.total_fare != null && <p>Total {fmt(result.total_fare, result.currency)}</p>}
          {result.success && <button className="btn btn-secondary btn-sm" onClick={verifyTickets}>Verify with Travelport</button>}
          {verify && verify.tickets.map((t, i) => (
            <div key={i} style={{ fontSize: '0.85rem' }}>
              {t.number}: {t.valid ? `valid — ${t.segments.join(', ')}` : `not verified (${t.error})`}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
