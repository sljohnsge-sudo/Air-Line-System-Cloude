import { useState, useEffect } from 'react';
import BookingCard from './BookingCard.jsx';
import { COUNTRIES } from './countries.js';

export default function CustomerPortal({ customerToken, customerProfile, onCustomerLogin, onCustomerLogout, API_BASE, fetchWithRetry, handleApiResponse, onViewTicket, onOpenInvoice, onRequestCancellation, visaEnabled }) {
  const [activeSection, setActiveSection] = useState('bookings');

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
  const [emailSubmitting, setEmailSubmitting] = useState(false);

  const [cancellationRequests, setCancellationRequests] = useState([]);

  const [visaNationality, setVisaNationality] = useState('');
  const [visaDestination, setVisaDestination] = useState('');
  const [visaResult, setVisaResult] = useState(null);
  const [visaError, setVisaError] = useState('');
  const [visaChecking, setVisaChecking] = useState(false);
  const [visaHistory, setVisaHistory] = useState([]);

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

  const loadCancellationRequests = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/cancellation-requests`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to load cancellation requests');
      setCancellationRequests(data.requests || []);
    } catch { /* non-critical, ignore */ }
  };

  const loadVisaHistory = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/visa-history`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to load visa consultation history');
      setVisaHistory(data.consultations || []);
    } catch { /* non-critical, ignore */ }
  };

  useEffect(() => {
    if (customerToken) {
      loadBookings();
      loadLoyalty();
      loadEmailRequest();
      loadCancellationRequests();
      if (visaEnabled) loadVisaHistory();
    }
  }, [customerToken]);

  const checkVisaStatus = async (e) => {
    e.preventDefault();
    setVisaError('');
    setVisaResult(null);
    setVisaChecking(true);
    try {
      const params = new URLSearchParams({ nationality: visaNationality, destination: visaDestination });
      const res = await fetchWithRetry(`${API_BASE}/customers/me/visa-status?${params}`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      const data = await handleApiResponse(res, 'Failed to check visa status');
      setVisaResult(data);
    } catch (err) {
      setVisaError(err.message);
    } finally {
      setVisaChecking(false);
    }
  };

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

  const customerNavItems = [
    { id: 'bookings', label: 'My Bookings', count: bookings.length },
    { id: 'loyalty', label: 'Loyalty Program' },
    ...(visaEnabled ? [{ id: 'visa', label: 'Visa Status' }] : []),
    { id: 'cancellations', label: 'Cancellation Requests', count: cancellationRequests.length },
    { id: 'account', label: 'Account Settings' },
  ];

  return (
    <div className="tab-content animate-fade">
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div>
            <h2 className="section-title" style={{ margin: 0 }}>Welcome, {customerProfile?.full_name}</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '0.25rem 0 0' }}>{customerProfile?.email}</p>
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
      </section>

      <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>
        <nav style={{ width: '220px', flexShrink: 0, position: 'sticky', top: '1rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          {customerNavItems.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveSection(item.id)}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', textAlign: 'left',
                padding: '0.6rem 0.8rem', borderRadius: '8px', border: 'none', cursor: 'pointer',
                fontSize: '0.85rem', fontWeight: activeSection === item.id ? 700 : 500,
                background: activeSection === item.id ? 'var(--gs-crimson)' : 'transparent',
                color: activeSection === item.id ? 'white' : 'var(--gs-dark)',
              }}
            >
              <span>{item.label}</span>
              {item.count > 0 && (
                <span style={{
                  fontSize: '0.7rem', fontWeight: 700, padding: '0.05rem 0.45rem', borderRadius: '999px',
                  background: activeSection === item.id ? 'rgba(255,255,255,0.25)' : '#fef2f2',
                  color: activeSection === item.id ? 'white' : '#991b1b',
                }}>
                  {item.count}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div style={{ flex: 1, minWidth: 0 }}>
          {activeSection === 'bookings' && (
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
          )}

          {activeSection === 'loyalty' && (
            <section className="search-section glass-panel">
              <h3 className="results-heading" style={{ marginTop: 0 }}>Loyalty Program</h3>
              {!loyalty ? (
                <div className="loading-state"><div className="spinner"></div><p>Loading your loyalty status...</p></div>
              ) : (
                <>
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
                  {(loyalty.tier_benefits?.length > 0 || loyalty.next_tier_benefits?.length > 0) && (
                    <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px dashed var(--border-color)', display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                      {loyalty.tier_benefits?.length > 0 && (
                        <div style={{ flex: 1, minWidth: '200px' }}>
                          <div style={{ fontSize: '0.72rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>Your {loyalty.tier} Benefits</div>
                          <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                            {loyalty.tier_benefits.map((b, i) => <li key={i} style={{ marginBottom: '0.2rem' }}>{b}</li>)}
                          </ul>
                        </div>
                      )}
                      {loyalty.next_tier && loyalty.next_tier_benefits?.length > 0 && (
                        <div style={{ flex: 1, minWidth: '200px' }}>
                          <div style={{ fontSize: '0.72rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>Unlock at {loyalty.next_tier}</div>
                          <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                            {loyalty.next_tier_benefits.map((b, i) => <li key={i} style={{ marginBottom: '0.2rem' }}>{b}</li>)}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
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
                </>
              )}
            </section>
          )}

          {activeSection === 'visa' && visaEnabled && (
            <section className="search-section glass-panel">
              <h3 className="results-heading" style={{ marginTop: 0 }}>Visa Status Check</h3>
              <form onSubmit={checkVisaStatus} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '180px' }}>
                  <label className="form-label">Your Nationality</label>
                  <select className="form-input" required value={visaNationality} onChange={e => setVisaNationality(e.target.value)}>
                    <option value="">-- Select nationality --</option>
                    {COUNTRIES.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </select>
                </div>
                <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '180px' }}>
                  <label className="form-label">Destination</label>
                  <select className="form-input" required value={visaDestination} onChange={e => setVisaDestination(e.target.value)}>
                    <option value="">-- Select destination --</option>
                    {COUNTRIES.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </select>
                </div>
                <button type="submit" className="btn btn-primary" disabled={visaChecking}>
                  {visaChecking ? 'Checking…' : 'Check Status'}
                </button>
              </form>
              {visaError && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{visaError}</div>}
              {visaResult && (
                <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px dashed var(--border-color)' }}>
                  {visaResult.found ? (
                    <>
                      <div style={{ fontWeight: '800', color: 'var(--gs-dark)' }}>
                        {visaResult.nationality} → {visaResult.destination}: <span style={{ color: visaResult.visa_required === 'not_required' || visaResult.visa_required === 'visa_free' ? '#166534' : '#b45309' }}>{visaResult.visa_required}</span>
                      </div>
                      <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: '0.4rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: '0.4rem' }}>
                        {visaResult.visa_type && <div><strong>Type:</strong> {visaResult.visa_type}</div>}
                        {visaResult.processing_time && <div><strong>Processing:</strong> {visaResult.processing_time}</div>}
                        {visaResult.validity && <div><strong>Validity:</strong> {visaResult.validity}</div>}
                      </div>
                      {visaResult.notes && <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>{visaResult.notes}</p>}
                      {visaResult.cancellation_policy && <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}><strong>Cancellation Policy:</strong> {visaResult.cancellation_policy}</p>}
                    </>
                  ) : (
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{visaResult.message}</p>
                  )}
                </div>
              )}
              {visaHistory.length > 0 && (
                <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px dashed var(--border-color)' }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>Your Visa Consultation History</div>
                  {visaHistory.map(v => (
                    <div key={v.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', padding: '0.3rem 0', borderBottom: '1px solid var(--border-color)' }}>
                      <span style={{ color: 'var(--text-secondary)' }}>{v.nationality} → {v.destination} · {v.slot_date} {v.slot_time}</span>
                      <span style={{ fontWeight: '700', textTransform: 'capitalize' }}>{v.status}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {activeSection === 'cancellations' && (
            <section className="search-section glass-panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.25rem' }}>
                <h3 className="results-heading" style={{ margin: 0 }}>Cancellation Requests</h3>
                <button className="btn btn-primary btn-sm" onClick={() => onRequestCancellation()}>+ New Cancellation Request</button>
              </div>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '0 0 1rem' }}>
                Submit a new request here, or click "Request Cancellation" directly on a booking under My Bookings — either way, it lands in this list once submitted.
              </p>
              {cancellationRequests.length === 0 ? (
                <div className="empty-state glass-panel animate-fade">
                  <p>No cancellation requests submitted yet.</p>
                </div>
              ) : (
                cancellationRequests.map(r => (
                  <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', padding: '0.4rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>PNR {r.booking_locator} · Travel {r.travel_date}</span>
                    <span style={{
                      fontWeight: '700', textTransform: 'capitalize',
                      color: r.status === 'resolved' ? '#166534' : r.status === 'rejected' ? '#991b1b' : '#b45309',
                    }}>{r.status}</span>
                  </div>
                ))
              )}
            </section>
          )}

          {activeSection === 'account' && (
            <section className="search-section glass-panel">
              <h3 className="results-heading" style={{ marginTop: 0 }}>Account Settings</h3>
              <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem', color: 'var(--gs-dark)' }}>Change Email Address</h4>
              <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: '0 0 0.75rem' }}>
                Email changes go through an admin review before taking effect, and are limited to 2 per 12-month period.
              </p>
              {emailRequest ? (
                <p style={{ fontSize: '0.82rem', color: '#b45309', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                  ⏳ Email change to <strong>{emailRequest.new_email}</strong> pending admin approval.
                </p>
              ) : (
                <form onSubmit={submitEmailChange} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                  <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '220px' }}>
                    <label className="form-label">New Email Address</label>
                    <input type="email" className="form-input" required value={newEmail} onChange={e => setNewEmail(e.target.value)} />
                  </div>
                  <button type="submit" className="btn btn-primary" disabled={emailSubmitting}>
                    {emailSubmitting ? 'Submitting…' : 'Submit Request'}
                  </button>
                  {emailFormError && <div className="error-banner" style={{ width: '100%' }}>{emailFormError}</div>}
                </form>
              )}
              {emailFormMsg && <p style={{ fontSize: '0.8rem', color: '#166534', marginTop: '0.75rem' }}>{emailFormMsg}</p>}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
