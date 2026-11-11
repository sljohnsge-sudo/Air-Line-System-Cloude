// Customer account › My Profile (11/11/2026). Saved travellers — filled
// automatically from what the customer typed when booking, editable — and
// other details the customer keeps up to date themselves (NIC, address,
// emergency contact, preferences, frequent-flyer numbers, visas).
// Backend: services/customer_profile.py. Email / mobile of the account are
// changed with a one-time code ("Change email / mobile" above).
import { useCallback, useEffect, useState } from 'react';
import { fmtDate } from './dateFormat.js';
import { COUNTRIES } from './countries.js';

const EMPTY_T = { first_name: '', last_name: '', date_of_birth: '', gender: '', nationality: '', passport_number: '',
  passport_expiry: '', passport_issue_country: '', phone: '', email: '', passenger_type: 'ADT' };
const PAX = { ADT: 'Adult', CNN: 'Child', INF: 'Infant', STU: 'Student' };
const countryList = (() => {
  try {
    const list = Array.isArray(COUNTRIES) ? COUNTRIES : Object.entries(COUNTRIES || {}).map(([code, name]) => ({ code, name }));
    return list.map(c => ({ code: c.code || c.iso2 || c.value, name: c.name || c.label })).filter(c => c.code && c.name);
  } catch { return []; }
})();

