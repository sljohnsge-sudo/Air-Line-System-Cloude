// The booking flow's cabin seat map (bird's-eye aircraft: nose, rows by seat
// type, tail), used in Manage my booking now that seats are chosen after
// ticketing. Seat data is Travelport's live seat map for the booking
// (/api/manage-booking/options → seat_maps, parsed by the same
// workbench_service.parse_seat_map_response as the booking flow), so the
// shape is the same: { flight_number, departure_airport, arrival_airport,
// columns: [{value, position}], rows: [{row_number, exit_row, seats: [
// {seat_number, col, status, type, price, currency}]}], seat_condition }.
//
// picks: { `${flight_number}|${traveler_id}`: seat } — one seat per passenger
// per flight; nothing is booked here, the parent sends the choices.
import React, { useState } from 'react';
import { createPortal } from 'react-dom';

const money = (cur, amt) => `${cur || ''} ${Number(amt || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim();

// Blocks of columns between aisles, from the airline's own layout (two
// neighbouring "Aisle" columns have an aisle between them); a single central
// aisle only when the data has no aisle tags.
export function seatColumnGroups(cols) {
  const groups = [[]];
  cols.forEach((c, i) => {
    groups[groups.length - 1].push(c);
    if (c.position === 'Aisle' && cols[i + 1]?.position === 'Aisle') groups.push([]);
  });
  if (groups.length === 1 && cols.length > 1) {
    const half = Math.ceil(cols.length / 2);
    return [cols.slice(0, half), cols.slice(half)];
  }
  return groups;
}

// A map has something to choose when any seat is Available.
export const mapIsOpen = (map) => (map?.rows || []).some(r => (r.seats || []).some(s => s?.status === 'available'));

export default function CabinSeatMap({ maps, travelers, labelOf, picks, setPicks, startFlight, onClose }) {
  const open = maps.filter(mapIsOpen);
  const [flight, setFlight] = useState(startFlight && open.some(m => m.flight_number === startFlight) ? startFlight : open[0]?.flight_number);
  const [activePax, setActivePax] = useState(travelers[0]?.id);
  const map = maps.find(m => m.flight_number === flight);
  const key = (f, t) => `${f}|${t}`;
  const cols = map?.columns || [];
  const groups = seatColumnGroups(cols);
  const fuselageWidth = `${Math.max(290, (cols.length + Math.max(groups.length - 1, 0)) * 36 + 44)}px`;
  const seatCharges = Object.values(picks).reduce((t, s) => t + (s?.price || 0), 0);
  const currency = Object.values(picks).find(s => s?.currency)?.currency || 'LKR';

  const pick = (seat) => {
    const mine = picks[key(flight, activePax)];
    const takenBy = Object.entries(picks).find(([k, s]) => k.startsWith(`${flight}|`) && s.seat_number === seat.seat_number && k !== key(flight, activePax));
    if (takenBy) return;
    setPicks(p => {
      const n = { ...p };
      if (mine?.seat_number === seat.seat_number) delete n[key(flight, activePax)];
      else n[key(flight, activePax)] = seat;
      return n;
    });
    // Next passenger without a seat on this flight.
    if (mine?.seat_number !== seat.seat_number) {
      const next = travelers.find(t => t.id !== activePax && !picks[key(flight, t.id)]);
      if (next) setTimeout(() => setActivePax(next.id), 250);
    }
  };

  const renderSeat = (seat, idx) => {
    if (!seat || seat.type === 'empty' || seat.status === 'empty') return <div key={idx} style={{ width: '32px', height: '32px' }} />;
    const available = seat.status === 'available';
    const pos = cols.find(c => c.value === seat.col)?.position;
    const posLabel = pos === 'Window' ? 'Window Seat' : pos === 'Aisle' ? 'Aisle Seat' : pos === 'Center' ? 'Middle Seat' : null;
    const posLetter = pos === 'Window' ? 'W' : pos === 'Aisle' ? 'A' : pos === 'Center' ? 'M' : null;
    const holder = Object.entries(picks).find(([k, s]) => k.startsWith(`${flight}|`) && s.seat_number === seat.seat_number);
    const mine = holder && holder[0] === key(flight, activePax);
    const others = holder && !mine;
    let bg = 'white', border = '1px solid #cbd5e1', color = 'var(--text-secondary)', cursor = 'pointer', pattern = {};
    if (!available) {
      bg = '#cbd5e1'; border = '1px solid #94a3b8'; color = '#94a3b8'; cursor = 'not-allowed';
      pattern = { backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 3px, rgba(100,116,139,0.35) 3px, rgba(100,116,139,0.35) 5px)' };
    } else if (mine) {
      bg = 'var(--gs-crimson, #c8102e)'; border = '1px solid var(--gs-crimson, #c8102e)'; color = 'white';
    } else if (others) {
      bg = '#fee2e2'; border = '1px dashed var(--gs-crimson, #c8102e)'; color = 'var(--gs-crimson, #c8102e)'; cursor = 'not-allowed';
    } else if ((seat.price || 0) > 0) {
      bg = '#fef3c7'; border = '1px solid #f59e0b'; color = '#92400e';
    }
    return (
      <button key={seat.seat_number || idx} type="button" disabled={!available} onClick={() => pick(seat)}
              title={`${seat.seat_number} - ${seat.type || 'Seat'}${posLabel ? ` - ${posLabel}` : ''} (${seat.price ? money(seat.currency, seat.price) : 'Free'})${others ? ' — chosen for another passenger' : ''}`}
              style={{ width: '32px', height: '32px', borderRadius: '4px', background: bg, border, color, fontSize: '0.65rem', fontWeight: 700,
                       display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', lineHeight: 1, cursor, outline: 'none',
                       boxShadow: mine ? '0 0 8px rgba(195, 18, 46, 0.4)' : 'none', transition: 'all 0.15s', padding: 0, ...pattern }}>
        {!available ? '×' : (<><span>{seat.seat_number?.slice(-1)}</span>{posLetter && <span style={{ fontSize: '0.42rem', fontWeight: 600, opacity: 0.7, marginTop: '1px' }}>{posLetter}</span>}</>)}
      </button>
    );
  };

  // Section headers where the seat type changes ("Preferred (+ LKR 5,000)").
  const rowMeta = (map?.rows || []).map(r => {
    const s = (r.seats || []).filter(x => x && x.type && x.type !== 'empty');
    if (!s.length) return null;
    const counts = {};
    s.forEach(x => { counts[x.type] = (counts[x.type] || 0) + 1; });
    const type = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
    const p = s.find(x => x.type === type) || s[0];
    return { type, price: p.price || 0, currency: p.currency || 'LKR' };
  });
  const headerAt = (i) => {
    const cur = rowMeta[i];
    if (!cur) return false;
    for (let k = i - 1; k >= 0; k--) if (rowMeta[k]) return rowMeta[k].type !== cur.type;
    return true;
  };

  const activeSeat = picks[key(flight, activePax)];

  return createPortal(
    <div className="cabin-map-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cabin-map-modal" role="dialog" aria-modal="true" aria-label="Choose seats">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', marginBottom: '0.75rem' }}>
          <h3 style={{ margin: 0, fontSize: '1.15rem' }}>Select Passenger Seats</h3>
          <button type="button" className="btn btn-primary btn-sm" onClick={onClose}>Done</button>
        </div>

        {/* Flight tabs (one per flight with a seat map) */}
        {maps.length > 1 && (
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.9rem', flexWrap: 'wrap' }}>
            {maps.map(m => {
              const ok = mapIsOpen(m);
              const active = ok && m.flight_number === flight;
              const seated = travelers.filter(t => picks[key(m.flight_number, t.id)]).length;
              return (
                <button key={m.flight_number} type="button" disabled={!ok} onClick={() => ok && setFlight(m.flight_number)}
                        style={{ flex: '1 1 0', minWidth: '110px', padding: '0.55rem 0.75rem', borderRadius: '8px', cursor: ok ? 'pointer' : 'not-allowed',
                                 border: '1.5px solid ' + (active ? 'var(--gs-crimson, #c8102e)' : 'var(--border-color)'),
                                 background: active ? 'var(--gs-crimson, #c8102e)' : ok ? '#fff' : '#f1f5f9',
                                 color: active ? '#fff' : ok ? 'var(--text-secondary)' : '#94a3b8',
                                 display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.15rem' }}>
                  <span style={{ fontWeight: 800, fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{labelOf(m.flight_number)}</span>
                  <span style={{ fontSize: '0.72rem', opacity: 0.85, fontWeight: 600 }}>{m.flight_number} · {m.departure_airport} → {m.arrival_airport}</span>
                  <span style={{ fontSize: '0.68rem', opacity: 0.85 }}>{ok ? `${seated}/${travelers.length} seated` : 'Seat selection not available'}</span>
                </button>
              );
            })}
          </div>
        )}
        {map?.seat_condition?.text && <div className="info-banner" style={{ fontSize: '0.78rem', marginBottom: '0.6rem' }}>{map.seat_condition.text}</div>}

        <div className="seat-selector-container cabin-map-body">
          {/* Left: passengers, fare guide, legend, cost */}
          <div className="seat-selector-sidebar cabin-map-sidebar">
            <div>
              <h4 style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--gs-dark)', margin: '0 0 0.5rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Passengers</h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {travelers.map((t, i) => {
                  const s = picks[key(flight, t.id)];
                  const active = activePax === t.id;
                  return (
                    <div key={t.id} onClick={() => setActivePax(t.id)}
                         style={{ padding: '0.6rem 0.8rem', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.15s',
                                  border: '1px solid ' + (active ? 'var(--gs-crimson, #c8102e)' : 'var(--border-color)'),
                                  background: active ? 'var(--gs-crimson, #c8102e)' : '#f8fafc', color: active ? 'white' : 'var(--text-secondary)' }}>
                      <div style={{ fontSize: '0.8rem', fontWeight: 700 }}>Passenger {i + 1}: {t.name}</div>
                      <div style={{ fontSize: '0.75rem', opacity: active ? 0.9 : 0.75 }}>
                        {flight}: <strong style={{ color: active ? 'white' : 'var(--gs-crimson, #c8102e)' }}>{s ? `${s.seat_number} (${s.type || 'Seat'})` : 'Not Selected'}</strong>
                      </div>
                      {s?.price > 0 && <div style={{ fontSize: '0.7rem', textAlign: 'right', opacity: 0.85 }}>+ {money(s.currency, s.price)}</div>}
                    </div>
                  );
                })}
              </div>
            </div>

            {activeSeat ? (
              <div style={{ background: activeSeat.price ? '#fffbeb' : '#f0fdf4', border: '1px solid ' + (activeSeat.price ? '#fde68a' : '#bbf7d0'),
                            borderRadius: '6px', padding: '0.7rem', fontSize: '0.75rem', color: activeSeat.price ? '#b45309' : '#15803d', fontWeight: 600, lineHeight: 1.35 }}>
                <div style={{ fontWeight: 800, marginBottom: '0.2rem' }}>{activeSeat.price ? '⚠️ Additional Charge' : '✓ No extra charge'}</div>
                {activeSeat.price
                  ? `Seat ${activeSeat.seat_number} (${activeSeat.type || 'Seat'}) costs ${money(activeSeat.currency, activeSeat.price)}. You pay only once the airline confirms it.`
                  : `Seat ${activeSeat.seat_number} (${activeSeat.type || 'Seat'}) is free with your fare.`}
              </div>
            ) : (
              <div style={{ background: '#f8fafc', border: '1px dashed #cbd5e1', borderRadius: '6px', padding: '0.7rem', fontSize: '0.75rem', color: '#64748b', textAlign: 'center', fontWeight: 600 }}>
                Select a seat on the cabin map for {travelers.find(t => t.id === activePax)?.name || 'this passenger'}.
              </div>
            )}

            <div style={{ background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '0.7rem', fontSize: '0.75rem' }}>
              <h5 style={{ fontWeight: 700, margin: '0 0 0.5rem', color: 'var(--gs-dark)' }}>Legend</h5>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.45rem' }}>
                {[['white', '#cbd5e1', 'Free'], ['#fef3c7', '#f59e0b', 'Paid'], ['var(--gs-crimson, #c8102e)', 'var(--gs-crimson, #c8102e)', 'Selected'], ['#cbd5e1', '#94a3b8', 'Occupied']].map(([bg, bd, t]) => (
                  <div key={t} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                    <div style={{ width: '14px', height: '14px', borderRadius: '3px', border: `1px solid ${bd}`, background: bg }} />
                    <span>{t}</span>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px dashed var(--border-color)', color: 'var(--text-muted)', fontSize: '0.7rem' }}>
                Small letter under the seat — <strong>W</strong> Window, <strong>M</strong> Middle, <strong>A</strong> Aisle.
              </div>
            </div>

            <div style={{ marginTop: 'auto', background: 'var(--tb-ice, #EAF4FC)', border: '1px solid var(--gs-crimson, #c8102e)', borderRadius: '6px', padding: '0.7rem', fontSize: '0.8rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Seat charges (all flights):</span>
                <strong>{money(currency, seatCharges)}</strong>
              </div>
              <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '0.25rem' }}>Charged only for seats the airline confirms.</div>
            </div>
          </div>

          {/* Right: the aircraft */}
          <div className="seat-map-panel cabin-map-panel">
            <div style={{ textAlign: 'center', padding: '0.25rem 0 0.5rem', borderBottom: '1px solid var(--border-color)', marginBottom: '0.75rem' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Aircraft Cabin Map · {flight}</div>
              <strong style={{ fontSize: '0.85rem', color: 'var(--gs-dark)' }}>FRONT OF AIRCRAFT</strong>
            </div>
            <div className="seat-map-fuselage-scroll" style={{ flex: 1, overflow: 'auto', padding: '0.5rem 1rem' }}>
              <div style={{ width: fuselageWidth, height: '75px', border: '2px solid #cbd5e1', borderBottom: 'none', borderTopLeftRadius: '150px', borderTopRightRadius: '150px',
                            background: '#f1f5f9', position: 'relative', margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ display: 'flex', gap: '8px', position: 'absolute', bottom: '15px' }}>
                  <div style={{ width: '35px', height: '14px', background: '#1e293b', borderTopLeftRadius: '10px', borderBottomLeftRadius: '2px', transform: 'skewY(-15deg)', border: '1px solid #64748b' }} />
                  <div style={{ width: '35px', height: '14px', background: '#1e293b', borderTopRightRadius: '10px', borderBottomRightRadius: '2px', transform: 'skewY(15deg)', border: '1px solid #64748b' }} />
                </div>
              </div>
              <div style={{ width: fuselageWidth, borderLeft: '2px solid #cbd5e1', borderRight: '2px solid #cbd5e1', background: '#f8fafc', display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '0 auto', padding: '1rem 0' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', width: 'fit-content' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', marginBottom: '0.5rem', fontWeight: 700, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {groups.map((g, gi) => (
                      <React.Fragment key={gi}>
                        {gi > 0 && <div style={{ width: '32px' }} />}
                        <div style={{ display: 'flex', gap: '0.25rem' }}>{g.map(c => <div key={c.value} style={{ width: '32px', textAlign: 'center' }}>{c.value}</div>)}</div>
                      </React.Fragment>
                    ))}
                  </div>
                  {(map?.rows || []).map((row, ri) => {
                    const seatGroups = groups.map(g => g.map(c => (row.seats || []).find(s => s.col === c.value) || null));
                    const meta = rowMeta[ri];
                    return (
                      <div key={row.row_number || ri} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%' }}>
                        {headerAt(ri) && (
                          <div style={{ width: '100%', margin: '0.85rem 0 0.5rem', position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                            <div style={{ position: 'absolute', left: 0, right: 0, height: '1px', background: '#cbd5e1' }} />
                            <span style={{ position: 'relative', padding: '0.2rem 0.65rem', borderRadius: '20px', fontSize: '0.62rem', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap',
                                           color: meta.price ? '#d97706' : '#16a34a', border: '1px solid ' + (meta.price ? '#fef3c7' : '#bbf7d0'), background: meta.price ? '#fffbeb' : '#f0fdf4' }}>
                              ✈ {meta.type} {meta.price ? `(+ ${meta.currency} ${meta.price.toLocaleString()})` : '(Free)'}
                            </span>
                          </div>
                        )}
                        {row.exit_row && (
                          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '0.6rem', color: '#b45309', background: '#fef3c7', padding: '0.15rem 0.5rem', borderRadius: '4px', margin: '0.25rem 0', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', boxSizing: 'border-box' }}>
                            <span>⟨ Exit Row</span><span>Exit Row ⟩</span>
                          </div>
                        )}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', margin: '0.15rem 0' }}>
                          <div style={{ display: 'flex', gap: '0.25rem' }}>{(seatGroups[0] || []).map((s, i) => renderSeat(s, i))}</div>
                          {seatGroups.slice(1).map((g, gi) => (
                            <React.Fragment key={gi}>
                              <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: '#f1f5f9', border: '1px solid #e2e8f0', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.75rem', color: 'var(--text-secondary)', fontWeight: 700 }}>{row.row_number}</div>
                              <div style={{ display: 'flex', gap: '0.25rem' }}>{g.map((s, i) => renderSeat(s, i))}</div>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div style={{ width: fuselageWidth, height: '60px', border: '2px solid #cbd5e1', borderTop: 'none', borderBottomLeftRadius: '80px 60px', borderBottomRightRadius: '80px 60px', background: '#f1f5f9', margin: '0 auto' }} />
            </div>
            <div style={{ textAlign: 'center', padding: '0.5rem 0 0.25rem' }}>
              <strong style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>BACK OF AIRCRAFT</strong>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
