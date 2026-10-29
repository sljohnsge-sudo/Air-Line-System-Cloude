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

export default function FareRulesPanel({ apiBase, rawOffering, locator, title = 'Fare rules', defaultOpen = false }) {
  const [open, setOpen] = useState(false);
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
    if (defaultOpen) { setOpen(true); load(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', background: '#ffffff' }}>
      <button
        type="button"
        onClick={toggle}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', padding: '0.55rem 0.75rem', cursor: 'pointer', fontSize: '0.78rem', fontWeight: 700, color: '#1e293b' }}
      >
        <span>📄 {title}</span>
        <span style={{ color: '#64748b' }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div style={{ padding: '0 0.75rem 0.75rem', fontSize: '0.76rem', color: '#334155' }}>
          {state.loading && <div style={{ color: '#64748b' }}>Loading the airline's fare rules…</div>}
          {state.error && (
            <div style={{ color: '#b91c1c' }}>
              {state.error}{' '}
              <button type="button" onClick={load} style={{ background: 'none', border: 'none', color: '#1d4ed8', cursor: 'pointer', padding: 0, fontSize: 'inherit' }}>Try again</button>
            </div>
          )}
          {state.fares && state.fares.length === 0 && <div style={{ color: '#64748b' }}>The airline returned no fare rules for this fare.</div>}
          {(state.fares || []).map((fare, i) => (
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
                    {key && <div style={{ marginBottom: '0.25rem' }}><strong>{key.name}:</strong> <span style={{ fontFamily: 'monospace' }}>{key.value}</span></div>}
                    {rest.length > 0 && (
                      <>
                        <button type="button" onClick={() => setShowAll(s => ({ ...s, [i]: !s[i] }))} style={{ background: 'none', border: 'none', color: '#1d4ed8', cursor: 'pointer', padding: 0, fontSize: '0.74rem', fontWeight: 600 }}>
                          {showAll[i] ? 'Hide' : 'Show'} all conditions ({rest.length})
                        </button>
                        {showAll[i] && (
                          <div style={{ marginTop: '0.3rem', display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                            {rest.map((s, k) => (
                              <div key={k}><strong>{s.name}:</strong> <span style={{ fontFamily: 'monospace' }}>{s.value}</span></div>
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
            <div style={{ marginTop: '0.5rem', fontSize: '0.68rem', color: '#94a3b8' }}>
              From the airline's filed fare rules via Travelport, in the currency the airline files them in.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
