import { useState, useEffect } from 'react';

export default function AdminPortal({ adminToken, onAdminLogin, onAdminLogout, API_BASE, fetchWithRetry, handleApiResponse, onNavigate, onOpenInvoice }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);

  const [settings, setSettings] = useState(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState('');
  const [saveMsg, setSaveMsg] = useState('');
  const [saving, setSaving] = useState(false);

  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState('');

  const [loyaltySettings, setLoyaltySettings] = useState(null);
  const [loyaltySettingsError, setLoyaltySettingsError] = useState('');
  const [loyaltySaving, setLoyaltySaving] = useState(false);
  const [loyaltySaveMsg, setLoyaltySaveMsg] = useState('');

  const [emailRequests, setEmailRequests] = useState([]);
  const [emailRequestsLoading, setEmailRequestsLoading] = useState(false);
  const [emailRequestsError, setEmailRequestsError] = useState('');
  const [emailRequestActionId, setEmailRequestActionId] = useState(null);

  const [assignLocator, setAssignLocator] = useState('');
  const [assignEmail, setAssignEmail] = useState('');
  const [assigning, setAssigning] = useState(false);
  const [assignMsg, setAssignMsg] = useState('');
  const [assignError, setAssignError] = useState('');

  const authHeaders = { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` };

  const loadSettings = async () => {
    setSettingsLoading(true);
    setSettingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/pricing-settings`, { headers: authHeaders });
      const data = await handleApiResponse(res, 'Failed to load pricing settings');
      setSettings(data);
    } catch (err) {
      setSettingsError(err.message);
    } finally {
      setSettingsLoading(false);
    }
  };

  const loadSummary = async () => {
    setSummaryLoading(true);
    setSummaryError('');
    try {
      const params = new URLSearchParams();
      if (startDate) params.set('start_date', startDate);
      if (endDate) params.set('end_date', endDate);
      const res = await fetchWithRetry(`${API_BASE}/admin/reports/summary?${params.toString()}`, { headers: authHeaders });
      const data = await handleApiResponse(res, 'Failed to load report summary');
      setSummary(data);
    } catch (err) {
      setSummaryError(err.message);
    } finally {
      setSummaryLoading(false);
    }
  };

  const loadLoyaltySettings = async () => {
    setLoyaltySettingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/loyalty-settings`, { headers: authHeaders });
      const data = await handleApiResponse(res, 'Failed to load loyalty settings');
      setLoyaltySettings(data);
    } catch (err) {
      setLoyaltySettingsError(err.message);
    }
  };

  const loadEmailRequests = async () => {
    setEmailRequestsLoading(true);
    setEmailRequestsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/email-change-requests?status=pending`, { headers: authHeaders });
      const data = await handleApiResponse(res, 'Failed to load email change requests');
      setEmailRequests(data.requests || []);
    } catch (err) {
      setEmailRequestsError(err.message);
    } finally {
      setEmailRequestsLoading(false);
    }
  };

  useEffect(() => {
    if (adminToken) {
      loadSettings();
      loadSummary();
      loadLoyaltySettings();
      loadEmailRequests();
    }
  }, [adminToken]);

  const handleSaveLoyaltySettings = async () => {
    setLoyaltySaving(true);
    setLoyaltySaveMsg('');
    setLoyaltySettingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/loyalty-settings`, {
        method: 'PUT',
        headers: authHeaders,
        body: JSON.stringify(loyaltySettings),
      });
      const data = await handleApiResponse(res, 'Failed to save loyalty settings');
      setLoyaltySettings(data);
      setLoyaltySaveMsg('Saved.');
      setTimeout(() => setLoyaltySaveMsg(''), 3000);
    } catch (err) {
      setLoyaltySettingsError(err.message);
    } finally {
      setLoyaltySaving(false);
    }
  };

  const reviewEmailRequest = async (id, action) => {
    setEmailRequestActionId(id);
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/email-change-requests/${id}/${action}`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({}),
      });
      await handleApiResponse(res, `Failed to ${action} request`);
      setEmailRequests(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setEmailRequestsError(err.message);
    } finally {
      setEmailRequestActionId(null);
    }
  };

  const handleAssignBooking = async (e) => {
    e.preventDefault();
    setAssigning(true);
    setAssignMsg('');
    setAssignError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/bookings/${assignLocator.trim()}/assign-customer`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ customer_email: assignEmail.trim() }),
      });
      const data = await handleApiResponse(res, 'Failed to assign booking');
      setAssignMsg(`Booking ${data.booking.locator_code} is now linked to ${assignEmail.trim()}.`);
      setAssignLocator('');
      setAssignEmail('');
    } catch (err) {
      setAssignError(err.message);
    } finally {
      setAssigning(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError('');
    setLoggingIn(true);
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await handleApiResponse(res, 'Login failed');
      onAdminLogin(data.access_token);
    } catch (err) {
      setLoginError(err.message);
    } finally {
      setLoggingIn(false);
    }
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    setSaveMsg('');
    setSettingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/pricing-settings`, {
        method: 'PUT',
        headers: authHeaders,
        body: JSON.stringify(settings),
      });
      const data = await handleApiResponse(res, 'Failed to save pricing settings');
      setSettings(data);
      setSaveMsg('Saved.');
      setTimeout(() => setSaveMsg(''), 3000);
    } catch (err) {
      setSettingsError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (!adminToken) {
    return (
      <div className="tab-content animate-fade" style={{ maxWidth: '420px', margin: '3rem auto' }}>
        <section className="search-section glass-panel">
          <h2 className="section-title">Admin Login</h2>
          {loginError && <div className="error-banner">{loginError}</div>}
          <form onSubmit={handleLogin}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input className="form-input" required value={username} onChange={e => setUsername(e.target.value)} autoFocus />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input type="password" className="form-input" required value={password} onChange={e => setPassword(e.target.value)} />
            </div>
            <button type="submit" className="btn btn-primary" disabled={loggingIn} style={{ width: '100%', marginTop: '0.5rem' }}>
              {loggingIn ? 'Signing in…' : 'Sign In'}
            </button>
          </form>
        </section>
      </div>
    );
  }

  const markupField = (category, label) => {
    if (!settings) return null;
    const modeKey = `${category}_markup_mode`;
    const percentKey = `${category}_markup_percent`;
    const fixedKey = `${category}_markup_fixed`;
    return (
      <div style={{ background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem', flex: 1, minWidth: '260px' }}>
        <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>{label}</h4>
        <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.75rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.85rem', cursor: 'pointer' }}>
            <input type="radio" checked={settings[modeKey] === 'percent'}
              onChange={() => setSettings(s => ({ ...s, [modeKey]: 'percent' }))} />
            Percentage
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.85rem', cursor: 'pointer' }}>
            <input type="radio" checked={settings[modeKey] === 'fixed'}
              onChange={() => setSettings(s => ({ ...s, [modeKey]: 'fixed' }))} />
            Fixed Amount
          </label>
        </div>
        <div className="form-group" style={{ marginBottom: '0.5rem' }}>
          <label className="form-label">Percentage (%)</label>
          <input type="number" min="0" step="0.01" className="form-input" value={settings[percentKey]}
            onChange={e => setSettings(s => ({ ...s, [percentKey]: parseFloat(e.target.value) || 0 }))} />
        </div>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label className="form-label">Fixed Amount</label>
          <input type="number" min="0" step="0.01" className="form-input" value={settings[fixedKey]}
            onChange={e => setSettings(s => ({ ...s, [fixedKey]: parseFloat(e.target.value) || 0 }))} />
        </div>
      </div>
    );
  };

  return (
    <div className="tab-content animate-fade">
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <h2 className="section-title" style={{ margin: 0 }}>Admin Portal — Pricing & Reports</h2>
          <button className="btn btn-secondary btn-sm" onClick={onAdminLogout}>Log Out</button>
        </div>
      </section>

      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Margin / Markup Settings</h3>
        {settingsError && <div className="error-banner">{settingsError}</div>}
        {settingsLoading || !settings ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading settings...</p></div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              {markupField('ticket', 'Flight Ticket Margin')}
              {markupField('seat', 'Seat Booking Margin')}
            </div>
            <button className="btn btn-primary" onClick={handleSaveSettings} disabled={saving}>
              {saving ? 'Saving…' : 'Save Pricing Settings'}
            </button>
            {saveMsg && <span style={{ marginLeft: '0.75rem', color: '#16a34a', fontWeight: 600, fontSize: '0.85rem' }}>{saveMsg}</span>}
          </>
        )}
      </section>

      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Loyalty Program Settings</h3>
        {loyaltySettingsError && <div className="error-banner">{loyaltySettingsError}</div>}
        {!loyaltySettings ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading settings...</p></div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              <div style={{ background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem', flex: 1, minWidth: '220px' }}>
                <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>Points Earning Rate</h4>
                <div className="form-group" style={{ marginBottom: '0.5rem' }}>
                  <label className="form-label">Points per 1 LKR spent</label>
                  <input type="number" min="0" step="0.001" className="form-input" value={loyaltySettings.points_per_lkr}
                    onChange={e => setLoyaltySettings(s => ({ ...s, points_per_lkr: parseFloat(e.target.value) || 0 }))} />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label className="form-label">Points per 1 USD spent</label>
                  <input type="number" min="0" step="0.01" className="form-input" value={loyaltySettings.points_per_usd}
                    onChange={e => setLoyaltySettings(s => ({ ...s, points_per_usd: parseFloat(e.target.value) || 0 }))} />
                </div>
              </div>
              <div style={{ background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem', flex: 1, minWidth: '220px' }}>
                <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>Tier Thresholds</h4>
                <div className="form-group" style={{ marginBottom: '0.5rem' }}>
                  <label className="form-label">🥈 Silver at (points)</label>
                  <input type="number" min="0" step="1" className="form-input" value={loyaltySettings.tier_silver_threshold}
                    onChange={e => setLoyaltySettings(s => ({ ...s, tier_silver_threshold: parseInt(e.target.value) || 0 }))} />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label className="form-label">🥇 Gold at (points)</label>
                  <input type="number" min="0" step="1" className="form-input" value={loyaltySettings.tier_gold_threshold}
                    onChange={e => setLoyaltySettings(s => ({ ...s, tier_gold_threshold: parseInt(e.target.value) || 0 }))} />
                </div>
              </div>
            </div>
            <button className="btn btn-primary" onClick={handleSaveLoyaltySettings} disabled={loyaltySaving}>
              {loyaltySaving ? 'Saving…' : 'Save Loyalty Settings'}
            </button>
            {loyaltySaveMsg && <span style={{ marginLeft: '0.75rem', color: '#16a34a', fontWeight: 600, fontSize: '0.85rem' }}>{loyaltySaveMsg}</span>}
          </>
        )}
      </section>

      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Email Change Requests {emailRequests.length > 0 && `(${emailRequests.length} pending)`}</h3>
        {emailRequestsError && <div className="error-banner">{emailRequestsError}</div>}
        {emailRequestsLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading requests...</p></div>
        ) : emailRequests.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No pending email change requests.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {emailRequests.map(r => (
              <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <strong>{r.old_email}</strong> → <strong style={{ color: 'var(--gs-crimson)' }}>{r.new_email}</strong>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>Requested {new Date(r.requested_at).toLocaleString()}</div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-primary btn-sm" disabled={emailRequestActionId === r.id} onClick={() => reviewEmailRequest(r.id, 'approve')}>Approve</button>
                  <button className="btn btn-danger btn-sm" disabled={emailRequestActionId === r.id} onClick={() => reviewEmailRequest(r.id, 'reject')}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Assign Booking to Customer</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginBottom: '1rem' }}>
          For bookings made under a different email than the customer's account — link it directly after verifying ownership.
        </p>
        <form onSubmit={handleAssignBooking} style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '160px' }}>
            <label className="form-label">PNR / Locator Code</label>
            <input className="form-input" required value={assignLocator} onChange={e => setAssignLocator(e.target.value.toUpperCase())} />
          </div>
          <div className="form-group" style={{ marginBottom: 0, flex: 1, minWidth: '220px' }}>
            <label className="form-label">Customer Account Email</label>
            <input type="email" className="form-input" required value={assignEmail} onChange={e => setAssignEmail(e.target.value)} />
          </div>
          <button type="submit" className="btn btn-primary" disabled={assigning}>
            {assigning ? 'Assigning…' : 'Assign'}
          </button>
        </form>
        {assignMsg && <p style={{ fontSize: '0.82rem', color: '#166534', marginTop: '0.75rem' }}>{assignMsg}</p>}
        {assignError && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{assignError}</div>}
      </section>

      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Reports</h3>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: '1.25rem' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Start Date</label>
            <input type="date" className="form-input" value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">End Date</label>
            <input type="date" className="form-input" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={loadSummary} disabled={summaryLoading}>
            {summaryLoading ? 'Loading…' : 'Apply Filter'}
          </button>
        </div>

        {summaryError && <div className="error-banner">{summaryError}</div>}

        {summary && (
          <>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1.25rem' }}>
              <div style={{ flex: 1, minWidth: '180px', background: 'rgba(195,18,46,0.06)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase' }}>Total Bookings</div>
                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: 'var(--gs-dark)' }}>{summary.total_bookings}</div>
              </div>
              <div style={{ flex: 1, minWidth: '180px', background: 'rgba(195,18,46,0.06)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase' }}>Tickets Issued</div>
                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: 'var(--gs-dark)' }}>
                  {(summary.by_currency || []).reduce((s, c) => s + (c.ticket_count || 0), 0)}
                </div>
              </div>
              <div style={{ flex: 1, minWidth: '180px', background: 'rgba(195,18,46,0.06)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase' }}>Cancelled Bookings</div>
                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: 'var(--gs-dark)' }}>{summary.total_cancelled}</div>
              </div>
            </div>

            <h4 style={{ fontSize: '0.85rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>
              Total Sales by Currency
            </h4>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
              {(summary.by_currency || []).length === 0 ? (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No bookings in this range.</p>
              ) : summary.by_currency.map(row => (
                <div key={row.currency} style={{ flex: 1, minWidth: '200px', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700 }}>{row.currency}</div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 800, color: 'var(--gs-crimson)' }}>
                    {row.currency} {Number(row.total_sales || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>{row.booking_count} bookings · {row.ticket_count} tickets</div>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="search-section glass-panel">
        <h3 className="results-heading">Ticket & Invoice Detail</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: '1rem' }}>
          Per-PNR ticket lists and Travelport invoice data are available in the existing tabs below.
        </p>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" onClick={() => onNavigate('bookings')}>View All Bookings / Tickets</button>
          <button className="btn btn-secondary" onClick={onOpenInvoice}>View Invoice Data</button>
        </div>
      </section>
    </div>
  );
}
