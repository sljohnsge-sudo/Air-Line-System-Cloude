// Admin › Providers & Modules › Go-live controls (11/11/2026). Super-admin
// switches for go-live testing: flight search, creating new bookings (PNR),
// issuing tickets, and staff void/refund. Every switch only stops a customer
// BEFORE they pay — a paid booking is always completed
// (backend services/go_live_switches.py).
import { useCallback, useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';

const ROWS = [
  { key: 'flight_search_enabled', label: 'Flight search results',
    desc: 'Customers can search flights. Off: the search page says flight search is paused.' },
  { key: 'pnr_create_enabled', label: 'Create bookings (PNR)',
    desc: 'Customers can make a new booking. Off: stopped before any booking is made or anything is charged.' },
  { key: 'ticketing_enabled', label: 'Issue tickets',
    desc: 'Customers can pay for and receive tickets. Off: stopped at the payment step, so nobody pays for a ticket that won’t be issued. Bookings already paid for still get their ticket.' },
  { key: 'ticketed_cancel_enabled', label: 'Void & refund ticketed bookings',
    desc: 'Staff can void or refund a ticketed booking (All Bookings and Cancellation Requests). Cancelling a held booking always works.' },
];

export default function AdminGoLiveControls({ API_BASE, adminToken, onSessionExpired, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState('');
  const [confirm, setConfirm] = useState(null); // {key, value}

  const call = useCallback(async (opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/go-live-switches`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`);
    return body;
  }, [API_BASE, adminToken, onSessionExpired]);

  useEffect(() => { call().then(setData).catch(e => setError(e.message)); }, [call]);

  const apply = async (key, value) => {
    setSaving(key); setError(''); setConfirm(null);
    try {
      setData(await call({ method: 'PUT', body: JSON.stringify({ [key]: value }) }));
      onChanged?.();
    } catch (e) { setError(e.message); }
    setSaving('');
  };

  if (error && !data) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="loading-state"><div className="spinner"></div><p>Loading go-live controls…</p></div>;

  return (
    <div className="glc">
      {!data.can_change && (
        <div className="glc-note">Only the main admin account (<strong>admin</strong>) can change these switches{data.signed_in_as ? <> — you are signed in as <strong>{data.signed_in_as}</strong></> : null}. To change them, log out and sign in as <strong>admin</strong>.</div>
      )}
      {error && <div className="error-banner">{error}</div>}
      {ROWS.map(r => {
        const on = !!data[r.key];
        const pending = confirm && confirm.key === r.key;
        return (
          <div key={r.key} className={`glc-row ${on ? 'on' : 'off'}`}>
            <div className="glc-text">
              <div className="glc-label">{r.label}</div>
              <div className="glc-desc">{r.desc}</div>
            </div>
            <div className="glc-actions">
              <span className={`glc-state ${on ? 'on' : 'off'}`}>{on ? 'ON' : 'OFF'}</span>
              {data.can_change && !pending && (
                <button type="button" className="btn btn-secondary btn-sm" disabled={!!saving}
                        onClick={() => setConfirm({ key: r.key, value: !on })}>
                  {saving === r.key ? 'Saving…' : on ? 'Turn off' : 'Turn on'}
                </button>
              )}
              {pending && (
                <span className="glc-confirm">
                  {confirm.value ? 'Turn on?' : 'Turn off?'}
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => apply(r.key, confirm.value)}>Yes</button>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setConfirm(null)}>No</button>
                </span>
              )}
            </div>
          </div>
        );
      })}
      {data.is_super_admin && (
        <div className={`glc-row ${data.all_admins_can_switch ? 'on' : 'off'}`}>
          <div className="glc-text">
            <div className="glc-label">All staff accounts can change these switches</div>
            <div className="glc-desc">On: every staff account can change the go-live, payment-method and void/refund switches. Off: only the main admin account (admin).</div>
          </div>
          <div className="glc-actions">
            <span className={`glc-state ${data.all_admins_can_switch ? 'on' : 'off'}`}>{data.all_admins_can_switch ? 'ON' : 'OFF'}</span>
            <button type="button" className="btn btn-secondary btn-sm" disabled={!!saving}
                    onClick={() => apply('all_admins_can_switch', !data.all_admins_can_switch)}>
              {data.all_admins_can_switch ? 'Turn off' : 'Turn on'}
            </button>
          </div>
        </div>
      )}
      {data.updated_by && (
        <div className="glc-foot">Last changed by {data.updated_by}{data.updated_at ? ` · ${fmtDateTime(data.updated_at)}` : ''}</div>
      )}
    </div>
  );
}
