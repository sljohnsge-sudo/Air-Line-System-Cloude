// Admin site › Administration › Staff Users & Roles (16/11/2026). One list
// of staff sign-ins with a role each; only an Admin sees this page and can
// create users, change roles, disable/enable accounts and reset passwords
// (backend /api/admin/admins, services/admin_db.py).
//   Admin            — everything: Admin Portal, Analytics, users, switches
//   Staff            — Admin Portal day-to-day work
//   Analytics        — Analytics dashboard only
//   Staff + Analytics — both
import { useCallback, useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';

const ROLE_HELP = {
  admin: 'Everything: Admin Portal, Analytics, staff users, switches and settings',
  staff: 'Admin Portal day-to-day work (no users, switches or settings)',
  analytics: 'Analytics dashboard only',
  staff_analytics: 'Admin Portal (Staff) and the Analytics dashboard',
};
const EMPTY = { username: '', full_name: '', password: '', role: 'staff' };

export default function AdminStaffUsers({ API_BASE, adminToken, onSessionExpired }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const [form, setForm] = useState(EMPTY);
  const [resetFor, setResetFor] = useState(null);   // user id
  const [resetPw, setResetPw] = useState('');

  const call = useCallback(async (path = '', opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/admins${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please sign in again.'); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body.detail;
      throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Request failed (${res.status})`);
    }
    return body;
  }, [API_BASE, adminToken, onSessionExpired]);

  const load = useCallback(() => call().then(setData).catch(e => setError(e.message)), [call]);
  useEffect(() => { load(); }, [load]);

  const change = async (u, patch, done) => {
    setBusy(`u${u.id}`); setError(''); setMsg('');
    try { await call(`/${u.id}`, { method: 'PUT', body: JSON.stringify(patch) }); setMsg(done); await load(); }
    catch (e) { setError(e.message); }
    setBusy('');
  };

  const create = async (e) => {
    e.preventDefault();
    setBusy('create'); setError(''); setMsg('');
    try {
      await call('', { method: 'POST', body: JSON.stringify({ ...form, full_name: form.full_name || null }) });
      setMsg(`User "${form.username}" created as ${data?.roles?.[form.role] || form.role}.`);
      setForm(EMPTY);
      await load();
    } catch (err) { setError(err.message); }
    setBusy('');
  };

  if (!data) return error ? <div className="error-banner">{error}</div>
    : <div className="loading-state"><div className="spinner"></div><p>Loading staff users…</p></div>;

  const roles = data.roles || {};
  return (
    <div className="su">
      {error && <div className="error-banner">{error}</div>}
      {msg && <div className="su-msg">{msg}</div>}

      <div className="su-table-wrap">
        <table className="su-table">
          <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last sign-in</th><th>Actions</th></tr></thead>
          <tbody>
            {data.admins.map(u => {
              const me = u.id === data.me;
              const disabled = !u.active;
              return (
                <tr key={u.id} className={disabled ? 'su-off' : ''}>
                  <td>
                    <strong>{u.username}</strong>{me && <span className="su-you">you</span>}
                    {u.full_name && <div className="su-sub">{u.full_name}</div>}
                    {u.created_by && <div className="su-sub">Created by {u.created_by}</div>}
                  </td>
                  <td>
                    <select className="form-select su-role" value={u.role} disabled={me || !!busy}
                            title={me ? 'Ask another Admin to change your own role' : ROLE_HELP[u.role]}
                            onChange={e => change(u, { role: e.target.value }, `${u.username} is now ${roles[e.target.value]}.`)}>
                      {Object.entries(roles).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                  </td>
                  <td><span className={`su-state ${u.active ? 'on' : 'off'}`}>{u.active ? 'Active' : 'Disabled'}</span></td>
                  <td className="su-sub">{u.last_login_at ? fmtDateTime(u.last_login_at) : 'Never'}</td>
                  <td>
                    <div className="su-actions">
                      {!me && (
                        <button type="button" className="btn btn-secondary btn-sm" disabled={!!busy}
                                onClick={() => change(u, { active: !u.active }, `${u.username} ${u.active ? 'disabled' : 'enabled'}.`)}>
                          {u.active ? 'Disable' : 'Enable'}
                        </button>
                      )}
                      <button type="button" className="btn btn-secondary btn-sm" disabled={!!busy}
                              onClick={() => { setResetFor(resetFor === u.id ? null : u.id); setResetPw(''); }}>
                        Reset password
                      </button>
                    </div>
                    {resetFor === u.id && (
                      <form className="su-reset" onSubmit={e => { e.preventDefault(); change(u, { password: resetPw }, `New password set for ${u.username}.`).then(() => setResetFor(null)); }}>
                        <input type="password" className="form-input" placeholder="New password (10+ characters)" minLength={10} required
                               autoComplete="new-password" value={resetPw} onChange={e => setResetPw(e.target.value)} />
                        <button type="submit" className="btn btn-primary btn-sm" disabled={!!busy}>Save</button>
                      </form>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h4 className="su-h">Add a staff user</h4>
      <form className="su-form" onSubmit={create}>
        <div className="form-group">
          <label className="form-label" htmlFor="su-username">Username *</label>
          <input id="su-username" className="form-input" required minLength={3} pattern="[A-Za-z0-9._\-]+"
                 title="Letters, numbers, dot, dash or underscore" autoComplete="off"
                 value={form.username} onChange={e => setForm(f => ({ ...f, username: e.target.value }))} />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="su-name">Full name</label>
          <input id="su-name" className="form-input" value={form.full_name} onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))} />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="su-password">Password * (10+ characters)</label>
          <input id="su-password" type="password" className="form-input" required minLength={10} autoComplete="new-password"
                 value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="su-role-new">Role *</label>
          <select id="su-role-new" className="form-select" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
            {Object.entries(roles).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <div className="su-sub">{ROLE_HELP[form.role]}</div>
        </div>
        <button type="submit" className="btn btn-primary" disabled={busy === 'create'}>{busy === 'create' ? 'Creating…' : 'Create user'}</button>
      </form>

      <div className="su-legend">
        {Object.entries(roles).map(([k, label]) => <div key={k}><strong>{label}:</strong> {ROLE_HELP[k]}</div>)}
        <div>A disabled account, or one whose role changes, is signed out within a minute.</div>
      </div>
    </div>
  );
}
