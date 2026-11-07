// Customer Portal → Loyalty Program. Everything shown comes from
// GET /api/customers/me/loyalty: the customer's balance and tier, every tier
// with the points it starts at and its admin-set benefits, the earning rate,
// and the points ledger. Nothing here is a sample figure. Styles: index.css
// under ".loy" (Tick and Book palette).
import { useMemo, useState } from 'react';
import { fmtDate } from './dateFormat.js';
import { IconPlane, IconInfo } from './WizardIcons.jsx';

const Svg = ({ size = 18, children }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
const IconShield = (p) => <Svg {...p}><path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6z" /><path d="m8.8 12 2.2 2.2 4.3-4.4" /></Svg>;
const IconCheck = (p) => <Svg {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Svg>;
const IconLock = (p) => <Svg {...p}><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></Svg>;
const IconSpark = (p) => <Svg {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" /></Svg>;
const IconTicket = (p) => <Svg {...p}><path d="M3 8.5V6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.5a2.5 2.5 0 0 0 0 5V16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2.5a2.5 2.5 0 0 0 0-5z" /><path d="M14 5v12" strokeDasharray="2 2.2" /></Svg>;
const IconTrophy = (p) => <Svg {...p}><path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20.5h7M10 17h4" /></Svg>;
const IconDownload = (p) => <Svg {...p}><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" /></Svg>;

const ORDER = ['Bronze', 'Silver', 'Gold', 'Platinum'];
const pts = (n) => `${Number(n || 0).toLocaleString()} pts`;
const num = (n) => Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });

// The tiers from the API; older answers (no "tiers") fall back to what they do carry.
function tierList(l) {
  if (l.tiers?.length) return l.tiers;
  return ORDER.map(name => ({ name, threshold: null,
    benefits: name === l.tier ? l.tier_benefits || [] : name === l.next_tier ? l.next_tier_benefits || [] : [] }));
}

function downloadStatement(rows, tier, balance) {
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = [['Date', 'Description', 'Booking', 'Points'].map(esc).join(',')]
    .concat(rows.map(t => [fmtDate(t.created_at), t.reason, t.locator_code || '', t.points_change].map(esc).join(',')));
  lines.push('', [esc('Balance'), esc(`${tier} member`), '', esc(balance)].join(','));
  const url = URL.createObjectURL(new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = `tick-and-book-points-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export default function LoyaltyPage({ loyalty }) {
  const tiers = useMemo(() => (loyalty ? tierList(loyalty) : []), [loyalty]);
  const current = loyalty?.tier || 'Bronze';
  const curIdx = Math.max(0, tiers.findIndex(t => t.name === current));
  const [picked, setPicked] = useState(null);

  const hero = (
    <section className="loy-hero">
      <h2>Tick and Book Loyalty Program</h2>
      <p>Book, travel, earn points and enjoy rewards on every trip.</p>
    </section>
  );
  if (!loyalty) {
    return (
      <div className="loy">
        {hero}
        <div className="loy-card loy-overlap"><div className="loading-state"><div className="spinner"></div><p>Loading your loyalty status…</p></div></div>
      </div>
    );
  }

  const balance = loyalty.points_balance || 0;
  const next = tiers[curIdx + 1];
  const from = tiers[curIdx]?.threshold ?? 0;
  const to = next?.threshold ?? (next ? balance + (loyalty.points_to_next_tier || 0) : null);
  const pct = next && to > from ? Math.max(0, Math.min(100, ((balance - from) / (to - from)) * 100)) : 100;
  const shown = tiers.find(t => t.name === (picked || next?.name || current)) || tiers[curIdx];
  const shownIdx = tiers.indexOf(shown);
  const state = shownIdx === curIdx ? 'current' : shownIdx < curIdx ? 'unlocked' : 'locked';
  const ledger = loyalty.recent_transactions || [];
  // The admin's earning rates, worded per currency; a rate of 0 earns nothing, so it isn't offered.
  const earn = loyalty.earn;
  const rates = [];
  if (earn?.points_per_lkr > 0) {
    const per100 = earn.points_per_lkr * 100;
    rates.push(per100 >= 1 ? `${num(per100)} point${per100 === 1 ? '' : 's'} for every LKR 100` : `1 point for every LKR ${num(1 / earn.points_per_lkr)}`);
  }
  if (earn?.points_per_usd > 0) rates.push(`${num(earn.points_per_usd)} point${earn.points_per_usd === 1 ? '' : 's'} for every USD 1`);
  const ladder = tiers.filter(t => t.threshold > 0);

  return (
    <div className="loy">
      {hero}

      {/* Tier card */}
      <section className={`loy-tier loy-overlap tier-${current.toLowerCase()}`}>
        <div className="loy-tier-top">
          <div className="loy-tier-id">
            <div className="loy-medal"><IconShield size={34} /><span>Tier {curIdx + 1}</span></div>
            <div>
              <span className="loy-pill">Current tier</span>
              <h3>{current} Member</h3>
              <div className="loy-tier-sub">{loyalty.member_since ? `Member since ${fmtDate(loyalty.member_since)}` : 'Tick and Book Rewards'}</div>
            </div>
          </div>
          <div className="loy-balance">
            <small>Available balance</small>
            <strong>{Number(balance).toLocaleString()}<span>pts</span></strong>
          </div>
        </div>
        <div className="loy-progress">
          {next ? (
            <>
              <div className="loy-progress-head">
                <span>Progress to <b>{next.name} Member</b></span>
                <b className="loy-amber">{Number(balance).toLocaleString()} / {to != null ? pts(to) : '—'}</b>
              </div>
              <div className="loy-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><i style={{ width: `${Math.max(pct, 2)}%` }} /></div>
              <div className="loy-progress-foot">
                <span><IconInfo size={14} /> {pts(loyalty.points_to_next_tier)} more to unlock {next.name}</span>
                <span>{Math.floor(pct)}% completed</span>
              </div>
            </>
          ) : (
            <div className="loy-progress-head"><span>You've reached the top tier — thank you for flying with us.</span></div>
          )}
        </div>
      </section>

      {/* Tier benefits */}
      <section className="loy-card">
        <div className="loy-head">
          <div>
            <h3>Tier benefits</h3>
            <p>Your perks today, and what the next tiers unlock.</p>
          </div>
          <div className="loy-seg" role="tablist" aria-label="Tiers">
            {tiers.map(t => (
              <button key={t.name} type="button" role="tab" aria-selected={t.name === shown?.name} onClick={() => setPicked(t.name)}>{t.name}</button>
            ))}
          </div>
        </div>
        <div className="loy-benefits">
          <div className="loy-mine">
            <div className="loy-mine-head">
              <span className={`loy-mini-medal tier-${current.toLowerCase()}`}><IconShield size={20} /></span>
              <div><h4>Your {current} benefits</h4><small>Active membership level</small></div>
              <span className="loy-tag">Current</span>
            </div>
            {(tiers[curIdx]?.benefits || []).length ? (
              <ul className="loy-list">{tiers[curIdx].benefits.map((b, i) => <li key={i}><IconCheck size={15} />{b}</li>)}</ul>
            ) : <p className="loy-muted">No benefits are listed for this tier yet.</p>}
          </div>
          <div className={`loy-other ${state}`}>
            <div className="loy-mine-head">
              <span className="loy-mini-medal dark"><IconSpark size={20} /></span>
              <div>
                <h4>{shown?.name} tier{state === 'current' ? ' (current)' : ''}</h4>
                <small>{state === 'current' ? 'Your level today' : state === 'unlocked' ? 'Already reached' : shown?.threshold != null ? `Unlocks at ${pts(shown.threshold)}` : 'Higher tier'}</small>
              </div>
              <span className={`loy-tag ${state}`}>{state === 'locked' ? <><IconLock size={13} /> Locked</> : state === 'current' ? 'Current' : 'Unlocked'}</span>
            </div>
            {(shown?.benefits || []).length ? (
              <ul className="loy-list">{shown.benefits.map((b, i) => <li key={i}><IconCheck size={15} />{b}</li>)}</ul>
            ) : <p className="loy-muted">No benefits are listed for this tier yet.</p>}
            {state === 'locked' && shown?.threshold != null && (
              <div className="loy-gap">{pts(Math.max(0, shown.threshold - balance))} to go</div>
            )}
          </div>
        </div>
      </section>

      {/* How you earn */}
      <section className="loy-card">
        <div className="loy-head"><div><h3>How you earn points</h3><p>Points come from the flights you book with Tick and Book.</p></div></div>
        <div className="loy-earn">
          <div className="loy-earn-card">
            <span className="loy-earn-icon c1"><IconPlane size={20} /></span>
            <h4>Book flights</h4>
            <p>{!earn ? 'Earn points on the flights you book.'
              : rates.length ? <>Earn {rates.map((r, i) => <span key={i}>{i > 0 && ' and '}<b>{r}</b></span>)} you pay for a flight.</>
              : 'Flight points are not being awarded at the moment.'}</p>
          </div>
          <div className="loy-earn-card">
            <span className="loy-earn-icon c2"><IconTicket size={20} /></span>
            <h4>Added when you're ticketed</h4>
            <p>Your points land in your balance as soon as the airline issues your e-ticket.</p>
          </div>
          <div className="loy-earn-card">
            <span className="loy-earn-icon c3"><IconTrophy size={20} /></span>
            <h4>Move up the tiers</h4>
            <p>{ladder.length ? ladder.map(t => `${t.name} at ${pts(t.threshold)}`).join(', ') + '.' : 'Higher tiers unlock as your balance grows.'} Your tier follows your points balance.</p>
          </div>
        </div>
      </section>

      {/* Ledger */}
      <section className="loy-card">
        <div className="loy-head">
          <div><h3>Points activity</h3><p>Every point you've earned or that was adjusted on your account.</p></div>
          {ledger.length > 0 && (
            <button type="button" className="loy-download" onClick={() => downloadStatement(ledger, current, balance)}>
              <IconDownload size={16} /> Download statement
            </button>
          )}
        </div>
        {ledger.length === 0 ? (
          <div className="loy-empty">No points activity yet. Your first flight ticket starts your balance.</div>
        ) : (
          <div className="loy-table-wrap">
            <table className="loy-table">
              <thead><tr><th>Date</th><th>Description</th><th>Type</th><th className="r">Points</th></tr></thead>
              <tbody>
                {ledger.map(t => (
                  <tr key={t.id}>
                    <td className="nowrap">{fmtDate(t.created_at)}</td>
                    <td>{t.reason}</td>
                    <td><span className={`loy-type ${t.locator_code ? 'flight' : 'adj'}`}>{t.locator_code ? 'Flight' : 'Adjustment'}</span></td>
                    <td className={`r nowrap ${t.points_change >= 0 ? 'plus' : 'minus'}`}>{t.points_change >= 0 ? '+' : ''}{Number(t.points_change).toLocaleString()} pts</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
