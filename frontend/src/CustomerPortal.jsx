import { useState, useEffect } from 'react';
import AddonInvoice from './AddonInvoice.jsx';
import BookingLineList from './BookingLineList.jsx';
import BookingDetailView from './BookingDetailView.jsx';
import BookingStatusPage from './BookingStatusPage.jsx';
import LoyaltyPage from './LoyaltyPage.jsx';
import { COUNTRIES } from './countries.js';
import { fmtDate } from './dateFormat.js';

const NavSvg = ({ children }) => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
// Account menu icons (one per tab id).
const NAV_ICONS = {
  bookings: () => <NavSvg><path d="M3 8.5V6a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.5a2.5 2.5 0 0 0 0 5V16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2.5a2.5 2.5 0 0 0 0-5z" /><path d="M14 5v12" strokeDasharray="2 2.2" /></NavSvg>,
  status: () => <NavSvg><circle cx="12" cy="12" r="9" /><path d="m8 12.5 2.8 2.8L16.5 9.5" /></NavSvg>,
  loyalty: () => <NavSvg><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" /></NavSvg>,
  visa: () => <NavSvg><rect x="4.5" y="2.5" width="15" height="19" rx="2" /><circle cx="12" cy="10" r="3.2" /><path d="M8.5 17h7" /></NavSvg>,
  cancellations: () => <NavSvg><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></NavSvg>,
  account: () => <NavSvg><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></NavSvg>,
};

