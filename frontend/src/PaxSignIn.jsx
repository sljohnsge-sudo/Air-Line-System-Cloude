// Sign-in on the Passenger Details step. When the step opens for a customer
// who is not signed in, a pop-up asks existing customers to sign in instead
// of typing their details (their details from earlier bookings then fill the
// form), offers new customers a free account, or lets them continue as a
// guest. Passenger 1 is filled automatically with the account holder; any
// passenger can be filled from the "Fill from a previous traveller" list.
// Signed in, the bar offers Sign out (the details already typed stay).
// Special requests (meal / wheelchair) are never copied — the backend leaves
// them out and so does onFill.
import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const TYPE_LABEL = { ADT: 'Adult', CNN: 'Child', INF: 'Infant', STU: 'Student' };
const SEEN_KEY = 'tb_pax_signin_prompt_seen';

// "aaa@gamil.com" -> "aaa@gmail.com": a likely typo in a common email
// domain (one or two letters off), offered as a one-click fix.
const COMMON_DOMAINS = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'live.com', 'msn.com', 'aol.com', 'protonmail.com', 'ymail.com', 'sltnet.lk'];
const editDistance = (a, b) => {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return d[a.length][b.length];
};
export const emailTypoFix = (email) => {
  const m = /^([^@\s]+)@([^@\s]+\.[^@\s]+)$/.exec((email || '').trim().toLowerCase());
  if (!m || COMMON_DOMAINS.includes(m[2])) return null;
  const best = COMMON_DOMAINS.map(d => [d, editDistance(m[2], d)]).sort((x, y) => x[1] - y[1])[0];
  return best && best[1] <= 2 ? `${m[1]}@${best[0]}` : null;
};

const promptSeen = () => { try { return sessionStorage.getItem(SEEN_KEY) === '1'; } catch { return false; } };
const markPromptSeen = () => { try { sessionStorage.setItem(SEEN_KEY, '1'); } catch { /* storage off */ } };

export default function PaxSignIn({ apiBase, customerToken, customerProfile, onLogin, onLogout, travelers, activeIdx, onFill }) {
  // Opens by itself once per visit for a guest; the bar's button reopens it.
  const [open, setOpen] = useState(() => !customerToken && !promptSeen());
  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(null); // null = not loaded yet
  const autoFilled = useRef(false);

  // Previous travellers of the signed-in customer, fresh on every sign-in.
  useEffect(() => {
    if (!customerToken) { setSaved(null); return; }
    let live = true;
    fetch(`${apiBase}/customers/me/saved-travellers`, { headers: { Authorization: `Bearer ${customerToken}` } })
      .then(r => (r.ok ? r.json() : { travellers: [] }))
      .then(d => { if (live) setSaved(d.travellers || []); })
      .catch(() => { if (live) setSaved([]); });
    return () => { live = false; };
  }, [apiBase, customerToken]);

  // Passenger 1 is filled once with the account holder (or the most recent
  // traveller) — only while that passenger's form is still empty. A new
  // account with no trips yet gets at least its email filled in.
  useEffect(() => {
    if (autoFilled.current || saved === null || !travelers?.length) return;
    autoFilled.current = true;
    const first = travelers[0] || {};
    if (first.first_name) return;
    const me = (customerProfile?.email || '').toLowerCase();
    const slotType = first.passenger_type || 'ADT';
    const pick = saved.find(t => me && (t.email || '').toLowerCase() === me && t.passenger_type === slotType)
      || saved.find(t => t.passenger_type === slotType);
    if (pick) onFill(0, pick);
    else if (me && !first.email) onFill(0, { email: customerProfile.email });
  }, [saved, travelers, customerProfile, onFill]);

  const close = () => { markPromptSeen(); setOpen(false); setError(''); };

  const submit = async () => {
    if (!email || !password || (mode === 'register' && !fullName.trim())) {
      setError(mode === 'register' ? 'Enter your name, email and a password.' : 'Enter your email and password.');
      return;
    }
    if (mode === 'register' && password.length < 6) { setError('Use at least 6 characters for the password.'); return; }
    setBusy(true); setError('');
    try {
      const res = await fetch(`${apiBase}/customers/${mode === 'register' ? 'register' : 'login'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mode === 'register' ? { email, password, full_name: fullName.trim() } : { email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail
          : mode === 'register' ? 'Check your name, email address and password.' : 'Check your email address and password.');
      }
      autoFilled.current = false;
      onLogin(data.access_token, data.customer);
      setPassword('');
      close();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const popup = open && !customerToken && createPortal(
    <div className="pax-signin-overlay" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}>
      <div className="pax-signin-modal" role="dialog" aria-modal="true" aria-labelledby="pax-signin-title"
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } if (e.key === 'Escape') close(); }}>
        <button type="button" className="pax-signin-x" onClick={close} aria-label="Close">×</button>
        {mode === 'login' ? (
          <>
            <h3 id="pax-signin-title">Already a Tick and Book customer?</h3>
            <p className="pax-signin-lead">
              Please sign in instead of filling in the form — your passenger details will be filled in automatically from your earlier trips.
            </p>
          </>
        ) : (
          <>
            <h3 id="pax-signin-title">Create your free account</h3>
            <p className="pax-signin-lead">One account for all your trips:</p>
            <ul className="pax-signin-benefits">
              <li>Your details fill in by themselves next time you book</li>
              <li>See and manage all your bookings in one place</li>
              <li>Collect rewards with our Loyalty Program</li>
            </ul>
          </>
        )}
        {mode === 'register' && (
          <input className="form-input" placeholder="Full name" autoComplete="name" value={fullName} onChange={e => setFullName(e.target.value)} />
        )}
        <input type="email" className="form-input" placeholder="Email address" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} />
        {emailTypoFix(email) && (
          <div className="pax-signin-typo">
            Did you mean <button type="button" onClick={() => { setEmail(emailTypoFix(email)); setError(''); }}>{emailTypoFix(email)}</button>?
          </div>
        )}
        <input type="password" className="form-input" placeholder={mode === 'register' ? 'Choose a password (6+ characters)' : 'Password'}
          autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} />
        {error && <div className="pax-signin-error">{error}</div>}
        <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>
          {busy ? 'Please wait…' : mode === 'register' ? 'Create account & continue' : 'Sign in & fill my details'}
        </button>
        <div className="pax-signin-switch">
          {mode === 'login' ? (
            <>New to Tick and Book? <button type="button" onClick={() => { setMode('register'); setError(''); }}>Create a free account</button> for more benefits.</>
          ) : (
            <>Already have an account? <button type="button" onClick={() => { setMode('login'); setError(''); }}>Sign in</button></>
          )}
        </div>
        <button type="button" className="pax-signin-guest" onClick={close}>Continue as guest</button>
      </div>
    </div>,
    document.body,
  );

  if (customerToken) {
    return (
      <div className="pax-signin">
        <span className="pax-signin-who">Signed in{customerProfile?.full_name ? ` as ${customerProfile.full_name}` : ''}</span>
        {saved?.length > 0 && (
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
        {saved?.length === 0 && <span className="pax-signin-note">· no earlier trips to fill from yet</span>}
        <button type="button" className="pax-signin-btn" onClick={() => { autoFilled.current = false; onLogout(); }}>Sign out</button>
      </div>
    );
  }

  return (
    <div className="pax-signin">
      <span className="pax-signin-who">Travelled with us before?</span>
      <button type="button" className="pax-signin-btn" onClick={() => { setMode('login'); setOpen(true); }}>Sign in</button>
      {popup}
    </div>
  );
}
