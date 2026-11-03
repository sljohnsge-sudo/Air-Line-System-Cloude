// Sign-in on the Passenger Details step. A customer who has travelled with us
// before signs in here (email + password, without leaving the booking) and
// their details from earlier bookings fill the form: passenger 1 is filled
// automatically with the account holder, any passenger can be filled from the
// "Fill from a previous traveller" list. Special requests (meal / wheelchair)
// are never copied — the backend leaves them out and so does onFill.
import React, { useState, useEffect, useRef } from 'react';

const TYPE_LABEL = { ADT: 'Adult', CNN: 'Child', INF: 'Infant', STU: 'Student' };

export default function PaxSignIn({ apiBase, customerToken, customerProfile, onLogin, travelers, activeIdx, onFill }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState([]);
  const autoFilled = useRef(false);

  // Previous travellers of the signed-in customer, fresh on every sign-in.
  useEffect(() => {
    if (!customerToken) { setSaved([]); return; }
    let live = true;
    fetch(`${apiBase}/customers/me/saved-travellers`, { headers: { Authorization: `Bearer ${customerToken}` } })
      .then(r => (r.ok ? r.json() : { travellers: [] }))
      .then(d => { if (live) setSaved(d.travellers || []); })
      .catch(() => { if (live) setSaved([]); });
    return () => { live = false; };
  }, [apiBase, customerToken]);

  // Passenger 1 is filled once with the account holder (or the most recent
  // traveller) — only while that passenger's form is still empty.
  useEffect(() => {
    if (autoFilled.current || !saved.length || !travelers?.length) return;
    autoFilled.current = true;
    if (travelers[0]?.first_name) return;
    const me = (customerProfile?.email || '').toLowerCase();
    const slotType = travelers[0]?.passenger_type || 'ADT';
    const pick = saved.find(t => me && (t.email || '').toLowerCase() === me && t.passenger_type === slotType)
      || saved.find(t => t.passenger_type === slotType);
    if (pick) onFill(0, pick);
  }, [saved, travelers, customerProfile, onFill]);

  const signIn = async () => {
    if (!email || !password) { setError('Enter your email and password.'); return; }
    setBusy(true); setError('');
    try {
      const res = await fetch(`${apiBase}/customers/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your email address and password.');
      autoFilled.current = false;
      onLogin(data.access_token, data.customer);
      setOpen(false); setPassword('');
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (customerToken) {
    return (
      <div className="pax-signin">
        <span className="pax-signin-who">Signed in{customerProfile?.full_name ? ` as ${customerProfile.full_name}` : ''}</span>
        {saved.length > 0 && (
          <select
            className="pax-signin-fill"
            value=""
            onChange={e => { const t = saved[Number(e.target.value)]; if (t) onFill(activeIdx, t); }}
          >
            <option value="">Fill from a previous traveller…</option>
            {saved.map((t, i) => (
              <option key={i} value={i}>{t.first_name} {t.last_name}{t.passenger_type && t.passenger_type !== 'ADT' ? ` (${TYPE_LABEL[t.passenger_type] || t.passenger_type})` : ''}</option>
            ))}
          </select>
        )}
      </div>
    );
  }

  return (
    <div className="pax-signin">
      <span className="pax-signin-who">Travelled with us before?</span>
      <button type="button" className="pax-signin-btn" onClick={() => setOpen(v => !v)}>Sign in</button>
      {open && (
        <div className="pax-signin-pop" onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); signIn(); } }}>
          <div className="pax-signin-title">Sign in to fill your details</div>
          <input type="email" className="form-input" placeholder="Email address" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} />
          <input type="password" className="form-input" placeholder="Password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
          {error && <div className="pax-signin-error">{error}</div>}
          <button type="button" className="btn btn-primary" disabled={busy} onClick={signIn}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </div>
      )}
    </div>
  );
}