function CountrySelect({ value, onChange, id }) {
  // Bookings save 2-letter codes (LK); the site's list is 3-letter — keep
  // whatever is saved editable as a code rather than showing a blank.
  if (!countryList.length || (value && !countryList.some(c => c.code === value))) {
    return <input id={id} className="form-input" maxLength={3} value={value || ''} placeholder="e.g. LK" onChange={e => onChange(e.target.value.toUpperCase())} />;
  }
  return (
    <select id={id} className="form-input" value={value || ''} onChange={e => onChange(e.target.value)}>
      <option value="">—</option>
      {countryList.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
    </select>
  );
}

export default function CustomerProfile({ API_BASE, customerToken, fetchWithRetry, handleApiResponse, onSessionExpired }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [editing, setEditing] = useState(null); // traveller id | 'new' | null
  const [form, setForm] = useState(EMPTY_T);
  const [details, setDetails] = useState(null);
  const [busy, setBusy] = useState(false);

  const call = useCallback(async (path, opts = {}) => {
    const res = await fetchWithRetry(`${API_BASE}/customers/me/profile${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${customerToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Please sign in again.'); }
    return handleApiResponse(res, 'Could not save your profile');
  }, [API_BASE, customerToken, fetchWithRetry, handleApiResponse, onSessionExpired]);

  const apply = (d) => { setData(d); setDetails({ ...d.details, frequent_flyer: d.details.frequent_flyer || [], visas: d.details.visas || [] }); };
  useEffect(() => { call('').then(apply).catch(e => setError(e.message)); }, [call]);

  const run = async (fn, okText) => {
    setBusy(true); setError(''); setMsg('');
    try { apply(await fn()); setMsg(okText); setEditing(null); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };
  const saveTraveller = () => run(() => editing === 'new'
    ? call('/travellers', { method: 'POST', body: JSON.stringify(form) })
    : call(`/travellers/${editing}`, { method: 'PUT', body: JSON.stringify(form) }), 'Traveller saved.');
  const removeTraveller = (t) => run(() => call(`/travellers/${t.id}`, { method: 'DELETE' }), `${t.first_name} ${t.last_name} removed.`);
  const saveDetails = () => run(() => call('/details', { method: 'PUT', body: JSON.stringify(details) }), 'Your details are saved.');

  if (error && !data) return <div className="error-banner">{error}</div>;
  if (!data || !details) return <div className="loading-state"><div className="spinner"></div><p>Loading your profile…</p></div>;

  const field = (key, label, props = {}) => (
    <label className="prof-field">
      <span>{label}</span>
      <input className="form-input" value={form[key] || ''} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} {...props} />
    </label>
  );
  const dfield = (key, label, props = {}) => (
    <label className="prof-field">
      <span>{label}</span>
      <input className="form-input" value={details[key] || ''} onChange={e => setDetails(d => ({ ...d, [key]: e.target.value }))} {...props} />
    </label>
  );
  const listEdit = (key, idx, prop, value) => setDetails(d => ({ ...d, [key]: d[key].map((x, i) => i === idx ? { ...x, [prop]: value } : x) }));

  const travellerForm = (
    <div className="prof-form">
      <div className="prof-grid">
        {field('first_name', 'First name (as in passport) *')}
        {field('last_name', 'Last name (as in passport) *')}
        {field('date_of_birth', 'Date of birth', { type: 'date' })}
        <label className="prof-field"><span>Gender</span>
          <select className="form-input" value={form.gender || ''} onChange={e => setForm(f => ({ ...f, gender: e.target.value }))}>
            <option value="">—</option><option>Male</option><option>Female</option>
          </select></label>
        <label className="prof-field"><span>Traveller type</span>
          <select className="form-input" value={form.passenger_type || 'ADT'} onChange={e => setForm(f => ({ ...f, passenger_type: e.target.value }))}>
            {Object.entries(PAX).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
        <label className="prof-field"><span>Nationality</span>
          <CountrySelect value={form.nationality} onChange={v => setForm(f => ({ ...f, nationality: v }))} /></label>
        {field('passport_number', 'Passport number', { maxLength: 20 })}
        {field('passport_expiry', 'Passport expiry', { type: 'date' })}
        <label className="prof-field"><span>Passport issued by</span>
          <CountrySelect value={form.passport_issue_country} onChange={v => setForm(f => ({ ...f, passport_issue_country: v }))} /></label>
        {field('phone', 'Mobile', { type: 'tel', maxLength: 20 })}
        {field('email', 'Email', { type: 'email' })}
      </div>
      <div className="prof-actions">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={saveTraveller}>{busy ? 'Saving…' : 'Save traveller'}</button>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
      </div>
    </div>
  );

  return (
    <div className="prof">
      {error && <div className="error-banner">{error}</div>}
      {msg && <div className="prof-msg">{msg}</div>}

      <section className="prof-card">
        <div className="prof-head">
          <div>
            <h3>Saved travellers</h3>
            <p>Saved automatically from your bookings, so you don't have to type them again. Keep passport details up to date here.</p>
          </div>
          {editing !== 'new' && (
            <button className="btn btn-secondary btn-sm" onClick={() => { setForm(EMPTY_T); setEditing('new'); setMsg(''); }}>+ Add traveller</button>
          )}
        </div>
        {editing === 'new' && travellerForm}
        {data.travellers.length === 0 && editing !== 'new' && <p className="prof-empty">No travellers yet — they are added when you book while signed in.</p>}
        <div className="prof-list">
          {data.travellers.map(t => (
            <div key={t.id} className="prof-traveller">
              {editing === t.id ? travellerForm : (
                <>
                  <div className="prof-t-main">
                    <strong>{t.first_name} {t.last_name}</strong>
                    <span className="rl-chip muted">{PAX[t.passenger_type] || 'Adult'}</span>
                    {t.source === 'booking' && <span className="prof-src">from your booking</span>}
                  </div>
                  <div className="prof-t-meta">
                    {t.date_of_birth && <span>Born {fmtDate(t.date_of_birth)}</span>}
                    {t.gender && <span>{t.gender}</span>}
                    {t.nationality && <span>{t.nationality}</span>}
                    {t.passport_number && <span>Passport {t.passport_number}{t.passport_expiry ? ` · expires ${fmtDate(t.passport_expiry)}` : ''}</span>}
                    {t.phone && <span>{t.phone}</span>}
                    {t.email && <span>{t.email}</span>}
                  </div>
                  <div className="prof-actions">
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { setForm({ ...EMPTY_T, ...Object.fromEntries(Object.entries(t).map(([k, v]) => [k, v ?? ''])) }); setEditing(t.id); setMsg(''); }}>Edit</button>
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => removeTraveller(t)}>Remove</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="prof-card">
        <div className="prof-head"><div><h3>Other details</h3><p>Keep these up to date yourself — they help us look after your trips.</p></div></div>
        <div className="prof-grid">
          {dfield('nic_number', 'NIC / national ID', { maxLength: 20 })}
          {dfield('address', 'Address', { maxLength: 255 })}
          {dfield('city', 'City', { maxLength: 80 })}
          {dfield('country', 'Country', { maxLength: 60 })}
          {dfield('emergency_name', 'Emergency contact name', { maxLength: 120 })}
          {dfield('emergency_phone', 'Emergency contact phone', { type: 'tel', maxLength: 20 })}
          <label className="prof-field"><span>Meal preference</span>
            <select className="form-input" value={details.meal_preference || ''} onChange={e => setDetails(d => ({ ...d, meal_preference: e.target.value }))}>
              <option value="">No preference</option>
              {['Vegetarian', 'Vegan', 'Hindu (no beef)', 'Muslim (halal)', 'Kosher', 'Diabetic', 'Gluten-free', 'Child meal'].map(m => <option key={m}>{m}</option>)}
            </select></label>
          <label className="prof-field"><span>Seat preference</span>
            <select className="form-input" value={details.seat_preference || ''} onChange={e => setDetails(d => ({ ...d, seat_preference: e.target.value }))}>
              <option value="">No preference</option><option>Window</option><option>Aisle</option>
            </select></label>
        </div>

        <h4 className="prof-sub">Frequent flyer numbers</h4>
        {details.frequent_flyer.map((x, i) => (
          <div key={i} className="prof-row">
            <input className="form-input" placeholder="Airline (e.g. SriLankan FlySmiLes)" value={x.airline || ''} onChange={e => listEdit('frequent_flyer', i, 'airline', e.target.value)} />
            <input className="form-input" placeholder="Membership number" value={x.number || ''} onChange={e => listEdit('frequent_flyer', i, 'number', e.target.value)} />
            <button className="btn btn-secondary btn-sm" onClick={() => setDetails(d => ({ ...d, frequent_flyer: d.frequent_flyer.filter((_, j) => j !== i) }))}>Remove</button>
          </div>
        ))}
        <button className="btn btn-secondary btn-sm prof-add" onClick={() => setDetails(d => ({ ...d, frequent_flyer: [...d.frequent_flyer, { airline: '', number: '' }] }))}>+ Add frequent flyer number</button>

        <h4 className="prof-sub">Visas</h4>
        {details.visas.map((x, i) => (
          <div key={i} className="prof-row">
            <input className="form-input" placeholder="Country" value={x.country || ''} onChange={e => listEdit('visas', i, 'country', e.target.value)} />
            <input className="form-input" placeholder="Visa type (e.g. Tourist)" value={x.type || ''} onChange={e => listEdit('visas', i, 'type', e.target.value)} />
            <input className="form-input" placeholder="Visa number" value={x.number || ''} onChange={e => listEdit('visas', i, 'number', e.target.value)} />
            <input className="form-input" type="date" title="Expiry" value={x.expiry || ''} onChange={e => listEdit('visas', i, 'expiry', e.target.value)} />
            <button className="btn btn-secondary btn-sm" onClick={() => setDetails(d => ({ ...d, visas: d.visas.filter((_, j) => j !== i) }))}>Remove</button>
          </div>
        ))}
        <button className="btn btn-secondary btn-sm prof-add" onClick={() => setDetails(d => ({ ...d, visas: [...d.visas, { country: '', type: '', number: '', expiry: '' }] }))}>+ Add visa</button>

        <label className="prof-field prof-notes"><span>Anything else we should know</span>
          <textarea className="form-input" rows={2} maxLength={500} value={details.notes || ''} onChange={e => setDetails(d => ({ ...d, notes: e.target.value }))} /></label>
        <div className="prof-actions">
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={saveDetails}>{busy ? 'Saving…' : 'Save details'}</button>
        </div>
      </section>
    </div>
  );
}
