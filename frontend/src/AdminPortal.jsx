import { useState, useEffect } from 'react';

export default function AdminPortal({ adminToken, onAdminLogin, onAdminLogout, API_BASE, fetchWithRetry, handleApiResponse, onNavigate, onOpenInvoice }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);

  const [activeAdminSection, setActiveAdminSection] = useState('pricing');

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

  const [cancellationRequests, setCancellationRequests] = useState([]);
  const [cancellationRequestsLoading, setCancellationRequestsLoading] = useState(false);
  const [cancellationRequestsError, setCancellationRequestsError] = useState('');
  const [cancellationRequestActionId, setCancellationRequestActionId] = useState(null);

  const [visaConsultations, setVisaConsultations] = useState([]);
  const [visaConsultationsLoading, setVisaConsultationsLoading] = useState(false);
  const [visaConsultationsError, setVisaConsultationsError] = useState('');
  const [visaConsultationActionId, setVisaConsultationActionId] = useState(null);

  const emptyPackageForm = {
    title: '', destination: '', duration_days: 1, duration_nights: 0, price: 0, currency: 'LKR',
    image_url: '', summary: '', description: '', itinerary: '', inclusions: '', exclusions: '',
    valid_from: '', valid_to: '', is_active: true,
  };
  const [packages, setPackages] = useState([]);
  const [packagesLoading, setPackagesLoading] = useState(false);
  const [packagesError, setPackagesError] = useState('');
  const [packageForm, setPackageForm] = useState(emptyPackageForm);
  const [editingPackageId, setEditingPackageId] = useState(null);
  const [packageFormOpen, setPackageFormOpen] = useState(false);
  const [packageSaving, setPackageSaving] = useState(false);
  const [packageImageUploading, setPackageImageUploading] = useState(false);
  const [packageImageUploadError, setPackageImageUploadError] = useState('');

  const [packageRequests, setPackageRequests] = useState([]);
  const [packageRequestsLoading, setPackageRequestsLoading] = useState(false);
  const [packageRequestsError, setPackageRequestsError] = useState('');
  const [packageRequestActionId, setPackageRequestActionId] = useState(null);

  const emptyVisaForm = { nationality: '', destination: '', visa_required: 'required', visa_type: '', processing_time: '', validity: '', notes: '' };
  const [visaRequirements, setVisaRequirements] = useState([]);
  const [visaRequirementsLoading, setVisaRequirementsLoading] = useState(false);
  const [visaRequirementsError, setVisaRequirementsError] = useState('');
  const [visaForm, setVisaForm] = useState(emptyVisaForm);
  const [editingVisaId, setEditingVisaId] = useState(null);
  const [visaFormOpen, setVisaFormOpen] = useState(false);
  const [visaSaving, setVisaSaving] = useState(false);

  const emptyConsultantForm = { country: '', consultant_name: '', email: '', phone: '', is_active: true };
  const [visaConsultantRoster, setVisaConsultantRoster] = useState([]);
  const [visaConsultantRosterLoading, setVisaConsultantRosterLoading] = useState(false);
  const [visaConsultantRosterError, setVisaConsultantRosterError] = useState('');
  const [consultantForm, setConsultantForm] = useState(emptyConsultantForm);
  const [editingConsultantId, setEditingConsultantId] = useState(null);
  const [consultantFormOpen, setConsultantFormOpen] = useState(false);
  const [consultantSaving, setConsultantSaving] = useState(false);

  const [assignLocator, setAssignLocator] = useState('');
  const [assignEmail, setAssignEmail] = useState('');
  const [assigning, setAssigning] = useState(false);
  const [assignMsg, setAssignMsg] = useState('');
  const [assignError, setAssignError] = useState('');

  const authHeaders = { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` };

  // A 401 here means the admin's JWT expired (24h) or was revoked — every
  // data call in this file uses this instead of handleApiResponse directly,
  // so an expired session drops back to the login form with a clear message
  // instead of every section on the page individually showing a raw
  // "Invalid or expired token" error.
  const adminApiResponse = async (res, defaultError) => {
    if (res.status === 401) {
      onAdminLogout();
      throw new Error('Your session expired — please log in again.');
    }
    return handleApiResponse(res, defaultError);
  };

  const loadSettings = async () => {
    setSettingsLoading(true);
    setSettingsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/pricing-settings`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load pricing settings');
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
      const data = await adminApiResponse(res, 'Failed to load report summary');
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
      const data = await adminApiResponse(res, 'Failed to load loyalty settings');
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
      const data = await adminApiResponse(res, 'Failed to load email change requests');
      setEmailRequests(data.requests || []);
    } catch (err) {
      setEmailRequestsError(err.message);
    } finally {
      setEmailRequestsLoading(false);
    }
  };

  const loadCancellationRequests = async () => {
    setCancellationRequestsLoading(true);
    setCancellationRequestsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/cancellation-requests?status=pending`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load cancellation requests');
      setCancellationRequests(data.requests || []);
    } catch (err) {
      setCancellationRequestsError(err.message);
    } finally {
      setCancellationRequestsLoading(false);
    }
  };

  const loadVisaConsultations = async () => {
    setVisaConsultationsLoading(true);
    setVisaConsultationsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-consultations?status=pending`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load visa consultation bookings');
      setVisaConsultations(data.consultations || []);
    } catch (err) {
      setVisaConsultationsError(err.message);
    } finally {
      setVisaConsultationsLoading(false);
    }
  };

  const loadVisaConsultantRoster = async () => {
    setVisaConsultantRosterLoading(true);
    setVisaConsultantRosterError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-consultants`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load visa consultants');
      setVisaConsultantRoster(data.consultants || []);
    } catch (err) {
      setVisaConsultantRosterError(err.message);
    } finally {
      setVisaConsultantRosterLoading(false);
    }
  };

  const loadPackages = async () => {
    setPackagesLoading(true);
    setPackagesError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/packages`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load tour packages');
      setPackages(data.packages || []);
    } catch (err) {
      setPackagesError(err.message);
    } finally {
      setPackagesLoading(false);
    }
  };

  const loadPackageRequests = async () => {
    setPackageRequestsLoading(true);
    setPackageRequestsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/package-booking-requests?status=pending`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load package booking requests');
      setPackageRequests(data.requests || []);
    } catch (err) {
      setPackageRequestsError(err.message);
    } finally {
      setPackageRequestsLoading(false);
    }
  };

  const loadVisaRequirements = async () => {
    setVisaRequirementsLoading(true);
    setVisaRequirementsError('');
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-requirements`, { headers: authHeaders });
      const data = await adminApiResponse(res, 'Failed to load visa requirements');
      setVisaRequirements(data.requirements || []);
    } catch (err) {
      setVisaRequirementsError(err.message);
    } finally {
      setVisaRequirementsLoading(false);
    }
  };

  useEffect(() => {
    if (adminToken) {
      loadSettings();
      loadSummary();
      loadLoyaltySettings();
      loadEmailRequests();
      loadCancellationRequests();
      loadVisaConsultations();
      loadVisaConsultantRoster();
      loadPackages();
      loadPackageRequests();
      loadVisaRequirements();
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
      const data = await adminApiResponse(res, 'Failed to save loyalty settings');
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
      await adminApiResponse(res, `Failed to ${action} request`);
      setEmailRequests(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setEmailRequestsError(err.message);
    } finally {
      setEmailRequestActionId(null);
    }
  };

  const reviewCancellationRequest = async (id, action) => {
    setCancellationRequestActionId(id);
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/cancellation-requests/${id}/${action}`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({}),
      });
      await adminApiResponse(res, `Failed to ${action} request`);
      setCancellationRequests(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setCancellationRequestsError(err.message);
    } finally {
      setCancellationRequestActionId(null);
    }
  };

  const reviewVisaConsultation = async (id, action) => {
    setVisaConsultationActionId(id);
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-consultations/${id}/${action}`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({}),
      });
      await adminApiResponse(res, `Failed to ${action} booking`);
      setVisaConsultations(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setVisaConsultationsError(err.message);
    } finally {
      setVisaConsultationActionId(null);
    }
  };

  const reviewPackageRequest = async (id, action) => {
    setPackageRequestActionId(id);
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/package-booking-requests/${id}/${action}`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({}),
      });
      await adminApiResponse(res, `Failed to ${action} request`);
      setPackageRequests(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setPackageRequestsError(err.message);
    } finally {
      setPackageRequestActionId(null);
    }
  };

  const openPackageForm = (pkg) => {
    if (pkg) {
      setEditingPackageId(pkg.id);
      setPackageForm({
        title: pkg.title, destination: pkg.destination, duration_days: pkg.duration_days,
        duration_nights: pkg.duration_nights, price: pkg.price, currency: pkg.currency,
        image_url: pkg.image_url || '', summary: pkg.summary || '', description: pkg.description || '',
        itinerary: pkg.itinerary || '', inclusions: pkg.inclusions || '', exclusions: pkg.exclusions || '',
        valid_from: pkg.valid_from || '', valid_to: pkg.valid_to || '', is_active: !!pkg.is_active,
      });
    } else {
      setEditingPackageId(null);
      setPackageForm(emptyPackageForm);
    }
    setPackageFormOpen(true);
    setPackageImageUploadError('');
  };

  const handlePackageImageUpload = async (file) => {
    if (!file) return;
    setPackageImageUploadError('');
    setPackageImageUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetchWithRetry(`${API_BASE}/admin/upload-image`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${adminToken}` },
        body: formData,
      });
      const data = await adminApiResponse(res, 'Failed to upload image');
      setPackageForm(f => ({ ...f, image_url: data.url }));
    } catch (err) {
      setPackageImageUploadError(err.message);
    } finally {
      setPackageImageUploading(false);
    }
  };

  const handleSavePackage = async (e) => {
    e.preventDefault();
    setPackageSaving(true);
    setPackagesError('');
    try {
      const body = {
        ...packageForm,
        duration_days: parseInt(packageForm.duration_days, 10) || 1,
        duration_nights: parseInt(packageForm.duration_nights, 10) || 0,
        price: parseFloat(packageForm.price) || 0,
        valid_from: packageForm.valid_from || null,
        valid_to: packageForm.valid_to || null,
      };
      const url = editingPackageId ? `${API_BASE}/admin/packages/${editingPackageId}` : `${API_BASE}/admin/packages`;
      const res = await fetchWithRetry(url, {
        method: editingPackageId ? 'PUT' : 'POST',
        headers: authHeaders,
        body: JSON.stringify(body),
      });
      await adminApiResponse(res, 'Failed to save package');
      setPackageFormOpen(false);
      setEditingPackageId(null);
      setPackageForm(emptyPackageForm);
      loadPackages();
    } catch (err) {
      setPackagesError(err.message);
    } finally {
      setPackageSaving(false);
    }
  };

  const handleDeletePackage = async (id) => {
    if (!confirm('Delete this package? This cannot be undone.')) return;
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/packages/${id}`, { method: 'DELETE', headers: authHeaders });
      await adminApiResponse(res, 'Failed to delete package');
      setPackages(pkgs => pkgs.filter(p => p.id !== id));
    } catch (err) {
      setPackagesError(err.message);
    }
  };

  const openVisaForm = (req) => {
    if (req) {
      setEditingVisaId(req.id);
      setVisaForm({
        nationality: req.nationality, destination: req.destination, visa_required: req.visa_required,
        visa_type: req.visa_type || '', processing_time: req.processing_time || '',
        validity: req.validity || '', notes: req.notes || '',
      });
    } else {
      setEditingVisaId(null);
      setVisaForm(emptyVisaForm);
    }
    setVisaFormOpen(true);
  };

  const handleSaveVisaRequirement = async (e) => {
    e.preventDefault();
    setVisaSaving(true);
    setVisaRequirementsError('');
    try {
      const url = editingVisaId ? `${API_BASE}/admin/visa-requirements/${editingVisaId}` : `${API_BASE}/admin/visa-requirements`;
      const res = await fetchWithRetry(url, {
        method: editingVisaId ? 'PUT' : 'POST',
        headers: authHeaders,
        body: JSON.stringify(visaForm),
      });
      await adminApiResponse(res, 'Failed to save visa requirement');
      setVisaFormOpen(false);
      setEditingVisaId(null);
      setVisaForm(emptyVisaForm);
      loadVisaRequirements();
    } catch (err) {
      setVisaRequirementsError(err.message);
    } finally {
      setVisaSaving(false);
    }
  };

  const handleDeleteVisaRequirement = async (id) => {
    if (!confirm('Delete this visa requirement entry?')) return;
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-requirements/${id}`, { method: 'DELETE', headers: authHeaders });
      await adminApiResponse(res, 'Failed to delete visa requirement');
      setVisaRequirements(reqs => reqs.filter(r => r.id !== id));
    } catch (err) {
      setVisaRequirementsError(err.message);
    }
  };

  const openConsultantForm = (c) => {
    if (c) {
      setEditingConsultantId(c.id);
      setConsultantForm({ country: c.country, consultant_name: c.consultant_name, email: c.email, phone: c.phone || '', is_active: !!c.is_active });
    } else {
      setEditingConsultantId(null);
      setConsultantForm(emptyConsultantForm);
    }
    setConsultantFormOpen(true);
  };

  const handleSaveVisaConsultant = async (e) => {
    e.preventDefault();
    setConsultantSaving(true);
    setVisaConsultantRosterError('');
    try {
      const url = editingConsultantId ? `${API_BASE}/admin/visa-consultants/${editingConsultantId}` : `${API_BASE}/admin/visa-consultants`;
      const res = await fetchWithRetry(url, {
        method: editingConsultantId ? 'PUT' : 'POST',
        headers: authHeaders,
        body: JSON.stringify(consultantForm),
      });
      await adminApiResponse(res, 'Failed to save visa consultant');
      setConsultantFormOpen(false);
      setEditingConsultantId(null);
      setConsultantForm(emptyConsultantForm);
      loadVisaConsultantRoster();
    } catch (err) {
      setVisaConsultantRosterError(err.message);
    } finally {
      setConsultantSaving(false);
    }
  };

  const handleDeleteVisaConsultant = async (id) => {
    if (!confirm('Remove this visa consultant? Bookings already made will keep their record of who they booked with.')) return;
    try {
      const res = await fetchWithRetry(`${API_BASE}/admin/visa-consultants/${id}`, { method: 'DELETE', headers: authHeaders });
      await adminApiResponse(res, 'Failed to delete visa consultant');
      setVisaConsultantRoster(list => list.filter(c => c.id !== id));
    } catch (err) {
      setVisaConsultantRosterError(err.message);
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
      const data = await adminApiResponse(res, 'Failed to assign booking');
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
      const data = await adminApiResponse(res, 'Failed to save pricing settings');
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

  const adminNavItems = [
    { id: 'pricing', label: 'Pricing & Markup' },
    { id: 'loyalty', label: 'Loyalty Program' },
    { id: 'email-requests', label: 'Email Change Requests', count: emailRequests.length },
    { id: 'visa-bookings', label: 'Visa Consultation Bookings', count: visaConsultations.length },
    { id: 'cancellations', label: 'Cancellation Requests', count: cancellationRequests.length },
    { id: 'packages', label: 'Tour Packages' },
    { id: 'package-requests', label: 'Package Booking Requests', count: packageRequests.length },
    { id: 'visa-requirements', label: 'Visa Requirements' },
    { id: 'visa-consultants', label: 'Visa Consultants' },
    { id: 'assign-booking', label: 'Assign Booking' },
    { id: 'reports', label: 'Reports' },
    { id: 'tickets', label: 'Tickets & Invoices' },
  ];

  return (
    <div className="tab-content animate-fade">
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <h2 className="section-title" style={{ margin: 0 }}>Admin Portal — Pricing & Reports</h2>
          <button className="btn btn-secondary btn-sm" onClick={onAdminLogout}>Log Out</button>
        </div>
      </section>

      <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>
        <nav style={{ width: '230px', flexShrink: 0, position: 'sticky', top: '1rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          {adminNavItems.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveAdminSection(item.id)}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', textAlign: 'left',
                padding: '0.6rem 0.8rem', borderRadius: '8px', border: 'none', cursor: 'pointer',
                fontSize: '0.85rem', fontWeight: activeAdminSection === item.id ? 700 : 500,
                background: activeAdminSection === item.id ? 'var(--gs-crimson)' : 'transparent',
                color: activeAdminSection === item.id ? 'white' : 'var(--gs-dark)',
              }}
            >
              <span>{item.label}</span>
              {item.count > 0 && (
                <span style={{
                  fontSize: '0.7rem', fontWeight: 700, padding: '0.05rem 0.45rem', borderRadius: '999px',
                  background: activeAdminSection === item.id ? 'rgba(255,255,255,0.25)' : '#fef2f2',
                  color: activeAdminSection === item.id ? 'white' : '#991b1b',
                }}>
                  {item.count}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div style={{ flex: 1, minWidth: 0 }}>

      {activeAdminSection === 'pricing' && (
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
      )}

      {activeAdminSection === 'loyalty' && (
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
                <div className="form-group" style={{ marginBottom: '0.5rem' }}>
                  <label className="form-label">🥇 Gold at (points)</label>
                  <input type="number" min="0" step="1" className="form-input" value={loyaltySettings.tier_gold_threshold}
                    onChange={e => setLoyaltySettings(s => ({ ...s, tier_gold_threshold: parseInt(e.target.value) || 0 }))} />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label className="form-label">💎 Platinum at (points)</label>
                  <input type="number" min="0" step="1" className="form-input" value={loyaltySettings.tier_platinum_threshold}
                    onChange={e => setLoyaltySettings(s => ({ ...s, tier_platinum_threshold: parseInt(e.target.value) || 0 }))} />
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
      )}

      {activeAdminSection === 'email-requests' && (
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
      )}

      {activeAdminSection === 'visa-bookings' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Visa Consultation Bookings {visaConsultations.length > 0 && `(${visaConsultations.length} pending)`}</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '-0.5rem', marginBottom: '1rem' }}>
          Submitted from the B2C "Visa Requirements" tab. Each row also went out by email to the consultant
          mailbox unless flagged "email not sent" below — in that case, follow up manually.
        </p>
        {visaConsultationsError && <div className="error-banner">{visaConsultationsError}</div>}
        {visaConsultationsLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading bookings...</p></div>
        ) : visaConsultations.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No pending visa consultation bookings.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {visaConsultations.map(r => (
              <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <div>
                    <strong style={{ color: 'var(--gs-crimson)' }}>{r.slot_date} at {r.slot_time}</strong>
                    {' — '}{r.nationality} → {r.destination}
                    {!r.email_sent && <span style={{ color: '#b45309', fontWeight: 700 }}> · email not sent</span>}
                  </div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
                    {r.full_name} · {r.email} · {r.phone}
                  </div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    Consultant: {r.consultant_name ? `${r.consultant_name} (${r.consultant_email})` : 'none assigned — sent to general mailbox'}
                  </div>
                  {r.notes && <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>Notes: {r.notes}</div>}
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    Requested {new Date(r.requested_at).toLocaleString()}
                    {r.customer_id ? ' · Signed-in customer' : ' · Public form (not signed in)'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-primary btn-sm" disabled={visaConsultationActionId === r.id} onClick={() => reviewVisaConsultation(r.id, 'resolve')}>Mark Resolved</button>
                  <button className="btn btn-danger btn-sm" disabled={visaConsultationActionId === r.id} onClick={() => reviewVisaConsultation(r.id, 'reject')}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'cancellations' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Cancellation Requests {cancellationRequests.length > 0 && `(${cancellationRequests.length} pending)`}</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '-0.5rem', marginBottom: '1rem' }}>
          Submitted via the B2C "Cancel Booking" form. Verify the booking and requester, then cancel it manually
          via Travelport (All Bookings tab) before marking Resolved.
        </p>
        {cancellationRequestsError && <div className="error-banner">{cancellationRequestsError}</div>}
        {cancellationRequestsLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading requests...</p></div>
        ) : cancellationRequests.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No pending cancellation requests.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {cancellationRequests.map(r => (
              <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <div>
                    <strong style={{ color: 'var(--gs-crimson)' }}>{r.booking_locator}</strong>
                    {' — '}Travel date {r.travel_date}
                    {r.all_passengers_cancelling ? ' · All passengers' : ' · Partial passengers'}
                  </div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
                    {r.requester_name} · {r.email} · {r.phone}
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    Requested {new Date(r.requested_at).toLocaleString()}
                    {r.customer_id ? ' · Signed-in customer' : ' · Public form (not signed in)'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-primary btn-sm" disabled={cancellationRequestActionId === r.id} onClick={() => reviewCancellationRequest(r.id, 'resolve')}>Mark Resolved</button>
                  <button className="btn btn-danger btn-sm" disabled={cancellationRequestActionId === r.id} onClick={() => reviewCancellationRequest(r.id, 'reject')}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'packages' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem' }}>
          <h3 className="results-heading" style={{ margin: 0 }}>Tour Packages {packages.length > 0 && `(${packages.length})`}</h3>
          <button className="btn btn-primary btn-sm" onClick={() => openPackageForm(null)}>+ Add Package</button>
        </div>
        {packagesError && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{packagesError}</div>}

        {packageFormOpen && (
          <form onSubmit={handleSavePackage} style={{ marginTop: '1rem', marginBottom: '1.25rem', padding: '1rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
            <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>{editingPackageId ? 'Edit Package' : 'New Package'}</h4>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 2, minWidth: '220px' }}>
                <label className="form-label">Title *</label>
                <input className="form-input" required value={packageForm.title} onChange={e => setPackageForm(f => ({ ...f, title: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 2, minWidth: '180px' }}>
                <label className="form-label">Destination *</label>
                <input className="form-input" required value={packageForm.destination} onChange={e => setPackageForm(f => ({ ...f, destination: e.target.value }))} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '100px' }}>
                <label className="form-label">Days *</label>
                <input type="number" min="1" className="form-input" required value={packageForm.duration_days} onChange={e => setPackageForm(f => ({ ...f, duration_days: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '100px' }}>
                <label className="form-label">Nights *</label>
                <input type="number" min="0" className="form-input" required value={packageForm.duration_nights} onChange={e => setPackageForm(f => ({ ...f, duration_nights: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '140px' }}>
                <label className="form-label">Price *</label>
                <input type="number" min="0" step="0.01" className="form-input" required value={packageForm.price} onChange={e => setPackageForm(f => ({ ...f, price: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '100px' }}>
                <label className="form-label">Currency</label>
                <input className="form-input" value={packageForm.currency} onChange={e => setPackageForm(f => ({ ...f, currency: e.target.value }))} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Package Image / Poster</label>
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                {packageForm.image_url && (
                  <div style={{
                    width: '120px', height: '80px', flexShrink: 0, borderRadius: '8px', border: '1px solid var(--border-color)',
                    backgroundImage: `url(${packageForm.image_url})`, backgroundSize: 'cover', backgroundPosition: 'center',
                  }} />
                )}
                <div style={{ flex: 1, minWidth: '220px' }}>
                  <input
                    className="form-input" placeholder="https://... or upload a file below"
                    value={packageForm.image_url}
                    onChange={e => setPackageForm(f => ({ ...f, image_url: e.target.value }))}
                    style={{ marginBottom: '0.5rem' }}
                  />
                  <input
                    type="file" accept="image/jpeg,image/png,image/webp,image/gif"
                    disabled={packageImageUploading}
                    onChange={e => handlePackageImageUpload(e.target.files?.[0])}
                  />
                  {packageImageUploading && <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: '0.4rem 0 0' }}>Uploading…</p>}
                  {packageImageUploadError && <div className="error-banner" style={{ marginTop: '0.5rem' }}>{packageImageUploadError}</div>}
                </div>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Short Summary (shown on card)</label>
              <input className="form-input" maxLength={500} value={packageForm.summary} onChange={e => setPackageForm(f => ({ ...f, summary: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Full Description</label>
              <textarea className="form-input" rows={2} value={packageForm.description} onChange={e => setPackageForm(f => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Itinerary</label>
              <textarea className="form-input" rows={3} value={packageForm.itinerary} onChange={e => setPackageForm(f => ({ ...f, itinerary: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '220px' }}>
                <label className="form-label">Inclusions</label>
                <textarea className="form-input" rows={2} value={packageForm.inclusions} onChange={e => setPackageForm(f => ({ ...f, inclusions: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '220px' }}>
                <label className="form-label">Exclusions</label>
                <textarea className="form-input" rows={2} value={packageForm.exclusions} onChange={e => setPackageForm(f => ({ ...f, exclusions: e.target.value }))} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '160px' }}>
                <label className="form-label">Valid From</label>
                <input type="date" className="form-input" value={packageForm.valid_from} onChange={e => setPackageForm(f => ({ ...f, valid_from: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '160px' }}>
                <label className="form-label">Valid To</label>
                <input type="date" className="form-input" value={packageForm.valid_to} onChange={e => setPackageForm(f => ({ ...f, valid_to: e.target.value }))} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', marginBottom: '0.75rem', cursor: 'pointer' }}>
                <input type="checkbox" checked={packageForm.is_active} onChange={e => setPackageForm(f => ({ ...f, is_active: e.target.checked }))} />
                Active (visible to customers)
              </label>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button type="submit" className="btn btn-primary" disabled={packageSaving}>{packageSaving ? 'Saving…' : 'Save Package'}</button>
              <button type="button" className="btn btn-secondary" onClick={() => { setPackageFormOpen(false); setEditingPackageId(null); }}>Cancel</button>
            </div>
          </form>
        )}

        {packagesLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading packages...</p></div>
        ) : packages.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No packages yet — add one above.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {packages.map(pkg => (
              <div key={pkg.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.85rem' }}>
                  <div style={{
                    width: '56px', height: '40px', flexShrink: 0, borderRadius: '6px', border: '1px solid var(--border-color)',
                    background: pkg.image_url ? `url(${pkg.image_url}) center/cover no-repeat` : '#e2e8f0',
                  }} />
                  <div>
                    <strong>{pkg.title}</strong> — {pkg.destination} · {pkg.duration_days}D/{pkg.duration_nights}N · {pkg.currency} {Number(pkg.price).toLocaleString()}
                    {!pkg.is_active && <span style={{ marginLeft: '0.5rem', fontSize: '0.7rem', fontWeight: 700, color: '#b45309', background: '#fef3c7', padding: '0.1rem 0.5rem', borderRadius: '999px' }}>Inactive</span>}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-secondary btn-sm" onClick={() => openPackageForm(pkg)}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDeletePackage(pkg.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'package-requests' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <h3 className="results-heading">Package Booking Requests {packageRequests.length > 0 && `(${packageRequests.length} pending)`}</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '-0.5rem', marginBottom: '1rem' }}>
          Submitted via the B2C "Tour Packages" page. Confirm availability and arrange payment with the customer directly.
        </p>
        {packageRequestsError && <div className="error-banner">{packageRequestsError}</div>}
        {packageRequestsLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading requests...</p></div>
        ) : packageRequests.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No pending package booking requests.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {packageRequests.map(r => (
              <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <div><strong style={{ color: 'var(--gs-crimson)' }}>{r.package_title}</strong> — {r.num_travelers} traveler(s){r.preferred_date && ` · Preferred: ${r.preferred_date}`}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>{r.full_name} · {r.email} · {r.phone}</div>
                  {r.notes && <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem', fontStyle: 'italic' }}>"{r.notes}"</div>}
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>Requested {new Date(r.requested_at).toLocaleString()}</div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-primary btn-sm" disabled={packageRequestActionId === r.id} onClick={() => reviewPackageRequest(r.id, 'resolve')}>Confirm</button>
                  <button className="btn btn-danger btn-sm" disabled={packageRequestActionId === r.id} onClick={() => reviewPackageRequest(r.id, 'reject')}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'visa-requirements' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem' }}>
          <h3 className="results-heading" style={{ margin: 0 }}>Visa Requirements {visaRequirements.length > 0 && `(${visaRequirements.length})`}</h3>
          <button className="btn btn-primary btn-sm" onClick={() => openVisaForm(null)}>+ Add Requirement</button>
        </div>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '0.35rem', marginBottom: '0' }}>
          Country names must match exactly what customers pick on the B2C Visa page (e.g. "Sri Lanka", "United Kingdom").
        </p>
        {visaRequirementsError && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{visaRequirementsError}</div>}

        {visaFormOpen && (
          <form onSubmit={handleSaveVisaRequirement} style={{ marginTop: '1rem', marginBottom: '1.25rem', padding: '1rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
            <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>{editingVisaId ? 'Edit Requirement' : 'New Requirement'}</h4>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '200px' }}>
                <label className="form-label">Nationality *</label>
                <input className="form-input" required placeholder="e.g. Sri Lanka" value={visaForm.nationality} onChange={e => setVisaForm(f => ({ ...f, nationality: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '200px' }}>
                <label className="form-label">Destination *</label>
                <input className="form-input" required placeholder="e.g. United Kingdom" value={visaForm.destination} onChange={e => setVisaForm(f => ({ ...f, destination: e.target.value }))} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Visa Status *</label>
              <select className="form-input" required value={visaForm.visa_required} onChange={e => setVisaForm(f => ({ ...f, visa_required: e.target.value }))}>
                <option value="required">Visa Required</option>
                <option value="not_required">Visa Not Required</option>
                <option value="visa_on_arrival">Visa on Arrival</option>
                <option value="e_visa">e-Visa Available</option>
              </select>
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '180px' }}>
                <label className="form-label">Visa Type</label>
                <input className="form-input" placeholder="e.g. Tourist e-Visa" value={visaForm.visa_type} onChange={e => setVisaForm(f => ({ ...f, visa_type: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '180px' }}>
                <label className="form-label">Processing Time</label>
                <input className="form-input" placeholder="e.g. 3-5 business days" value={visaForm.processing_time} onChange={e => setVisaForm(f => ({ ...f, processing_time: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '180px' }}>
                <label className="form-label">Validity</label>
                <input className="form-input" placeholder="e.g. 30 days single entry" value={visaForm.validity} onChange={e => setVisaForm(f => ({ ...f, validity: e.target.value }))} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Notes</label>
              <textarea className="form-input" rows={2} value={visaForm.notes} onChange={e => setVisaForm(f => ({ ...f, notes: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button type="submit" className="btn btn-primary" disabled={visaSaving}>{visaSaving ? 'Saving…' : 'Save Requirement'}</button>
              <button type="button" className="btn btn-secondary" onClick={() => { setVisaFormOpen(false); setEditingVisaId(null); }}>Cancel</button>
            </div>
          </form>
        )}

        {visaRequirementsLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading visa requirements...</p></div>
        ) : visaRequirements.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem' }}>No visa requirements documented yet — add one above.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.75rem' }}>
            {visaRequirements.map(req => (
              <div key={req.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <strong>{req.nationality}</strong> → <strong>{req.destination}</strong>
                  <span style={{ marginLeft: '0.5rem', fontSize: '0.7rem', fontWeight: 700, padding: '0.1rem 0.5rem', borderRadius: '999px', background: '#e0f2fe', color: '#075985' }}>
                    {({ required: 'Required', not_required: 'Not Required', visa_on_arrival: 'On Arrival', e_visa: 'e-Visa' })[req.visa_required] || req.visa_required}
                  </span>
                  {req.visa_type && <span style={{ marginLeft: '0.5rem', color: 'var(--text-muted)' }}>· {req.visa_type}</span>}
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-secondary btn-sm" onClick={() => openVisaForm(req)}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDeleteVisaRequirement(req.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'visa-consultants' && (
      <section className="search-section glass-panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem' }}>
          <h3 className="results-heading" style={{ margin: 0 }}>Visa Consultants {visaConsultantRoster.length > 0 && `(${visaConsultantRoster.length})`}</h3>
          <button className="btn btn-primary btn-sm" onClick={() => openConsultantForm(null)}>+ Add Consultant</button>
        </div>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '0.35rem', marginBottom: '0' }}>
          One consultant per destination country. Shown to customers on the B2C Visa page once they pick that
          destination, and used to route consultation booking emails — falls back to the general
          VISA_CONSULTANT_EMAIL (.env) for any country with no consultant assigned here.
        </p>
        {visaConsultantRosterError && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{visaConsultantRosterError}</div>}

        {consultantFormOpen && (
          <form onSubmit={handleSaveVisaConsultant} style={{ marginTop: '1rem', marginBottom: '1.25rem', padding: '1rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
            <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', color: 'var(--gs-dark)' }}>{editingConsultantId ? 'Edit Consultant' : 'New Consultant'}</h4>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '200px' }}>
                <label className="form-label">Destination Country *</label>
                <input className="form-input" required placeholder="e.g. Sri Lanka" value={consultantForm.country} onChange={e => setConsultantForm(f => ({ ...f, country: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '200px' }}>
                <label className="form-label">Consultant Name *</label>
                <input className="form-input" required placeholder="e.g. Nadeesha Perera" value={consultantForm.consultant_name} onChange={e => setConsultantForm(f => ({ ...f, consultant_name: e.target.value }))} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ flex: 1, minWidth: '200px' }}>
                <label className="form-label">Email *</label>
                <input type="email" className="form-input" required value={consultantForm.email} onChange={e => setConsultantForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: '160px' }}>
                <label className="form-label">Phone</label>
                <input type="tel" className="form-input" value={consultantForm.phone} onChange={e => setConsultantForm(f => ({ ...f, phone: e.target.value }))} />
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', margin: '0.5rem 0' }}>
              <input type="checkbox" checked={consultantForm.is_active} onChange={e => setConsultantForm(f => ({ ...f, is_active: e.target.checked }))} />
              Active (shown to customers and used for routing)
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button type="submit" className="btn btn-primary" disabled={consultantSaving}>{consultantSaving ? 'Saving…' : 'Save Consultant'}</button>
              <button type="button" className="btn btn-secondary" onClick={() => { setConsultantFormOpen(false); setEditingConsultantId(null); }}>Cancel</button>
            </div>
          </form>
        )}

        {visaConsultantRosterLoading ? (
          <div className="loading-state"><div className="spinner"></div><p>Loading visa consultants...</p></div>
        ) : visaConsultantRoster.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem' }}>No visa consultants assigned yet — add one above.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.75rem' }}>
            {visaConsultantRoster.map(c => (
              <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem', background: '#f8fafc', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.85rem' }}>
                  <strong>{c.country}</strong> → {c.consultant_name}
                  {!c.is_active && (
                    <span style={{ marginLeft: '0.5rem', fontSize: '0.7rem', fontWeight: 700, padding: '0.1rem 0.5rem', borderRadius: '999px', background: '#fef2f2', color: '#991b1b' }}>Inactive</span>
                  )}
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{c.email}{c.phone && ` · ${c.phone}`}</div>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button className="btn btn-secondary btn-sm" onClick={() => openConsultantForm(c)}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDeleteVisaConsultant(c.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}

      {activeAdminSection === 'assign-booking' && (
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
      )}

      {activeAdminSection === 'reports' && (
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
      )}

      {activeAdminSection === 'tickets' && (
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
      )}

        </div>
      </div>
    </div>
  );
}
