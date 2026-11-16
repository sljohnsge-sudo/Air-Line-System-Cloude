// Admin › Administration › Website Contacts & Texts (16/11/2026). Staff edit
// the customer contact details and the customer-facing texts, section by
// section (backend services/site_content.py). Each section is saved on its
// own; an emptied field goes back to the standard text.
import { useCallback, useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';

const KIND_INPUT = { phone: 'tel', email: 'email', text: 'text' };

export default function AdminSiteContent({ API_BASE, adminToken, onSessionExpired }) {
  const [data, setData] = useState(null);
  const [drafts, setDrafts] = useState({});   // key -> edited text
  const [error, setError] = useState('');
  const [saving, setSaving] = useState('');   // section id
  const [saved, setSaved] = useState('');     // section id just saved

  const call = useCallback(async (opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/site-content`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`);
    return body;
  }, [API_BASE, adminToken, onSessionExpired]);

  useEffect(() => { call().then(setData).catch(e => setError(e.message)); }, [call]);

  const valueOf = (item) => (item.key in drafts ? drafts[item.key] : item.value);
  const changedIn = (section) => section.items.filter(i => i.key in drafts && drafts[i.key] !== i.value);

  const save = async (section, overrides = null) => {
    const values = overrides || Object.fromEntries(changedIn(section).map(i => [i.key, drafts[i.key]]));
    if (!Object.keys(values).length) return;
    setSaving(section.id); setError(''); setSaved('');
    try {
      const next = await call({ method: 'PUT', body: JSON.stringify({ values }) });
      setData(next);
      setDrafts(d => Object.fromEntries(Object.entries(d).filter(([k]) => !(k in values))));
      setSaved(section.id);
    } catch (e) { setError(e.message); }
    setSaving('');
  };

  if (error && !data) return <div className="error-banner">{error}</div>;
  if (!data) return <div className="loading-state"><div className="spinner"></div><p>Loading website texts…</p></div>;

  return (
    <div className="glc">
      {!data.can_change && (
        <div className="glc-note">Only a super admin can change these. You can read them here.</div>
      )}
      {error && <div className="error-banner">{error}</div>}
      {data.sections.map(section => {
        const changed = changedIn(section);
        return (
          <div key={section.id} className="sc-section">
            <div className="glc-group">{section.title}</div>
            {section.note && <p className="glc-desc" style={{ margin: '0 0 0.6rem' }}>{section.note}</p>}
            {section.items.map(item => {
              const id = `sc-${item.key}`;
              const v = valueOf(item);
              const multi = item.kind === 'paragraph' || item.kind === 'list';
              return (
                <div key={item.key} className="form-group sc-item">
                  <label className="form-label" htmlFor={id}>
                    {item.label}
                    {item.customised && <span className="sc-badge">changed</span>}
                  </label>
                  {multi ? (
                    <textarea id={id} className="form-input" rows={item.kind === 'list' ? 7 : 4} value={v}
                              disabled={!data.can_change} onChange={e => setDrafts(d => ({ ...d, [item.key]: e.target.value }))} />
                  ) : (
                    <input id={id} type={KIND_INPUT[item.kind] || 'text'} className="form-input" value={v}
                           disabled={!data.can_change} onChange={e => setDrafts(d => ({ ...d, [item.key]: e.target.value }))} />
                  )}
                  <div className="glc-desc">
                    {item.help ? `${item.help} ` : ''}
                    {item.customised && item.updated_by ? `Last changed by ${item.updated_by}${item.updated_at ? ` · ${fmtDateTime(item.updated_at)}` : ''}. ` : ''}
                    {data.can_change && item.customised && (
                      <button type="button" className="sc-link" disabled={!!saving}
                              onClick={() => save(section, { [item.key]: '' })}>Use the standard text</button>
                    )}
                  </div>
                </div>
              );
            })}
            {data.can_change && (
              <div className="sc-actions">
                <button type="button" className="btn btn-primary btn-sm" disabled={!changed.length || !!saving}
                        onClick={() => save(section)}>
                  {saving === section.id ? 'Saving…' : `Save ${section.title}`}
                </button>
                {changed.length > 0 && (
                  <button type="button" className="btn btn-secondary btn-sm" disabled={!!saving}
                          onClick={() => setDrafts(d => Object.fromEntries(Object.entries(d).filter(([k]) => !section.items.some(i => i.key === k))))}>
                    Discard changes
                  </button>
                )}
                {saved === section.id && !changed.length && <span className="sc-saved">Saved. Customers see it within a minute.</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
