// Customer account › My Profile › Offers & notifications (11/11/2026).
// The customer switches special notifications on, picks what (offers,
// airline deals, hotel deals, fare drops) and how (email, SMS, WhatsApp), and
// lists favourite destinations, airlines and hotels. Staff send special
// offers to matching customers (Admin › Offers & Interests).
// Backend: services/customer_interests.py.
import { useEffect, useState } from 'react';

const POPULAR = {
  destinations: ['Dubai', 'Maldives', 'Singapore', 'Bangkok', 'London', 'India', 'Thailand', 'Australia', 'Japan', 'Saudi Arabia'],
  airlines: ['SriLankan Airlines', 'Emirates', 'Qatar Airways', 'Etihad Airways', 'Singapore Airlines', 'Air India', 'Thai Airways', 'Cathay Pacific', 'Malaysia Airlines', 'Gulf Air'],
  hotels: [],
};

function TagInput({ label, help, items, onChange, suggestions = [], placeholder }) {
  const [text, setText] = useState('');
  const add = (v) => {
    const t = (v || '').trim();
    if (!t || items.some(x => x.toLowerCase() === t.toLowerCase()) || items.length >= 15) return;
    onChange([...items, t]); setText('');
  };
  return (
    <div className="ci-field">
      <span className="ci-label">{label}</span>
      {help && <small className="ci-help">{help}</small>}
      <div className="ci-tags">
        {items.map(x => (
          <span key={x} className="ci-tag">{x}<button type="button" aria-label={`Remove ${x}`} onClick={() => onChange(items.filter(y => y !== x))}>×</button></span>
        ))}
        <input className="ci-tag-input" value={text} placeholder={placeholder}
               onChange={e => setText(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(text); } }}
               onBlur={() => add(text)} />
      </div>
      {suggestions.filter(s => !items.some(x => x.toLowerCase() === s.toLowerCase())).length > 0 && (
        <div className="ci-suggest">
          {suggestions.filter(s => !items.some(x => x.toLowerCase() === s.toLowerCase())).slice(0, 8).map(s => (
            <button key={s} type="button" onClick={() => add(s)}>+ {s}</button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CustomerInterests({ API_BASE, customerToken, fetchWithRetry, handleApiResponse, onSessionExpired }) {
  const [v, setV] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const call = async (opts = {}) => {
    const res = await fetchWithRetry(`${API_BASE}/customers/me/interests`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${customerToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Please sign in again.'); }
    return handleApiResponse(res, 'Could not save your preferences');
  };
  useEffect(() => { call().then(setV).catch(e => setErr(e.message)); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!v) return err ? <div className="error-banner">{err}</div> : null;
  const toggle = (key, value) => setV(s => ({ ...s, [key]: s[key].includes(value) ? s[key].filter(x => x !== value) : [...s[key], value] }));
  const save = async () => {
    setBusy(true); setMsg(''); setErr('');
    try {
      const d = await call({ method: 'PUT', body: JSON.stringify(v) });
      setV(d); setMsg(d.opt_in ? 'Saved — we will send you special offers for your favourites.' : 'Saved — special notifications are off.');
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <section className="prof-card ci">
      <div className="prof-head">
        <div>
          <h3>Offers &amp; notifications</h3>
          <p>Tell us what you love and we'll let you know about special fares, airline and hotel deals for your favourites.</p>
        </div>
      </div>
      {msg && <div className="prof-msg">{msg}</div>}
      {err && <div className="error-banner">{err}</div>}

      <label className={`ci-switch${v.opt_in ? ' on' : ''}`}>
        <input type="checkbox" checked={v.opt_in} onChange={e => setV(s => ({ ...s, opt_in: e.target.checked }))} />
        <span className="ci-switch-track" aria-hidden="true"><span /></span>
        <span><strong>Send me special notifications</strong><small>You can switch this off at any time.</small></span>
      </label>

      <div className={`ci-body${v.opt_in ? '' : ' ci-dim'}`}>
        <div className="ci-row">
          <div className="ci-field">
            <span className="ci-label">What would you like to hear about?</span>
            <div className="ci-checks">
              {Object.entries(v.topic_labels).map(([k, l]) => (
                <label key={k} className={`ci-check${v.topics.includes(k) ? ' on' : ''}`}>
                  <input type="checkbox" checked={v.topics.includes(k)} disabled={!v.opt_in} onChange={() => toggle('topics', k)} />{l}
                </label>
              ))}
            </div>
          </div>
          <div className="ci-field">
            <span className="ci-label">How should we tell you?</span>
            <div className="ci-checks">
              {Object.entries(v.channel_labels).map(([k, l]) => (
                <label key={k} className={`ci-check${v.channels.includes(k) ? ' on' : ''}`}>
                  <input type="checkbox" checked={v.channels.includes(k)} disabled={!v.opt_in} onChange={() => toggle('channels', k)} />{l}
                </label>
              ))}
            </div>
          </div>
        </div>
        <TagInput label="Favourite destinations" help="Countries or cities — press Enter after each" items={v.destinations}
                  onChange={x => setV(s => ({ ...s, destinations: x }))} suggestions={POPULAR.destinations} placeholder="e.g. Dubai, Maldives" />
        <TagInput label="Favourite airlines" items={v.airlines} onChange={x => setV(s => ({ ...s, airlines: x }))}
                  suggestions={POPULAR.airlines} placeholder="e.g. Emirates" />
        <TagInput label="Favourite hotels or hotel brands" items={v.hotels} onChange={x => setV(s => ({ ...s, hotels: x }))}
                  placeholder="e.g. Shangri-La, Cinnamon" />
        <label className="ci-field">
          <span className="ci-label">Anything else you'd like offers for</span>
          <input className="form-input" maxLength={300} value={v.other} placeholder="e.g. honeymoon packages, business class upgrades"
                 onChange={e => setV(s => ({ ...s, other: e.target.value }))} />
        </label>
      </div>
      <div className="prof-actions">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save preferences'}</button>
      </div>
    </section>
  );
}
