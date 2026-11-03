// Manage My Booking (Booking Status page): add special requests (wheelchair,
// meals), choose seats, add extras on an existing booking. Everything is done
// on Travelport's post-commit workbench by the backend
// (/api/manage-booking/*); prices shown are Travelport's live prices with
// our markup, and the amount charged is set server-side, never here.
import { useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';
import BookingWaitLoader from './BookingWaitLoader.jsx';

const money = (cur, amt) => `${cur || ''} ${Number(amt || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();
const box = { border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 0.9rem', marginTop: '0.75rem', background: 'white' };
const h5 = { margin: '0 0 0.5rem', fontSize: '0.88rem' };
const muted = { color: 'var(--text-muted)', fontSize: '0.74rem' };
// Card payment for an add-on on PayCorp's hosted page. The backend takes the
// amount from the add-on record (never this page's figure); on return
// CustomerPortal completes it and the airline issues the EMD.
export async function startAddonPayment({ apiBase, fetchWithRetry, addon }) {
  const base = `${window.location.origin}/account`;
  const res = await fetchWithRetry(`${apiBase}/payments/init`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ amount: addon.amount, currency: addon.currency, client_ref: addon.addon_id,
                           return_url: `${base}?addon=${addon.addon_id}`, cancel_url: `${base}?addon=${addon.addon_id}&payment=cancelled`,
                           comment: `Tick and Book - seats/extras ${addon.addon_id}` }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not start the payment');
  window.location.href = data.payment_page_url;
}

// A payment deadline from the backend (UTC ISO) in the customer's own time.
export function formatDeadline(iso) {
  if (!iso) return '';
  return fmtDateTime(iso);
}

const STATUS_TEXT = { confirmed: 'Confirmed by the airline', pending: 'Sent to the airline — waiting for its confirmation (not charged)', sent: 'Sent to the airline', failed: 'Not Added' };

function SeatMap({ map, chosen, onPick }) {
  // An aisle sits between two neighbouring aisle-seat columns (C|D, G|H).
  const cols = map.columns || [];
  const aisleAfter = new Set(cols.filter((c, i) => c.position === 'Aisle' && cols[i + 1]?.position === 'Aisle').map(c => c.value));
  return (
    <div style={{ overflowX: 'auto', paddingBottom: '0.3rem' }}>
      <div style={{ display: 'inline-flex', flexDirection: 'column', gap: '3px' }}>
        {map.rows.map(row => (
          <div key={row.row_number} style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
            <span style={{ width: '22px', fontSize: '0.62rem', color: 'var(--text-muted)', textAlign: 'right' }}>{row.row_number}</span>
            {row.seats.map((s, i) => {
              const gap = aisleAfter.has(s.col) ? { marginRight: '12px' } : {};
              if (s.type === 'empty' || s.status === 'empty') return <span key={i} style={{ width: '24px', height: '24px', ...gap }} />;
              const open = s.status === 'available';
              const picked = chosen === s.seat_number;
              return (
                <button key={i} type="button" disabled={!open} onClick={() => onPick(s)}
                        title={open ? `${s.seat_number} · ${s.price ? money(s.currency, s.price) : 'Free'}` : `${s.seat_number} · not available`}
                        style={{ ...gap, width: '24px', height: '24px', fontSize: '0.5rem', padding: 0, borderRadius: '4px', cursor: open ? 'pointer' : 'not-allowed',
                                 border: picked ? '2px solid var(--gs-crimson, #c8102e)' : '1px solid #cbd5e1',
                                 background: picked ? 'var(--gs-crimson, #c8102e)' : !open ? '#e2e8f0' : s.price ? '#fef3c7' : '#dcfce7',
                                 color: picked ? 'white' : '#334155' }}>
                  {s.col}
                </button>
              );
            })}
            {row.exit_row && <span style={{ fontSize: '0.55rem', color: '#b45309', marginLeft: '2px' }}>EXIT</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ManageMyBooking({ manageToken, apiBase, fetchWithRetry, authToken, onChanged }) {
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [ssr, setSsr] = useState({});          // traveler_id -> {wheelchair, meal}
  const [seatPick, setSeatPick] = useState({}); // `${flight}|${traveler}` -> seat
  const [extraPick, setExtraPick] = useState({}); // index -> true
  const [mapFlight, setMapFlight] = useState(null);
  const [mapTraveler, setMapTraveler] = useState(null);
  const [result, setResult] = useState(null);

  const post = async (path, body) => {
    const res = await fetchWithRetry(`${apiBase}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` },
      body: JSON.stringify({ manage_token: manageToken, ...body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Travelport did not answer');
    return data;
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const o = await post('/manage-booking/options', {});
        setOptions(o);
        setMapFlight(o.seat_maps?.[0]?.flight_number || null);
        setMapTraveler(o.travelers?.[0]?.id || null);
      } catch (e) { setError(e.message); }
      setLoading(false);
    })();
  }, [manageToken]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <BookingWaitLoader icon="seat" title="Loading seats and extras from the airline" subtitle="Live from the airline — this can take a few seconds" />;
  if (error && !options) return <div className="error-banner">{error}</div>;

  const travelers = options.travelers || [];
  const nameOf = (id) => travelers.find(t => t.id === id)?.name || '';
  const map = (options.seat_maps || []).find(m => m.flight_number === mapFlight);
  const chosenSeats = Object.entries(seatPick).map(([k, s]) => { const [flight, traveler] = k.split('|'); return { flight, traveler, seat: s }; });
  const chosenExtras = Object.keys(extraPick).filter(k => extraPick[k]).map(k => options.extras[Number(k)]);
  const total = chosenSeats.reduce((a, c) => a + (c.seat.price || 0), 0) + chosenExtras.reduce((a, x) => a + (x.price || 0), 0);
  const currency = chosenSeats[0]?.seat.currency || chosenExtras[0]?.currency || 'LKR';

  const sendSpecialRequests = async () => {
    const requests = Object.entries(ssr).flatMap(([traveler_id, v]) =>
      [v.wheelchair && { traveler_id, code: v.wheelchair }, v.meal && { traveler_id, code: v.meal }].filter(Boolean));
    if (!requests.length) return;
    setBusy('ssr'); setError(''); setResult(null);
    try {
      const r = await post('/manage-booking/special-requests', { requests });
      setResult({ kind: 'ssr', ...r });
      setSsr({});
      onChanged?.();
    } catch (e) { setError(e.message); }
    setBusy('');
  };

  const sendSeatsExtras = async () => {
    setBusy('addons'); setError(''); setResult(null);
    try {
      const r = await post('/manage-booking/seats-extras', {
        seats: chosenSeats.map(c => ({ traveler_id: c.traveler, flight_number: c.flight, seat_number: c.seat.seat_number })),
        extras: chosenExtras.map(x => ({ traveler_id: x.traveler_identifier_ref, airline_name: x.airline_name, segment_numbers: x.segment_numbers, name: x.name })),
      });
      setResult({ kind: 'addons', ...r });
      setSeatPick({}); setExtraPick({});
      onChanged?.();
    } catch (e) { setError(e.message); }
    setBusy('');
  };

  // Paid add-ons on a ticketed booking: card payment, then EMD issued.
  const payAddons = async () => {
    setBusy('pay'); setError('');
    try {
      await startAddonPayment({ apiBase, fetchWithRetry, addon: result });
    } catch (e) { setError(e.message); setBusy(''); }
  };

  return (
    <div>
      {error && <div className="error-banner" style={{ marginTop: '0.5rem' }}>{error}</div>}

      {busy === 'addons' && (
        <BookingWaitLoader icon="seat" title="Sending your choices to the airline" subtitle="Waiting for the airline's answer — please don't close this page" />
      )}

      {result && (
        <div style={{ ...box, background: '#f8fafc' }}>
          <h5 style={h5}>Result from the airline</h5>
          {(result.kind === 'ssr' ? result.results : [...(result.seats || []), ...(result.extras || [])]).map((i, n) => (
            <div key={n} style={{ fontSize: '0.8rem', padding: '0.2rem 0' }}>
              <strong>{i.seat_number ? `Seat ${i.seat_number}` : i.name || i.code}</strong>{i.price ? ` · ${money(i.currency, i.price)}` : ''} — {STATUS_TEXT[i.status] || i.status}
              {(i.reason || i.error) && i.status !== 'pending' ? (
                i.status === 'failed'
                  ? <div style={{ fontSize: '0.78rem', color: '#475569', marginTop: '0.15rem' }}>{i.reason || i.error}</div>
                  : <span style={muted}> ({i.reason || i.error})</span>
              ) : null}
            </div>
          ))}
          {result.kind === 'ssr' && result.results?.some(r => r.status === 'sent') && !(result.special_services || []).length && (
            <div style={muted}>The airline accepted the request but doesn't send a status back for this booking — it will show at check-in.</div>
          )}
          {result.kind === 'addons' && [...(result.seats || []), ...(result.extras || [])].some(i => i.status === 'pending') && (
            <div style={{ marginTop: '0.5rem', padding: '0.55rem 0.7rem', borderRadius: '6px', background: '#eff6ff', border: '1px solid #bfdbfe', fontSize: '0.8rem', color: '#1e3a8a' }}>
              We've sent your request to the airline for confirmation. Nothing is charged now. As soon as the airline confirms
              or declines, we'll let you know by email and SMS — if it confirms, you can pay here on Booking Status to keep it.
            </div>
          )}
          {result.payment_required && (
            <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.85rem' }}>
                To finish, pay <strong>{money(result.currency, result.amount)}</strong>
                {result.deadline_utc ? <> by <strong>{formatDeadline(result.deadline_utc)}</strong></> : null}. The airline then issues the document for your seat/extra.
                {result.deadline_utc ? ' If it isn’t paid by then it is removed from your booking and can’t be used at the airport.' : ''}
              </span>
              <button className="btn btn-primary btn-sm" disabled={busy === 'pay'} onClick={payAddons}>Pay {money(result.currency, result.amount)}</button>
            </div>
          )}
          {result.added_to_booking_total ? (
            <div style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>Added {money(result.currency, result.added_to_booking_total)} to your booking — new total {money(result.currency, result.new_total)}, paid when your ticket is issued.</div>
          ) : null}
        </div>
      )}

      <div style={box}>
        <h5 style={h5}>Special requests</h5>
        <div style={muted}>Free. The airline confirms them — the status shows above once it answers.</div>
        {travelers.map(t => (
          <div key={t.id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.5rem', fontSize: '0.8rem' }}>
            <strong style={{ minWidth: '140px' }}>{t.name}</strong>
            <select className="form-input" style={{ maxWidth: '300px', padding: '0.3rem 0.5rem', fontSize: '0.78rem' }}
                    value={ssr[t.id]?.wheelchair || ''} onChange={e => setSsr(p => ({ ...p, [t.id]: { ...p[t.id], wheelchair: e.target.value } }))}>
              <option value="">No wheelchair</option>
              {options.special_requests.wheelchair.map(w => <option key={w.code} value={w.code}>{w.label}</option>)}
            </select>
            {options.special_requests.meals.length > 0 && (
              <select className="form-input" style={{ maxWidth: '220px', padding: '0.3rem 0.5rem', fontSize: '0.78rem' }}
                      value={ssr[t.id]?.meal || ''} onChange={e => setSsr(p => ({ ...p, [t.id]: { ...p[t.id], meal: e.target.value } }))}>
                <option value="">No special meal</option>
                {options.special_requests.meals.map(m => <option key={m.code} value={m.code}>{m.label}</option>)}
              </select>
            )}
          </div>
        ))}
        {options.special_requests.meals_note && <div style={{ ...muted, marginTop: '0.4rem' }}>{options.special_requests.meals_note}</div>}
        <div style={{ ...muted, marginTop: '0.4rem' }}>Note: with a wheelchair request, some airlines (SriLankan, for one) don't allow paid seat selection.</div>
        <button className="btn btn-primary btn-sm" style={{ marginTop: '0.6rem' }} disabled={busy === 'ssr' || !Object.values(ssr).some(v => v?.wheelchair || v?.meal)}
                onClick={sendSpecialRequests}>{busy === 'ssr' ? 'Sending…' : 'Send special requests'}</button>
      </div>

      {options.seats_extras_note ? (
        <div style={{ ...box, background: '#fffbeb', borderColor: '#fde68a', fontSize: '0.82rem', color: '#78350f' }}>{options.seats_extras_note}</div>
      ) : (
        <>
          <div style={box}>
            <h5 style={h5}>Seats</h5>
            {options.seat_map_error && <div style={muted}>Seat map not available: {options.seat_map_error}</div>}
            {(options.seat_maps || []).length > 0 && (
              <>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
                  <select className="form-input" style={{ maxWidth: '200px', padding: '0.3rem 0.5rem', fontSize: '0.78rem' }} value={mapFlight || ''} onChange={e => setMapFlight(e.target.value)}>
                    {options.seat_maps.map(m => <option key={m.flight_number} value={m.flight_number}>{m.flight_number} {m.departure_airport} → {m.arrival_airport}</option>)}
                  </select>
                  <select className="form-input" style={{ maxWidth: '220px', padding: '0.3rem 0.5rem', fontSize: '0.78rem' }} value={mapTraveler || ''} onChange={e => setMapTraveler(e.target.value)}>
                    {travelers.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </div>
                {map?.seat_condition?.text && <div style={{ ...muted, marginBottom: '0.4rem' }}>{map.seat_condition.text}</div>}
                <div style={{ ...muted, marginBottom: '0.4rem' }}>
                  <span style={{ background: '#dcfce7', padding: '0 0.3rem', borderRadius: '3px' }}>Free</span>{' '}
                  <span style={{ background: '#fef3c7', padding: '0 0.3rem', borderRadius: '3px' }}>Paid</span>{' '}
                  <span style={{ background: '#e2e8f0', padding: '0 0.3rem', borderRadius: '3px' }}>Taken</span> · hover a seat for its price.
                  A seat already on the booking can't be swapped online yet.
                </div>
                {map && <SeatMap map={map} chosen={seatPick[`${mapFlight}|${mapTraveler}`]?.seat_number}
                                 onPick={s => setSeatPick(p => ({ ...p, [`${mapFlight}|${mapTraveler}`]: s }))} />}
                {chosenSeats.map(c => (
                  <div key={`${c.flight}|${c.traveler}`} style={{ fontSize: '0.78rem', marginTop: '0.3rem' }}>
                    {c.flight} · {nameOf(c.traveler)} · <strong>Seat {c.seat.seat_number}</strong> · {c.seat.price ? money(c.seat.currency, c.seat.price) : 'Free'}{' '}
                    <button type="button" className="btn btn-secondary btn-sm" style={{ padding: '0 0.4rem', fontSize: '0.68rem' }}
                            onClick={() => setSeatPick(p => { const n = { ...p }; delete n[`${c.flight}|${c.traveler}`]; return n; })}>Remove</button>
                  </div>
                ))}
              </>
            )}
          </div>

          <div style={box}>
            <h5 style={h5}>Extras</h5>
            {options.extras_error && <div style={{ fontSize: '0.8rem', color: '#475569' }}>{options.extras_error}</div>}
            {(options.extras || []).length === 0 && !options.extras_error && <div style={muted}>The airline offers no extras for this booking.</div>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '0.3rem' }}>
              {(options.extras || []).map((x, i) => (
                <label key={i} style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', fontSize: '0.78rem', padding: '0.3rem 0.4rem', border: '1px solid var(--border-color)', borderRadius: '6px', cursor: 'pointer' }}>
                  <input type="checkbox" checked={!!extraPick[i]} onChange={e => setExtraPick(p => ({ ...p, [i]: e.target.checked }))} />
                  <span style={{ flex: 1 }}>{x.name}<br /><span style={muted}>{x.passenger}</span></span>
                  <strong style={{ whiteSpace: 'nowrap' }}>{money(x.currency, x.price)}</strong>
                </label>
              ))}
            </div>
          </div>

          <div style={{ ...box, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.85rem' }}>
              {chosenSeats.length + chosenExtras.length} selected · <strong>{money(currency, total)}</strong>
              <span style={muted}> — the airline's live price at booking is what's charged; nothing is charged if it can't confirm.</span>
            </span>
            <button className="btn btn-primary btn-sm" disabled={busy === 'addons' || !(chosenSeats.length + chosenExtras.length)} onClick={sendSeatsExtras}>
              {busy === 'addons' ? 'Booking with the airline…' : 'Add to my booking'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
