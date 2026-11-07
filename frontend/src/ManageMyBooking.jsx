// Manage My Booking (Booking Status page): add special requests (wheelchair,
// meals), choose seats, add extras on an existing booking. Everything is done
// on Travelport's post-commit workbench by the backend
// (/api/manage-booking/*); prices shown are Travelport's live prices with
// our markup, and the amount charged is set server-side, never here.
//
// view: which Booking Status tab is open — 'seats', 'extras' or 'requests'
// (anything else renders nothing). The component stays mounted across tabs
// so chosen seats and extras are kept while the customer moves between them.
import { useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';
import BookingWaitLoader from './BookingWaitLoader.jsx';
import CabinSeatMap, { mapIsOpen } from './CabinSeatMap.jsx';
import { IconSeat, IconBag, IconWheelchair, IconInfo } from './WizardIcons.jsx';

const money = (cur, amt) => `${cur || ''} ${Number(amt || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();
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

const STATUS = {
  confirmed: ['Confirmed by the airline', 'ok'],
  pending: ['Waiting for the airline (not charged)', 'info'],
  sent: ['Sent to the airline (not confirmed yet)', 'info'],
  failed: ['Not added', 'bad'],
};


// Extras laid out like the booking flow's Options step: one block per flight
// (Departure / Return, from Travelport's own segment_numbers), and per
// passenger one drop-down each for Additional baggage, Meal and Lounge.
// Items sold for several flights at once get their own block.
const EXTRA_CATS = [
  { code: 'BG', label: 'Additional baggage' },
  { code: 'ML', label: 'Meal' },
  { code: 'LG', label: 'Lounge' },
];

// Round trip (ends where it began): the return starts at the first flight
// that leaves 12 hours or more after the previous one landed.
export function flightDirections(flights) {
  const list = [...flights].sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
  const roundTrip = list.length > 1 && list[0].from && list[0].from === list[list.length - 1].to;
  let back = false;
  return list.map((f, i) => {
    if (roundTrip && i > 0 && !back) {
      const gap = (new Date((f.departure || '').replace(' ', 'T')) - new Date((list[i - 1].arrival || '').replace(' ', 'T'))) / 3.6e6;
      if (gap >= 12) back = true;
    }
    return { ...f, dir: roundTrip ? (back ? 'Return' : 'Departure') : `Flight ${i + 1}` };
  });
}

function ExtrasByFlight({ extras, flights, travelers, pick, setPick }) {
  const flown = flightDirections(flights);
  const groupOf = (x) => {
    const nums = [...new Set(x.segment_numbers || [])].sort((a, b) => a - b);
    return nums.length === 1 ? `F${nums[0]}` : `M${nums.join(',')}`;
  };
  const titleOf = (g) => {
    const nums = g.slice(1).split(',').filter(Boolean).map(Number);
    const fs = nums.map(n => flown[n - 1]).filter(Boolean);
    if (g.startsWith('F') && fs[0]) return [fs[0].dir, `${fs[0].flight} · ${fs[0].from} → ${fs[0].to}`];
    const dirs = new Set(fs.map(f => f.dir));
    return dirs.size === 1 && fs.length ? [fs[0].dir, 'All flights'] : ['Whole trip', 'Every flight on the booking'];
  };
  const order = (g) => Math.min(...g.slice(1).split(',').filter(Boolean).map(Number), 99) * 10 + (g.startsWith('F') ? 0 : 5);
  const groups = [...new Set(extras.map(groupOf))].sort((a, b) => order(a) - order(b));
  return groups.map(g => {
    // Per flight all three are shown (one the airline doesn't offer is
    // greyed out), as in the booking flow; a multi-flight block only shows
    // what it has.
    const cats = g.startsWith('F') ? EXTRA_CATS
      : EXTRA_CATS.filter(c => extras.some(x => groupOf(x) === g && (x.category_code || '').toUpperCase() === c.code));
    if (!cats.length) return null;
    const [dir, title] = titleOf(g);
    return (
      <div key={g} className="mmb-extra-group">
        <div className="mmb-extra-title"><span className="mmb-dir">{dir}</span><strong>{title}</strong></div>
        {travelers.map(t => {
          const mine = extras.map((x, i) => ({ x, i })).filter(({ x }) => groupOf(x) === g && x.traveler_identifier_ref === t.id);
          if (!mine.length) return null;
          return (
            <div key={t.id} className="mmb-extra-pax">
              <div className="mmb-pax-name">{t.name}</div>
              <div className="mmb-extra-fields">
                {cats.map(c => {
                  const opts = mine.filter(({ x }) => (x.category_code || '').toUpperCase() === c.code).sort((a, b) => (a.x.price || 0) - (b.x.price || 0));
                  const key = `${t.id}|${g}|${c.code}`;
                  const fl = g.startsWith('F') ? flown[Number(g.slice(1)) - 1] : null;
                  return (
                    <label key={c.code} className="mmb-field">
                      <span>{c.code === 'LG' && fl ? `Lounge at ${fl.from}` : c.label}</span>
                      <select className="form-select" value={pick[key] ?? ''} disabled={!opts.length}
                              onChange={e => setPick(p => ({ ...p, [key]: e.target.value }))}>
                        <option value="">{opts.length ? 'No selection' : 'Not offered on this flight'}</option>
                        {opts.map(({ x, i }) => (
                          <option key={i} value={i}>
                            {x.name} — {money(x.currency, x.price)}{x.refundable === true ? ' · Refundable' : x.refundable === false ? ' · Non-refundable' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    );
  });
}

const PanelHead = ({ icon: Icon, title, sub }) => (
  <div className="mmb-card-head">
    <span className="mmb-icon"><Icon size={20} /></span>
    <div><h3>{title}</h3>{sub && <p className="mmb-sub">{sub}</p>}</div>
  </div>
);

export default function ManageMyBooking({ manageToken, apiBase, fetchWithRetry, authToken, onChanged, view }) {
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [ssr, setSsr] = useState({});          // traveler_id -> {wheelchair, meal}
  const [seatPick, setSeatPick] = useState({}); // `${flight}|${traveler}` -> seat
  const [extraPick, setExtraPick] = useState({}); // `${traveler}|${group}|${category}` -> index in options.extras
  const [cabinFor, setCabinFor] = useState(null); // flight number whose cabin map is open
  const [result, setResult] = useState(null);

  const post = async (path, body) => {
    const res = await fetchWithRetry(`${apiBase}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authToken}` },
      body: JSON.stringify({ manage_token: manageToken, ...body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The airline did not answer');
    return data;
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const o = await post('/manage-booking/options', {});
        setOptions(o);
      } catch (e) { setError(e.message); }
      setLoading(false);
    })();
  }, [manageToken]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!['seats', 'extras', 'requests'].includes(view)) return null;
  if (loading) return <BookingWaitLoader icon="seat" title="Loading seats and extras from the airline" subtitle="Live from the airline — this can take a few seconds" />;
  if (error && !options) return <div className="error-banner">{error}</div>;

  const travelers = options.travelers || [];
  const chosenSeats = Object.entries(seatPick).map(([k, s]) => { const [flight, traveler] = k.split('|'); return { flight, traveler, seat: s }; });
  const chosenExtras = Object.values(extraPick).filter(v => v !== '' && v != null).map(v => options.extras[Number(v)]).filter(Boolean);
  const total = chosenSeats.reduce((a, c) => a + (c.seat.price || 0), 0) + chosenExtras.reduce((a, x) => a + (x.price || 0), 0);
  const currency = chosenSeats[0]?.seat.currency || chosenExtras[0]?.currency || 'LKR';
  const addons = view === 'seats' || view === 'extras';
  const ndcNote = options.seats_extras_note;

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

  // Special requests are listed by name (the airline's code alone, "WCHR",
  // means nothing to a customer).
  const ssrLabel = (code) => [...(options.special_requests?.wheelchair || []), ...(options.special_requests?.meals || [])]
    .find(o => o.code === code)?.label || code;

  const renderResult = () => (
    <section className="mmb-card mmb-result">
      <h4 className="mmb-section-title">Answer from the airline</h4>
      {(result.kind === 'ssr' ? result.results : [...(result.seats || []), ...(result.extras || [])]).map((i, n) => {
        const [label, cls] = STATUS[i.status] || [i.status, 'warn'];
        return (
          <div key={n} className="mmb-result-row">
            <div>
              <strong>{i.seat_number ? `Seat ${i.seat_number}` : i.name || ssrLabel(i.code)}</strong>
              {i.flight_number && <span className="mmb-muted"> · {i.flight_number}</span>}
              {i.price ? <span className="mmb-muted"> · {money(i.currency, i.price)}</span> : null}
              {(i.reason || i.error) && i.status !== 'pending' && <div className="mmb-reason">{i.reason || i.error}</div>}
            </div>
            <span className={`mmb-chip ${cls}`}>{label}</span>
          </div>
        );
      })}
      {result.kind === 'ssr' && result.results?.some(r => r.status === 'sent') && !(result.special_services || []).length && (
        <div className="mmb-alert warn" style={{ marginTop: '0.6rem' }}>
          <strong>Not confirmed yet.</strong> The request is on your booking with the airline, but this airline doesn't send its
          confirmation back online for this booking. The airline confirms it at check-in — use <strong>Check again</strong> later to
          see if it has been updated, or contact the airline directly or call 011 222 2222 to make sure.
        </div>
      )}
      {result.kind === 'addons' && [...(result.seats || []), ...(result.extras || [])].some(i => i.status === 'pending') && (
        <div className="mmb-alert info" style={{ marginTop: '0.6rem' }}>
          We've sent your request to the airline for confirmation. Nothing is charged now. As soon as the airline confirms
          or declines, we'll let you know by email and SMS — if it confirms, you can pay here on Booking Status to keep it.
        </div>
      )}
      {result.payment_required && (
        <div className="mmb-pay">
          <span>
            To finish, pay <strong>{money(result.currency, result.amount)}</strong>
            {result.deadline_utc ? <> by <strong>{formatDeadline(result.deadline_utc)}</strong></> : null}. The airline then issues the document for your seat/extra.
            {result.deadline_utc ? ' If it isn’t paid by then it is removed from your booking and can’t be used at the airport.' : ''}
          </span>
          <button className="btn btn-primary" disabled={busy === 'pay'} onClick={payAddons}>Pay {money(result.currency, result.amount)}</button>
        </div>
      )}
      {result.added_to_booking_total ? (
        <div className="mmb-muted" style={{ marginTop: '0.5rem' }}>Added {money(result.currency, result.added_to_booking_total)} to your booking — new total {money(result.currency, result.new_total)}, paid when your ticket is issued.</div>
      ) : null}
    </section>
  );

  const flown = flightDirections(options.flights || []);
  const maps = options.seat_maps || [];
  const seatRows = flown.length ? flown : maps.map(m => ({ flight: m.flight_number, from: m.departure_airport, to: m.arrival_airport, dir: 'Flight' }));
  const picked = chosenSeats.length + chosenExtras.length;

  return (
    <div className="mmb-manage">
      {error && <div className="error-banner">{error}</div>}

      {view === 'requests' && (
        <section className="mmb-card">
          <PanelHead icon={IconWheelchair} title="Special requests" sub="Free of charge. Your request goes straight to the airline — its answer shows below and in Booking overview." />
          <div className="mmb-ssr">
            {travelers.map(t => (
              <div key={t.id} className="mmb-ssr-row">
                <div className="mmb-pax-name">{t.name}</div>
                <label className="mmb-field">
                  <span>Wheelchair</span>
                  <select className="form-select" value={ssr[t.id]?.wheelchair || ''} onChange={e => setSsr(p => ({ ...p, [t.id]: { ...p[t.id], wheelchair: e.target.value } }))}>
                    <option value="">No wheelchair</option>
                    {options.special_requests.wheelchair.map(w => <option key={w.code} value={w.code}>{w.label}</option>)}
                  </select>
                </label>
                {options.special_requests.meals.length > 0 && (
                  <label className="mmb-field">
                    <span>Special meal</span>
                    <select className="form-select" value={ssr[t.id]?.meal || ''} onChange={e => setSsr(p => ({ ...p, [t.id]: { ...p[t.id], meal: e.target.value } }))}>
                      <option value="">No special meal</option>
                      {options.special_requests.meals.map(m => <option key={m.code} value={m.code}>{m.label}</option>)}
                    </select>
                  </label>
                )}
              </div>
            ))}
          </div>
          {options.special_requests.meals_note && <p className="mmb-muted" style={{ marginTop: '0.5rem' }}>{options.special_requests.meals_note}</p>}
          <p className="mmb-note"><IconInfo size={14} /> With a wheelchair request, some airlines (SriLankan, for one) don't allow paid seat selection.</p>
          <div className="mmb-actions">
            <button className="btn btn-primary" disabled={busy === 'ssr' || !Object.values(ssr).some(v => v?.wheelchair || v?.meal)}
                    onClick={sendSpecialRequests}>{busy === 'ssr' ? 'Sending…' : 'Send special requests'}</button>
          </div>
        </section>
      )}
      {view === 'requests' && result?.kind === 'ssr' && renderResult()}

      {addons && ndcNote && <div className="mmb-alert warn">{ndcNote}</div>}
      {addons && !ndcNote && options.paid_addons_note && <div className="mmb-alert info">{options.paid_addons_note}</div>}

      {view === 'seats' && !ndcNote && (
        <section className="mmb-card">
          <PanelHead icon={IconSeat} title="Choose your seats"
                     sub="Pick a seat on the aircraft cabin map. Free seats cost nothing; a paid seat is charged only once the airline confirms it. A seat already on the booking can't be swapped online yet." />
          {options.seat_map_error && <div className="mmb-alert warn">Seat map not available: {options.seat_map_error}</div>}
          <div className="mmb-seat-flights">
            {seatRows.map(f => {
              const m = maps.find(x => x.flight_number === f.flight);
              const ok = mapIsOpen(m);
              const mine = travelers.map(t => ({ t, s: seatPick[`${f.flight}|${t.id}`] })).filter(x => x.s);
              return (
                <div key={f.flight} className={`mmb-seat-flight${ok ? '' : ' closed'}`}>
                  <div style={{ minWidth: 0 }}>
                    <span className="mmb-dir">{f.dir}</span>
                    <div className="mmb-seat-route">{f.flight} · {f.from} → {f.to}</div>
                    <div className={ok ? 'mmb-ok' : 'mmb-no'}>
                      {ok ? '✓ Seat selection available' : m?.seat_condition?.closed ? `✕ Seat selection has closed (the airline closes it ${m.seat_condition.hours} hours before departure)` : '✕ Seat selection not available online for this flight'}
                    </div>
                    {mine.length > 0 && (
                      <div className="mmb-chips">
                        {mine.map(({ t, s: seat }) => (
                          <span key={t.id} className="mmb-pick">{t.name}: {seat.seat_number} · {seat.price ? money(seat.currency, seat.price) : 'Free'}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <button type="button" className={ok ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'} disabled={!ok} onClick={() => setCabinFor(f.flight)}>
                    {ok ? (mine.length ? 'Change seat' : 'Choose seat') : 'Not available'}
                  </button>
                </div>
              );
            })}
          </div>
          {cabinFor && (
            <CabinSeatMap maps={maps} travelers={travelers} startFlight={cabinFor}
                          labelOf={(fn) => flown.find(x => x.flight === fn)?.dir || 'Flight'}
                          picks={seatPick} setPicks={setSeatPick} onClose={() => setCabinFor(null)} />
          )}
        </section>
      )}

      {view === 'extras' && !ndcNote && (
        <section className="mmb-card">
          <PanelHead icon={IconBag} title="Add extras" sub="Extra baggage, meals and lounges for each flight. You pay only once the airline confirms them." />
          {options.extras_error && <div className="mmb-alert info">{options.extras_error}</div>}
          {(options.extras || []).length === 0 && !options.extras_error && <div className="mmb-muted">The airline offers no extras for this booking.</div>}
          {(options.extras || []).length > 0 && (
            <ExtrasByFlight extras={options.extras} flights={options.flights || []} travelers={travelers}
                            pick={extraPick} setPick={setExtraPick} />
          )}
        </section>
      )}

      {addons && !ndcNote && (
        <>
          <div className="mmb-bar">
            <div>
              <small>{chosenSeats.length} seat{chosenSeats.length === 1 ? '' : 's'} · {chosenExtras.length} extra{chosenExtras.length === 1 ? '' : 's'} selected</small>
              <div className="mmb-total">{money(currency, total)}</div>
              <small>The airline's live price at booking is what's charged; nothing is charged if it can't confirm.</small>
            </div>
            <button className="btn mmb-bar-btn" disabled={busy === 'addons' || !picked} onClick={sendSeatsExtras}>
              {busy === 'addons' ? 'Booking with the airline…' : 'Add to my booking'}
            </button>
          </div>
          <ol className="mmb-steps">
            <li className="done"><b>1</b><div><strong>Choose seats &amp; extras</strong>Across both tabs</div></li>
            <li><b>2</b><div><strong>The airline confirms</strong>Free items are added at once</div></li>
            <li><b>3</b><div><strong>Pay for paid items</strong>Only what the airline confirmed</div></li>
          </ol>
        </>
      )}

      {busy === 'addons' && (
        <BookingWaitLoader icon="seat" title="Sending your choices to the airline" subtitle="Waiting for the airline's answer — please don't close this page" />
      )}
      {addons && result && result.kind !== 'ssr' && renderResult()}
    </div>
  );
}
