import { useState, useEffect } from 'react';
import BookingCard from './BookingCard.jsx';

export default function CustomerPortal({ customerToken, customerProfile, onCustomerLogin, onCustomerLogout, API_BASE, fetchWithRetry, handleApiResponse, onViewTicket, onOpenInvoice, onRequestCancellation }) {
  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [phone, setPhone] = useState('');
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const [bookings, setBookings] = useState([]);
  const [loadingBookings, setLoadingBookings] = useState(false);
  const [bookingsError, setBookingsError] = useState('');

  const [loyalty, setLoyalty] = useState(null);

  const [emailRequest, setEmailRequest] = useState(null);
  const [newEmail, setNewEmail] = useState('');
  const [emailFormError, setEmailFormError] = useState('');
  const [emailFormMsg, setEmailFormMsg] = useState('');
  const [emailFormOpen, setEmailFormOpen] = useState(false);
  const [emailSubmitting, setEmailSubmitting] = useState(false);

  const loadBookings = async () => {
    setLoadingBookings(true);
    setBookingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/bookings`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to load your bookings');
      setBookings(data.bookings || []);
    } catch (err) {
      setBookingsError(err.message);
    } finally {
      setLoadingBookings(false);
    }
  };

  const loadLoyalty = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/loyalty`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to load loyalty status');
      setLoyalty(data);
    } catch { /* non-critical, ignore */ }
  };

  const loadEmailRequest = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/email-change-request`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to load email change status');
      setEmailRequest(data.request || null);
    } catch { /* non-critical, ignore */ }
  };

  useEffect(() => {
    if (customerToken) {
      loadBookings();
      loadLoyalty();
      loadEmailRequest();
    }
  }, [customerToken]);

  const submitEmailChange = async (e) => {
    e.preventDefault();
    setEmailFormError('');
    setEmailFormMsg('');
    setEmailSubmitting(true);
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/email-change-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${customerToken}` },
        body: JSON.stringify({ new_email: newEmail }),
      });
      const data = await handleApiResponse(res, 'Failed to submit email change request');
      setEmailRequest(data);
      setEmailFormMsg('Request submitted — an admin will review it shortly.');
      setNewEmail('');
      setEmailFormOpen(false);
    } catch (err) {
      setEmailFormError(err.message);
    } finally {
      setEmailSubmitting(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError('');
    setSubmitting(true);
    try {
      const endpoint = mode === 'login' ? 'login' : 'register';
      const body = mode === 'login'
        ? { email, password }
        : { email, password, full_name: fullName, phone: phone || undefined };
      const res = await fetchWithRetry(`${API_BASE}/customers/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await handleApiResponse(res, mode === 'login' ? 'Login failed' : 'Registration failed');
      onCustomerLogin(data.access_token, data.customer);
    } catch (err) {
      setFormError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (!customerToken) {
    return (
      <div className="tab-content animate-fade" style={{ maxWidth: '420px', margin: '3rem auto' }}>
        <section className="search-section glass-panel">
          <div style={{ display: 'flex', background: '#f1f5f9', borderRadius: '8px', padding: '0.2rem', gap: '0.2rem', marginBottom: '1.25rem' }}>
            <button onClick={() => { setMode('login'); setFormError(''); }} style={{
              flex: 1, border: 'none', background: mode === 'login' ? 'white' : 'transparent',
              color: mode === 'login' ? '#0f172a' : '#64748b', fontSize: '0.8rem', fontWeight: '700',
              padding: '0.5rem', borderRadius: '6px', cursor: 'pointer',
            }}>Sign In</button>
            <button onClick={() => { setMode('register'); setFormError(''); }} style={{
              flex: 1, border: 'none', background: mode === 'register' ? 'white' : 'transparent',
              color: mode === 'register' ? '#0f172a' : '#64748b', fontSize: '0.8rem', fontWeight: '700',
              padding: '0.5rem', borderRadius: '6px', cursor: 'pointer',
            }}>Create Account</button>
          </div>

          <h2 className="section-title">{mode === 'login' ? 'Customer Sign In' : 'Create Your Account'}</h2>
          {formError && <div className="error-banner">{formError}</div>}
          <form onSubmit={handleSubmit}>
            {mode === 'register' && (
              <div className="form-group">
                <label className="form-label">Full Name *</label>
                <input className="form-input" required value={fullName} onChange={e => setFullName(e.target.value)} />
              </div>
            )}
            <div className="form-group">
              <label className="form-label">Email Address *</label>
              <input type="email" className="form-input" required value={email} onChange={e => setEmail(e.target.value)} />
            </div>
            {mode === 'register' && (
              <div className="form-group">
                <label className="form-label">Phone</label>
                <input type="tel" className="form-input" value={phone} onChange={e => setPhone(e.target.value)} />
              </div>
            )}
            <div className="form-group">
              <label className="form-label">Password *</label>
              <input type="password" className="form-input" required minLength={6} value={password} onChange={e => setPassword(e.target.value)} />
            </div>
            <button type="submit" className="btn btn-primary" disabled={submitting} style={{ width: '100%', marginTop: '0.5rem' }}>
              {submitting ? 'Please wait…' : mode === 'login' ? 'Sign In' : 'Create Account'}
            </button>
          </form>
          {mode === 'register' && (
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '1rem' }}>
              Bookings you make while signed in, and any past bookings made under this email address, will show up on your account.
            </p>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="tab-content animate-fade">
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div>
            <h2 className="section-title" style={{ margin: 0 }}>Welcome, {customerProfile?.full_name}</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '0.25rem 0 0' }}>
              {customerProfile?.email}
              {!emailRequest && (
                <button onClick={() => { setEmailFormOpen(o => !o); setEmailFormMsg(''); setEmailFormError(''); }}
                  style={{ marginLeft: '0.6rem', border: 'none', background: 'none', color: 'var(--gs-crimson)', fontSize: '0.78rem', fontWeight: '700', cursor: 'pointer', padding: 0 }}>
                  Change Email
                </button>
              )}
            </p>
            {emailRequest && (
              <p style={{ fontSize: '0.78rem', color: '#b45309', margin: '0.35rem 0 0', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                ⏳ Email change to <strong>{emailRequest.new_email}</strong> pending admin approval.
              </p>
            )}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-secondary btn-sm" onClick={onOpenInvoice}>🔍 Look Up a Booking (PNR/Ticket)</button>
            <button className="btn btn-secondary btn-sm" onClick={onCustomerLogout}>Log Out</button>
          </div>
        </div>

        {emailFormOpen && !emailRequest && (
          <form onSubmit={submitEmailChange} style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px dashed var(--border-color)', display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '220px' }}>
              <label className="form-label">New Email Address</label>
              <input type="email" className="form-input" required value={newEmail} onChange={e => setNewEmail(e.target.value)} />
            </div>
            <button type="submit" className="btn btn-primary" disabled={emailSubmitting}>
              {emailSubmitting ? 'Submitting…' : 'Submit Request'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setEmailFormOpen(false)}>Cancel</button>
            {emailFormError && <div className="error-banner" style={{ width: '100%' }}>{emailFormError}</div>}
          </form>
        )}
        {emailFormMsg && <p style={{ fontSize: '0.8rem', color: '#166534', marginTop: '0.75rem' }}>{emailFormMsg}</p>}
        {!emailFormOpen && (
          <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
            Email changes go through an admin review before taking effect.
          </p>
        )}
      </section>

      {loyalty && (
        <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', flexWrap: 'wrap' }}>
            <div style={{
              width: '56px', height: '56px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '1.4rem', flexShrink: 0,
              background: loyalty.tier === 'Platinum' ? 'linear-gradient(135deg,#e5e9f0,#7c8ba1)' : loyalty.tier === 'Gold' ? 'linear-gradient(135deg,#fde047,#ca8a04)' : loyalty.tier === 'Silver' ? 'linear-gradient(135deg,#e2e8f0,#94a3b8)' : 'linear-gradient(135deg,#d6a679,#92582f)',
            }}>
              {loyalty.tier === 'Platinum' ? '💎' : loyalty.tier === 'Gold' ? '🥇' : loyalty.tier === 'Silver' ? '🥈' : '🥉'}
            </div>
            <div style={{ flex: 1, minWidth: '200px' }}>
              <div style={{ fontWeight: '800', fontSize: '1.1rem', color: 'var(--gs-dark)' }}>
                {loyalty.tier} Member — {loyalty.points_balance.toLocaleString()} pts
              </div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                {loyalty.next_tier
                  ? `${loyalty.points_to_next_tier.toLocaleString()} points to ${loyalty.next_tier}`
                  : 'You’ve reached the top tier'}
              </div>
            </div>
          </div>
          {loyalty.recent_transactions?.length > 0 && (
            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px dashed var(--border-color)' }}>
              <div style={{ fontSize: '0.72rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Recent Activity</div>
              {loyalty.recent_transactions.slice(0, 5).map(t => (
                <div key={t.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', padding: '0.3rem 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{t.reason}</span>
                  <span style={{ fontWeight: '700', color: t.points_change >= 0 ? '#166534' : '#991b1b' }}>{t.points_change >= 0 ? '+' : ''}{t.points_change}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="bookings-list-section">
        <h3 className="results-heading">
          {loadingBookings ? 'Loading...' : `My Bookings (${bookings.length})`}
        </h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '-0.5rem 0 1rem' }}>
          Bookings made while signed in, plus any past bookings made under this account's email address.
          Booked under a different email? Use "Look Up a Booking" above with the PNR or ticket number instead.
        </p>
        {bookingsError && <div className="error-banner">{bookingsError}</div>}
        {loadingBookings ? (
          <div className="loading-state"><div className="spinner"></div><p>Retrieving your bookings...</p></div>
        ) : bookings.length === 0 ? (
          <div className="empty-state glass-panel animate-fade">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2 17h20M2 12h20M2 7h20"/></svg>
            <p>No bookings yet under this account. Bookings you make while signed in, or made previously under this account's email, will appear here.</p>
          </div>
        ) : (
          <div className="bookings-grid">
            {bookings.map((b) => (
              <BookingCard key={b.id} booking={b} onViewTicket={onViewTicket} onRequestCancellation={onRequestCancellation} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
