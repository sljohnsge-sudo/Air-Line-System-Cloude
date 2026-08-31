import { useState, useEffect } from 'react';
import BookingCard from './BookingCard.jsx';

export default function CustomerPortal({ customerToken, customerProfile, onCustomerLogin, onCustomerLogout, API_BASE, fetchWithRetry, handleApiResponse, onViewTicket }) {
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

  useEffect(() => {
    if (customerToken) loadBookings();
  }, [customerToken]);

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
              Your account will only show bookings you make while signed in — it will not show any earlier guest bookings.
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
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '0.25rem 0 0' }}>{customerProfile?.email}</p>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onCustomerLogout}>Log Out</button>
        </div>
      </section>

      <section className="bookings-list-section">
        <h3 className="results-heading">
          {loadingBookings ? 'Loading...' : `My Bookings (${bookings.length})`}
        </h3>
        {bookingsError && <div className="error-banner">{bookingsError}</div>}
        {loadingBookings ? (
          <div className="loading-state"><div className="spinner"></div><p>Retrieving your bookings...</p></div>
        ) : bookings.length === 0 ? (
          <div className="empty-state glass-panel animate-fade">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M2 17h20M2 12h20M2 7h20"/></svg>
            <p>No bookings yet under this account. Bookings you make while signed in will appear here.</p>
          </div>
        ) : (
          <div className="bookings-grid">
            {bookings.map((b) => (
              <BookingCard key={b.id} booking={b} onViewTicket={onViewTicket} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
