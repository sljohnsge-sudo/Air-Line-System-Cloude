// Admin › Customers & Insights › Analytics Logins. Separate sign-in accounts
// for the business dashboard at /analytics (they cannot open the Admin
// Console). Only a super admin can add accounts, set passwords or deactivate
// them; other admins see the list read-only. Backend:
// services/analytics_service.py, /api/admin/analytics-users.
import { useCallback, useEffect, useRef, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';
import './analytics.css';

const MIN_PW = 10;

export default function AdminAnalyticsUsers({ API_BASE, adminToken, onSessionExpired }) {
  const expiredRef = useRef(onSessionExpired);
  expiredRef.current = onSessionExpired;
  const json = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin${path}`, {
      ...opts,
      headers: { Authorization: `Bearer ${adminToken}`, ...(opts.body ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) },
    });
    if (res.status === 401) { expiredRef.current?.(); throw new Error('Your session expired — please log in again.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Request failed (${res.status})`);
    }
    return data;
  }, [API_BASE, adminToken]);

  const [d, setD] = useState(null);
  const [error, setError] = useState('');
  const [ok, setOk] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ username: '', full_name: '', password: '' });
  const [pwFor, setPwFor] = useState(null);
  const [pw, setPw] = useState('');

  const load = useCallback(() => json('/analytics-users').then(setD).catch(e => setError(e.message)), [json]);
  useEffect(() => { load(); }, [load]);

  const run = async (fn, msg) => {
    setBusy(true); setError(''); setOk('');
    try { await fn(); setOk(msg); await load(); } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const create = (e) => {
    e.preventDefault();
    run(async () => {
      await json('/analytics-users', { method: 'POST', body: JSON.stringify(form) });
      setForm({ username: '', full_name: '', password: '' });
    }, `Account ${form.username.trim().toLowerCase()} created. They sign in at /analytics.`);
  };
  const setActive = (u, active) => run(
    () => json(`/analytics-users/${u.id}`, { method: 'PUT', body: JSON.stringify({ active }) }),
    `${u.username} ${active ? 're-activated' : 'deactivated — signed out at once'}.`);
  const savePw = (u) => run(async () => {
    await json(`/analytics-users/${u.id}`, { method: 'PUT', body: JSON.stringify({ password: pw }) });
    setPwFor(null); setPw('');
  }, `New password set for ${u.username}.`);
  const refresh = () => run(() => json('/analytics/refresh', { method: 'POST' }), 'Analytics data refreshed.');

  if (!d && !error) return <div className="loading-state">Loading…</div>;
  const canEdit = !!d?.can_edit;
  const sync = d?.last_sync;

  return (
    <div className="an-admin">
      {error && <div className="error-banner">{error}</div>}
      {ok && <div className="an-ok">{ok}</div>}
      {d && (
        <>
          <div className="recon-head">
            <div className="adm-sub an-wrap">
              Dashboard: <a href="/analytics" target="_blank" rel="noreferrer">/analytics</a>
              {' · '}{d.storage?.note}
              {' · '}{sync?.refreshed_at ? `last refreshed ${fmtDateTime(sync.refreshed_at)}` : 'not refreshed yet'}
              {sync?.counts ? ` (${sync.counts.bookings} bookings, ${sync.counts.customers} customers, ${sync.counts.searches} searches)` : ''}
            </div>
            <button className="btn btn-secondary btn-sm" onClick={refresh} disabled={busy}>Refresh analytics now</button>
          </div>

          {canEdit ? (
            <form className="an-form" onSubmit={create}>
              <label>Username
                <input className="form-input" value={form.username} autoComplete="off" placeholder="e.g. finance.lead"
                       onChange={e => setForm(f => ({ ...f, username: e.target.value }))} required minLength={3} maxLength={50} />
              </label>
              <label>Full name
                <input className="form-input" value={form.full_name} autoComplete="off"
                       onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))} maxLength={150} />
              </label>
              <label>Password (min {MIN_PW})
                <input className="form-input" type="password" value={form.password} autoComplete="new-password"
                       onChange={e => setForm(f => ({ ...f, password: e.target.value }))} required minLength={MIN_PW} />
              </label>
              <button className="btn btn-primary btn-sm" type="submit" disabled={busy || form.password.length < MIN_PW || form.username.trim().length < 3}>
                Add account
              </button>
            </form>
          ) : (
            <p className="adm-sub an-wrap" style={{ margin: '0.6rem 0' }}>Read only — only the main admin account (<strong>admin</strong>) can add analytics accounts, set passwords or deactivate them. Log out and sign in as <strong>admin</strong> to manage them.</p>
          )}

          <div className="adm-table-wrap">
            <table className="adm-table">
              <thead><tr><th>Username</th><th>Name</th><th>Status</th><th>Created</th><th>Last sign-in</th><th>Changed</th>{canEdit && <th></th>}</tr></thead>
              <tbody>
                {d.users.map(u => (
                  <tr key={u.id}>
                    <td><strong>{u.username}</strong></td>
                    <td>{u.full_name || '—'}</td>
                    <td>{u.active ? <span className="rl-chip ok">Active</span> : <span className="rl-chip muted">Deactivated</span>}</td>
                    <td className="nowrap">{fmtDateTime(u.created_at)}{u.created_by && <div className="adm-sub">by {u.created_by}</div>}</td>
                    <td className="nowrap">{fmtDateTime(u.last_login_at, '—')}</td>
                    <td className="nowrap">{fmtDateTime(u.updated_at, '—')}{u.updated_by && <div className="adm-sub">by {u.updated_by}</div>}</td>
                    {canEdit && (
                      <td>
                        {pwFor === u.id ? (
                          <div className="an-row-actions">
                            <input className="form-input" type="password" autoComplete="new-password" placeholder={`New password (min ${MIN_PW})`}
                                   value={pw} onChange={e => setPw(e.target.value)} style={{ maxWidth: 200 }} />
                            <button className="btn btn-primary btn-sm" disabled={busy || pw.length < MIN_PW} onClick={() => savePw(u)}>Save</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => { setPwFor(null); setPw(''); }}>Cancel</button>
                          </div>
                        ) : (
                          <div className="an-row-actions">
                            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { setPwFor(u.id); setPw(''); }}>Set password</button>
                            {u.active
                              ? <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setActive(u, false)}>Deactivate</button>
                              : <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setActive(u, true)}>Re-activate</button>}
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
                {!d.users.length && <tr><td colSpan={canEdit ? 7 : 6} className="adm-sub">No analytics accounts yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
