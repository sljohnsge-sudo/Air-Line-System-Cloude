// Fare rules (change / cancellation conditions) of a Travelport fare, loaded
// live on demand: after search from the fare's raw_offering
// (POST /api/fare-rules), or for a booking from its PNR
// (GET /api/bookings/{locator}/fare-rules). Shown on the fare cards and the
// Review step. Only what Travelport returns is shown — nothing is filled in.
import React, { useState, useEffect } from 'react';

const PTC_LABELS = { ADT: 'Adult', CNN: 'Child', INF: 'Infant', STU: 'Student' };

const money = (a) => `${a.currency || ''} ${Number(a.amount).toLocaleString()}`.trim();

function penaltyValue(p) {
  if (p.permitted === false) return { text: 'Not allowed', color: '#b91c1c' };
  const parts = [];
  const byCurrency = {};
  (p.amounts || []).forEach(a => { (byCurrency[a.currency] = byCurrency[a.currency] || []).push(a); });
  Object.values(byCurrency).forEach(list => {
    parts.push(list.length > 1 ? `${money(list[0])} – ${Number(list[list.length - 1].amount).toLocaleString()}` : money(list[0]));
  });
  (p.percents || []).forEach(v => parts.push(`${v}%`));
  if (parts.length) return { text: parts.join(' / '), color: '#1e293b' };
  // The airline filed this fee in another currency (e.g. AUD); its LKR amount
  // is on the fare's Changes / Cancellations line from the search.
  if (p.fee_applies) return { text: 'Fee applies', color: '#1e293b' };
  if (p.permitted) return { text: 'Allowed (fee not stated)', color: '#475569' };
  return { text: 'See conditions below', color: '#475569' };
}

function penaltyLabel(p) {
  let label = p.kind === 'Change' ? 'Change' : 'Cancellation';
  if (p.when && p.when !== 'any time') label += ` · ${p.when}`;
  if (p.within) label += ` (${p.within})`;
  const ptc = (p.passenger_types || []).filter(t => t !== 'ADT');
  if (ptc.length) label += ` · ${ptc.map(t => PTC_LABELS[t] || t).join(', ')}`;
  return label;
}

// "QR659 CMB-DOH · QR1022 DOH-DXB" -> "CMB → DXB" (whole journey of one fare)
function legName(fare) {
  const routes = (fare.flights || []).map(f => (f.split(' ')[1] || '').split('-')).filter(r => r.length === 2);
  return routes.length ? `${routes[0][0]} → ${routes[routes.length - 1][1]}` : (fare.brand_name || 'Fare');
}

// One list for the customer: each rule once; when the outbound and return
// fares differ, the rule shows each journey's value under it.
function consolidate(fares) {
  const rows = new Map();
  fares.forEach(fare => {
    const leg = legName(fare);
    (fare.penalties || []).forEach(p => {
      const label = penaltyLabel(p);
      if (!rows.has(label)) rows.set(label, { label, values: [] });
      const v = penaltyValue(p);
      if (!rows.get(label).values.some(x => x.leg === leg && x.text === v.text)) rows.get(label).values.push({ leg, ...v });
    });
  });
  return [...rows.values()];
}

// Short customer wording: "Change before departure", "Cancel after departure"
const shortLabel = (label) => label.replace(/^Cancellation/, 'Cancel').replace(' · ', ' ');

function ConsolidatedRules({ fares, fallback }) {
  const rows = consolidate(fares);
  // The airline filed no amount we can show (e.g. a fee in another currency):
  // keep the fare's own Changes / Cancel lines from the search.
  const vague = rows.length === 0 || rows.some(r => r.values.some(v => v.text === 'Fee applies' || v.text.startsWith('See conditions')));
  const notes = [...new Set(fares.flatMap(f => f.notes || []))];
  // Nothing structured came back: show the airline's wording once (no repeats).
  const wording = rows.length ? [] : [...new Map(fares.flatMap(f => f.summary || []).map(s => [`${s.name}|${s.value}`, s])).values()];
  return (
    <div>
      {rows.map(r => {
        const same = r.values.every(v => v.text === r.values[0].text);
        return (
          <div key={r.label} style={{ padding: '0.12rem 0', borderBottom: '1px solid #f1f5f9' }}>
            {same ? (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.4rem' }}>
                <span style={{ color: '#475569' }}>{shortLabel(r.label)}</span>
                <strong style={{ color: r.values[0].color, whiteSpace: 'nowrap' }}>{r.values[0].text}</strong>
              </div>
            ) : (
              <>
                <div style={{ color: '#475569' }}>{shortLabel(r.label)}</div>
                {r.values.map(v => (
                  <div key={v.leg} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', paddingLeft: '0.5rem' }}>
                    <span style={{ color: '#94a3b8' }}>{v.leg}</span>
                    <strong style={{ color: v.color, whiteSpace: 'nowrap' }}>{v.text}</strong>
                  </div>
                ))}
              </>
            )}
          </div>
        );
      })}
      {vague && fallback && <div style={{ marginTop: '0.2rem' }}>{fallback}</div>}
      {!fallback && wording.map((s, k) => <div key={k} style={{ marginTop: '0.2rem' }}><strong>{s.name}:</strong> {s.value}</div>)}
      {notes.length > 0 && <div style={{ marginTop: '0.3rem', color: '#92400e' }}>{notes.join(' ')}</div>}
    </div>
  );
}