export default function CustomerPortal({ customerToken, customerProfile, onCustomerLogin, onCustomerLogout, API_BASE, fetchWithRetry, handleApiResponse, onViewTicket, onOpenInvoice, onRequestCancellation, visaEnabled }) {
  // The Loyalty Program page is the portal's home page after sign-in.
  const [activeSection, setActiveSection] = useState('loyalty');

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
  // The booking opened from the My Bookings list (its detail view), by PNR.
  const [openLocator, setOpenLocator] = useState(null);
  // Booking Status page opened from a booking's detail view, pre-filled.
  const [statusPrefill, setStatusPrefill] = useState(null);
  // Result of a Manage My Booking add-on payment, after returning from PayCorp.
  const [addonPaymentResult, setAddonPaymentResult] = useState(null);
  const [addonInvoiceOpen, setAddonInvoiceOpen] = useState(null);
  const [liveStatuses, setLiveStatuses] = useState({});
  const [refreshingStatuses, setRefreshingStatuses] = useState(false);

  const [loyalty, setLoyalty] = useState(null);

  const [emailRequest, setEmailRequest] = useState(null);

  const [cancellationRequests, setCancellationRequests] = useState([]);

  const [visaNationality, setVisaNationality] = useState('');
  const [visaDestination, setVisaDestination] = useState('');
  const [visaResult, setVisaResult] = useState(null);
  const [visaError, setVisaError] = useState('');
  const [visaChecking, setVisaChecking] = useState(false);
  const [visaHistory, setVisaHistory] = useState([]);

  const [oauthError, setOauthError] = useState('');
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get('oauth_error');
    if (err) {
      setOauthError(err === 'not_configured' ? 'That sign-in method isn\'t set up yet.' : 'Sign-in failed. Please try again or use your email and password.');
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // A customer token expires after 24h (see AuthConfig.JWT_EXPIRE_MINUTES) --
  // without this, an expired token made every "me" call below 401 silently
  // (their catch blocks are non-critical/ignore), leaving e.g. the Loyalty
  // tab stuck on "Loading your loyalty status..." forever with no sign the
  // session itself had lapsed. Logging the customer out on a 401 sends them
  // back to the sign-in form instead of a dead spinner.
  const handleSessionExpired = () => {
    onCustomerLogout();
  };

  const loadBookings = async () => {
    setLoadingBookings(true);
    setBookingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/bookings`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.status === 401) return handleSessionExpired();
      const data = await handleApiResponse(res, 'Failed to load your bookings');
      setBookings(data.bookings || []);
      refreshLiveStatuses();
    } catch (err) {
      setBookingsError(err.message);
    } finally {
      setLoadingBookings(false);
    }
  };

  // Upcoming bookings' status checked live with Travelport (and saved back),
  // so an airline cancellation shows in the list itself, not only in Live status.
  const openBooking = openLocator ? bookings.find(b => b.locator_code === openLocator) : null;
  const openBookingDetail = (b) => { setOpenLocator(b.locator_code); window.scrollTo({ top: 0, behavior: 'smooth' }); };
  const closeBooking = () => setOpenLocator(null);
  const openBookingStatus = (b) => {
    const t = (Array.isArray(b.travelers) && b.travelers[0]) || {};
    const lastName = t.last_name || t.surname || (b.passenger_name || '').trim().split(/\s+/).pop() || '';
    setStatusPrefill({ query: b.locator_code, lastName, key: Date.now() });
    setOpenLocator(null);
    setActiveSection('status');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const refreshLiveStatuses = async () => {
    setRefreshingStatuses(true);
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/bookings/refresh-status`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setLiveStatuses(data.statuses || {});
      }
    } catch {
      // Best effort: the list still shows the saved statuses.
    } finally {
      setRefreshingStatuses(false);
    }
  };

  const loadLoyalty = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/loyalty`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.status === 401) return handleSessionExpired();
      const data = await handleApiResponse(res, 'Failed to load loyalty status');
      setLoyalty(data);
    } catch { /* non-critical, ignore */ }
  };

  const loadEmailRequest = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/email-change-request`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.status === 401) return handleSessionExpired();
      const data = await handleApiResponse(res, 'Failed to load email change status');
      setEmailRequest(data.request || null);
    } catch { /* non-critical, ignore */ }
  };

  const loadCancellationRequests = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/cancellation-requests`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.status === 401) return handleSessionExpired();
      const data = await handleApiResponse(res, 'Failed to load cancellation requests');
      setCancellationRequests(data.requests || []);
    } catch { /* non-critical, ignore */ }
  };

  const loadVisaHistory = async () => {
    try {
      const res = await fetchWithRetry(`${API_BASE}/customers/me/visa-history`, {
        headers: { Authorization: `Bearer ${customerToken}` },
      });
      if (res.status === 401) return handleSessionExpired();
      const data = await handleApiResponse(res, 'Failed to load visa consultation history');
      setVisaHistory(data.consultations || []);
    } catch { /* non-critical, ignore */ }
  };

  // Link from a payment-due / reminder email or SMS (?status=PNR): open
  // Booking Status with the reference filled in (the customer adds the last name).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const pnr = params.get('status');
    if (!pnr || !customerToken) return;
    window.history.replaceState({}, '', window.location.pathname);
    setStatusPrefill({ query: pnr.toUpperCase(), lastName: '', key: Date.now() });
    setActiveSection('status');
  }, [customerToken]); // eslint-disable-line react-hooks/exhaustive-deps

  // Back from PayCorp for a Manage My Booking add-on (?addon=...&reqid=...):
  // the backend verifies the payment and has the airline issue the EMD.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const addon = params.get('addon');
    if (!addon || !customerToken) return;
    setActiveSection('status');
    window.history.replaceState({}, '', window.location.pathname);
    if (params.get('payment') === 'cancelled' || !params.get('reqid')) {
      setAddonPaymentResult({ ok: false, message: 'Payment was cancelled — you have not been charged. Your seat/extra stays on the booking unpaid only until its payment deadline: pay for it from Booking Status before then, or it will be removed.' });
      return;
    }
    (async () => {
      try {
        const res = await fetchWithRetry(`${API_BASE}/manage-booking/complete-payment`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${customerToken}` },
          body: JSON.stringify({ addon_id: addon, reqid: params.get('reqid') }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not complete the payment');
        // Below the message: the booking itself, looked up again, so the new
        // seats/extras and their EMDs show (not an empty search form).
        if (data.locator_code) {
          setStatusPrefill({ query: data.locator_code, lastName: data.last_name || '', key: Date.now() });
        }
        if (data.status === 'completed') {
          setAddonPaymentResult({ ok: true, invoice: data.addon_id || addon,
            message: `Payment confirmed — thank you. The airline has issued ${(data.emds || []).map(e => `EMD ${e.number}`).join(', ') || 'the document'} for your seats/extras.` });
          // The paid invoice opens at once (charges, items, EMDs); "View invoice" reopens it.
          setAddonInvoiceOpen(data.addon_id || addon);
        } else if (data.status === 'emd_failed') {
          setAddonPaymentResult({ ok: false, message: 'Your payment was received but the airline didn\u2019t issue the document yet. Our team has been alerted and will complete it or refund you.' });
        } else {
          setAddonPaymentResult({ ok: false, message: 'This payment is no longer open (the payment deadline passed or it was replaced) — you have not been charged. Check Booking Status for what is still due.' });
        }
      } catch (err) {
        setAddonPaymentResult({ ok: false, message: err.message });
      }
    })();
  }, [customerToken]); // eslint-disable-line react-hooks/exhaustive-deps

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
          {oauthError && <div className="error-banner">{oauthError}</div>}
          {formError && <div className="error-banner">{formError}</div>}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginBottom: '1.25rem' }}>
            <button
              type="button"
              onClick={() => { window.location.href = `${API_BASE}/customers/oauth/google/start`; }}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem',
                width: '100%', padding: '0.6rem', borderRadius: '8px', border: '1px solid #e2e8f0',
                background: 'white', color: '#0f172a', fontSize: '0.85rem', fontWeight: '600', cursor: 'pointer',
              }}
            >
              <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
                <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"/>
                <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
                <path fill="#4CAF50" d="M24 44c5.5 0 10.4-2.1 14.1-5.6l-6.5-5.5C29.5 34.5 26.9 35.5 24 35.5c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.6 39.6 16.3 44 24 44z"/>
                <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.2 4.2-4.1 5.6l6.5 5.5C41.3 36 44 30.5 44 24c0-1.3-.1-2.7-.4-3.5z"/>
              </svg>
              Continue with Google
            </button>
            {/* Microsoft sign-in removed from the page (11/11/2026): Google or an email account only. */}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', margin: '0 0 1.25rem', color: '#94a3b8', fontSize: '0.72rem', fontWeight: '700', textTransform: 'uppercase' }}>
            <div style={{ flex: 1, height: '1px', background: '#e2e8f0' }} />
            or
            <div style={{ flex: 1, height: '1px', background: '#e2e8f0' }} />
          </div>

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

  // Visa Status, shown on the Loyalty page (no tab of its own).
  const visaSection = visaEnabled ? (
    <section className="loy-card loy-visa">
      <div className="loy-head"><div><h3>Visa status</h3><p>Check the visa rules for your nationality and destination, and see your visa consultations.</p></div></div>
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
              <span style={{ color: 'var(--text-secondary)' }}>{v.nationality} → {v.destination} · {fmtDate(v.slot_date)} {v.slot_time}</span>
              <span style={{ fontWeight: '700', textTransform: 'capitalize' }}>{v.status}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  ) : null;

  const customerNavItems = [
    { id: 'loyalty', label: 'Loyalty Program' },
    { id: 'bookings', label: 'My Bookings', count: bookings.length },
    { id: 'status', label: 'Booking Status' },
    { id: 'cancellations', label: 'Cancellation Requests', count: cancellationRequests.length },
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
            <button className="btn btn-secondary btn-sm" onClick={onCustomerLogout}>Log Out</button>
          </div>
        </div>
      </section>

      {/* Account menu: one row of tabs across the top (was a left sidebar). */}
      <div className="customer-portal-layout cp-layout">
        <nav className="cp-tabs" aria-label="My account">
          {customerNavItems.map(item => {
            const Icon = NAV_ICONS[item.id];
            const active = activeSection === item.id;
            return (
              <button key={item.id} type="button" className="cp-tab" aria-current={active ? 'page' : undefined}
                      onClick={() => { setActiveSection(item.id); setOpenLocator(null); }}>
                {Icon && <Icon />}
                <span>{item.label}</span>
                {item.count > 0 && <span className="cp-count">{item.count}</span>}
              </button>
            );
          })}
        </nav>

        <div className="cp-content">
          {activeSection === 'bookings' && openBooking && (
            <BookingDetailView booking={openBooking} liveStatus={liveStatuses[openBooking.locator_code]}
                               onBack={closeBooking} onViewTicket={onViewTicket} onRequestCancellation={onRequestCancellation}
                               onLiveStatus={openBookingStatus} />
          )}

          {activeSection === 'bookings' && !openBooking && (
            <section className="bookings-list-section">
              <h3 className="results-heading">
                {loadingBookings ? 'Loading...' : `My Bookings (${bookings.length})`}
              </h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '-0.5rem 0 1rem' }}>
                Bookings made while signed in, plus any past bookings made under this account's email address.
                Booked under a different email? Open the Booking Status tab and enter the PNR or ticket number with the passenger's last name.
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
                <BookingLineList bookings={bookings} liveStatuses={liveStatuses} refreshing={refreshingStatuses}
                                 onOpen={openBookingDetail} />
              )}
            </section>
          )}

          {activeSection === 'status' && (
            <>
              {addonPaymentResult && (
                <div className={addonPaymentResult.ok ? '' : 'error-banner'} style={addonPaymentResult.ok ? { background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px', padding: '0.7rem 0.9rem', marginBottom: '0.9rem', fontSize: '0.85rem', color: '#166534' } : { marginBottom: '0.9rem' }}>
                  {addonPaymentResult.message}
                  {addonPaymentResult.ok && addonPaymentResult.invoice && (
                    <div style={{ marginTop: '0.45rem' }}>
                      <button className="btn btn-primary btn-sm" onClick={() => setAddonInvoiceOpen(addonPaymentResult.invoice)}>View invoice</button>
                    </div>
                  )}
                </div>
              )}
              <BookingStatusPage apiBase={API_BASE} fetchWithRetry={fetchWithRetry} authToken={customerToken} prefill={statusPrefill} />
              {addonInvoiceOpen && (
                <AddonInvoice addonId={addonInvoiceOpen} apiBase={API_BASE} fetchWithRetry={fetchWithRetry} authToken={customerToken}
                              onClose={() => setAddonInvoiceOpen(null)} />
              )}
            </>
          )}

          {activeSection === 'loyalty' && (
            <LoyaltyPage loyalty={loyalty} bookings={bookings} loadingBookings={loadingBookings} liveStatuses={liveStatuses}
                         cancellationRequests={cancellationRequests} visa={visaSection}
                         onOpen={(b) => { setActiveSection('bookings'); openBookingDetail(b); }}
                         onManage={openBookingStatus} onCancel={onRequestCancellation} />
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
                    <span style={{ color: 'var(--text-secondary)' }}>PNR {r.booking_locator} · Travel {fmtDate(r.travel_date)}</span>
                    <span style={{
                      fontWeight: '700', textTransform: 'capitalize',
                      color: r.status === 'resolved' ? '#166534' : r.status === 'rejected' ? '#991b1b' : '#b45309',
                    }}>{r.status}</span>
                  </div>
                ))
              )}
            </section>
          )}

        </div>
      </div>
    </div>
  );
}
