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

// Google / Microsoft sign-in (16/11/2026), the same as the customer login
// page. They leave the site, so App keeps the booking (flight, passengers,
// search) in this tab first and brings the customer back to this step.
const GoogleIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"/>
    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
    <path fill="#4CAF50" d="M24 44c5.5 0 10.4-2.1 14.1-5.6l-6.5-5.5C29.5 34.5 26.9 35.5 24 35.5c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.6 39.6 16.3 44 24 44z"/>
    <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.2 4.2-4.1 5.6l6.5 5.5C41.3 36 44 30.5 44 24c0-1.3-.1-2.7-.4-3.5z"/>
  </svg>
);
const MicrosoftIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 23 23" aria-hidden="true">
    <path fill="#f25022" d="M1 1h10v10H1z"/><path fill="#00a4ef" d="M1 12h10v10H1z"/>
    <path fill="#7fba00" d="M12 1h10v10H12z"/><path fill="#ffb900" d="M12 12h10v10H12z"/>
  </svg>
);
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

export default function PaxSignIn({ apiBase, customerToken, customerProfile, onLogin, onLogout, travelers, activeIdx, onFill, onOAuth }) {
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
        {onOAuth && (
          <>
            <div className="pax-oauth-row">
              <button type="button" className="pax-oauth-wide" disabled={busy} onClick={() => { markPromptSeen(); onOAuth('google'); }}>
                <GoogleIcon size={18} /> Continue with Google
              </button>
              <button type="button" className="pax-oauth-wide" disabled={busy} onClick={() => { markPromptSeen(); onOAuth('microsoft'); }}>
                <MicrosoftIcon size={18} /> Continue with Microsoft
              </button>
            </div>
            <div className="pax-oauth-note">You come straight back here, with your flight and details kept.</div>
            <div className="pax-oauth-or">or with your Tick and Book account</div>
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
            <option value="">Fill from a saved traveller (My Profile)…</option>
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
      {onOAuth && (
        <>
          <button type="button" className="pax-signin-btn pax-oauth" title="Sign in with Google" onClick={() => { markPromptSeen(); onOAuth('google'); }}>
            <GoogleIcon /> <span>Google</span>
          </button>
          <button type="button" className="pax-signin-btn pax-oauth" title="Sign in with Microsoft" onClick={() => { markPromptSeen(); onOAuth('microsoft'); }}>
            <MicrosoftIcon /> <span>Microsoft</span>
          </button>
        </>
      )}
      <button type="button" className="pax-signin-btn" onClick={() => { setMode('login'); setOpen(true); }}>Sign in</button>
      {popup}
    </div>
  );
}