// alwaysOpen: the Review step shows the rules in full, with no collapse toggle.
// consolidated: the fare chooser shows each rule once (no per-flight repeats).
export default function FareRulesPanel({ apiBase, rawOffering, locator, title = 'Fare rules', defaultOpen = false, alwaysOpen = false, consolidated = false, children }) {
  const [open, setOpen] = useState(alwaysOpen);
  const [state, setState] = useState({ loading: false, error: '', fares: null });
  const [showAll, setShowAll] = useState({});

  const load = async () => {
    setState({ loading: true, error: '', fares: null });
    try {
      const res = locator
        ? await fetch(`${apiBase}/bookings/${encodeURIComponent(locator)}/fare-rules`)
        : await fetch(`${apiBase}/fare-rules`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_offering: rawOffering }),
          });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Fare rules could not be loaded.');
      setState({ loading: false, error: '', fares: data.fares || [] });
    } catch (e) {
      setState({ loading: false, error: e.message || String(e), fares: null });
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !state.fares && !state.loading) load();
  };

  // Opened by default (Review step): load once when shown.
  useEffect(() => {
    if (defaultOpen || alwaysOpen) { setOpen(true); load(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', background: '#ffffff' }}>
      {alwaysOpen ? (
        <div style={{ padding: '0.5rem 0.75rem 0.4rem', fontSize: '0.8rem', fontWeight: 700, color: '#1e293b' }}>📄 {title}</div>
      ) : (
      <button
        type="button"
        onClick={toggle}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', padding: '0.35rem 0.6rem', cursor: 'pointer', fontSize: '0.72rem', fontWeight: 700, color: '#1e293b' }}
      >
        <span>📄 {title}</span>
        <span style={{ color: '#64748b' }}>{open ? '▾' : '▸'}</span>
      </button>
      )}
      {open && (
        <div style={{ padding: consolidated ? '0 0.6rem 0.4rem' : '0 0.75rem 0.75rem', fontSize: consolidated ? '0.7rem' : '0.76rem', color: '#334155' }}>
          {state.loading && (consolidated && children ? children : <div style={{ color: '#64748b' }}>Loading the airline's fare rules…</div>)}
          {state.error && (
            <div style={{ color: '#b91c1c' }}>
              {consolidated && children && <div style={{ color: '#334155' }}>{children}</div>}
              {state.error}{' '}
              <button type="button" onClick={load} style={{ background: 'none', border: 'none', color: '#1d4ed8', cursor: 'pointer', padding: 0, fontSize: 'inherit' }}>Try again</button>
            </div>
          )}
          {state.fares && state.fares.length === 0 && (consolidated && children ? children : <div style={{ color: '#64748b' }}>The airline returned no fare rules for this fare.</div>)}
          {consolidated && state.fares?.length > 0 && <ConsolidatedRules fares={state.fares} fallback={children} />}
          {!consolidated && (state.fares || []).map((fare, i) => (
            <div key={i} style={{ borderTop: i ? '1px dashed #e2e8f0' : 'none', paddingTop: i ? '0.6rem' : 0, marginTop: i ? '0.6rem' : 0 }}>
              {(fare.flights?.length > 0 || fare.brand_name) && (
                <div style={{ fontWeight: 700, color: '#475569', marginBottom: '0.35rem' }}>
                  {fare.flights?.join(' · ')}{fare.brand_name ? ` — ${fare.brand_name}` : ''}{fare.fare_source ? ` (${fare.fare_source})` : ''}
                </div>
              )}
              {fare.penalties?.length > 0 && (
                <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '0.4rem' }}>
                  <tbody>
                    {fare.penalties.map((p, j) => {
                      const v = penaltyValue(p);
                      return (
                        <tr key={j} style={{ borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '0.25rem 0', color: '#475569' }}>{penaltyLabel(p)}</td>
                          <td style={{ padding: '0.25rem 0', textAlign: 'right', fontWeight: 700, color: v.color, whiteSpace: 'nowrap' }}>{v.text}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {fare.summary?.length > 0 && (() => {
                const key = fare.summary.find(s => /penalt/i.test(s.name || ''));
                const rest = fare.summary.filter(s => s !== key);
                return (
                  <div>
                    {key && <div style={{ marginBottom: '0.25rem' }}><strong>{key.name}:</strong> <span style={{ fontFamily: 'var(--font-body)' }}>{key.value}</span></div>}
                    {rest.length > 0 && (
                      <>
                        <button type="button" onClick={() => setShowAll(s => ({ ...s, [i]: !(s[i] ?? alwaysOpen) }))} style={{ background: 'none', border: 'none', color: '#1d4ed8', cursor: 'pointer', padding: 0, fontSize: '0.74rem', fontWeight: 600 }}>
                          {(showAll[i] ?? alwaysOpen) ? 'Hide' : 'Show'} all conditions ({rest.length})
                        </button>
                        {(showAll[i] ?? alwaysOpen) && (
                          <div style={{ marginTop: '0.3rem', display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                            {rest.map((s, k) => (
                              <div key={k}><strong>{s.name}:</strong> <span style={{ fontFamily: 'var(--font-body)' }}>{s.value}</span></div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                );
              })()}
              {fare.notes?.length > 0 && (
                <div style={{ marginTop: '0.3rem', color: '#92400e' }}>{fare.notes.join(' ')}</div>
              )}
            </div>
          ))}
          {state.fares?.length > 0 && (
            <div style={{ marginTop: consolidated ? '0.2rem' : '0.5rem', fontSize: '0.68rem', color: '#94a3b8' }}>
              {consolidated ? 'Live from the airline via Travelport.' : "From the airline's filed fare rules via Travelport, in the currency the airline files them in."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
