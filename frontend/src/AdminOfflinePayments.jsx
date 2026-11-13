// Admin › Bank & Cash Payments (11/11/2026, item 12). Payment method switches
// (super admin) and the bank-transfer / cash payments waiting for staff to
// confirm the money arrived. Confirming issues the ticket and emails it — the
// same step as after a card payment (backend services/offline_payments.py).
import { useCallback, useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';
import IssuedTicketPopup from './IssuedTicketPopup.jsx';

// Amadeus bookings are paid under the reference AD-<Amadeus PNR> (13/11/2026).
const isAmadeus = (p) => String(p?.locator_code || '').toUpperCase().startsWith('AD-');

const money = (a, c) => (a || a === 0 ? `${c || ''} ${Number(a).toLocaleString(undefined, { minimumFractionDigits: 2 })}`.trim() : '—');
const STATUS = { awaiting: ['Awaiting payment', 'warn'], confirming: ['Issuing ticket…', 'warn'], ticketed: ['Ticketed', 'ok'],
  failed: ['Not ticketed', 'bad'], cancelled: ['Cancelled', 'muted'] };
const METHODS = [
  { key: 'card_enabled', label: 'Card (NDB / CyberSource)', desc: 'Customers pay by card on the bank’s secure page.' },
  { key: 'bank_enabled', label: 'Bank transfer / deposit', desc: 'Customer pays at the bank with the PNR as reference; staff confirm here, then the ticket is issued. All fares except Emirates NDC (card only).' },
  { key: 'cash_enabled', label: 'Cash', desc: 'Customers pay in cash at a Tick and Book office (or staff take cash at the counter) with the PNR as reference; staff confirm here, then the ticket is issued.' },
];

export default function AdminOfflinePayments({ API_BASE, adminToken, onSessionExpired, settingsOnly = false }) {
  const api = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}`, ...(opts.headers || {}) },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
    return data;
  }, [API_BASE, adminToken, onSessionExpired]);

  const [settings, setSettings] = useState(null);
  const [instructions, setInstructions] = useState('');
  const [holdHours, setHoldHours] = useState(12);
  const [payments, setPayments] = useState(null);
  const [filter, setFilter] = useState('awaiting');
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const [open, setOpen] = useState(null); // payment id being confirmed
  const [form, setForm] = useState({ received_amount: '', bank_reference: '', note: '' });
  const [view, setView] = useState(null); // {payment, justIssued}: the issued-ticket pop-up

  const loadSettings = useCallback(() => api('/payment-methods').then(d => {
    setSettings(d); setInstructions(d.bank_instructions || ''); setHoldHours(d.bank_hold_hours || 12);
  }).catch(e => setError(e.message)), [api]);
  const loadPayments = useCallback(() => api(`/offline-payments${filter === 'all' ? '' : `?status=${filter}`}`)
    .then(d => setPayments(d.payments || [])).catch(e => setError(e.message)), [api, filter]);
  useEffect(() => { loadSettings(); }, [loadSettings]);
  useEffect(() => { if (!settingsOnly) loadPayments(); }, [loadPayments, settingsOnly]);

  const saveSettings = async (values) => {
    setBusy('settings'); setError(''); setMsg('');
    try { const d = await api('/payment-methods', { method: 'PUT', body: JSON.stringify(values) }); setSettings(d); setMsg('Saved.'); }
    catch (e) { setError(e.message); }
    setBusy('');
  };

  const confirm = async (p) => {
    setBusy(`c${p.id}`); setError(''); setMsg('');
    try {
      const d = await api(`/offline-payments/${p.id}/confirm`, { method: 'POST', body: JSON.stringify({
        received_amount: Number(form.received_amount), bank_reference: form.bank_reference || null, note: form.note || null }) });
      setOpen(null); loadPayments();
      if (isAmadeus(p)) {
        // Amadeus booking (13/11/2026): the pop-up below checks Travelport
        // bookings only, so the issued ticket number(s) are shown here.
        const nums = (d.ticket_numbers && d.ticket_numbers.length ? d.ticket_numbers : [d.ticket_number]).filter(Boolean);
        setMsg(d.already_issued
          ? `${p.locator_code}: this booking was already ticketed (${nums.join(', ')}) - check how the customer paid before keeping this payment.`
          : `${p.locator_code}: Amadeus ticket${nums.length > 1 ? 's' : ''} ${nums.join(', ')} issued.`);
        setBusy('');
        return;
      }
      setMsg(`${p.locator_code}: ticket ${d.ticket_number || ''} issued.`);
      // Show the ticket straight away so staff can see it was issued properly and share it.
      const fresh = await api('/offline-payments').then(x => (x.payments || []).find(r => r.id === p.id)).catch(() => null);
      setView({ payment: fresh || { ...p, status: 'ticketed', ticket_number: d.ticket_number,
                                    received_amount: Number(form.received_amount), bank_reference: form.bank_reference || null },
                justIssued: true });
    } catch (e) { setError(`${p.locator_code}: ${e.message}`); loadPayments(); }
    setBusy('');
  };
  const cancel = async (p) => {
    setBusy(`x${p.id}`); setError(''); setMsg('');
    try { await api(`/offline-payments/${p.id}/cancel`, { method: 'POST' }); setMsg(`${p.locator_code}: cancelled.`); loadPayments(); }
    catch (e) { setError(e.message); }
    setBusy('');
  };

  return (
    <div className="ofp">
      {error && <div className="error-banner">{error}</div>}
      {msg && <div className="ofp-msg">{msg}</div>}

      {!settingsOnly && <h4 className="ofp-h">Payment methods</h4>}
      {!settings ? <div className="loading-state"><div className="spinner"></div></div> : (
        <div className="glc">
          {!settings.can_change && <div className="glc-note">Only the main admin account (<strong>admin</strong>) can change payment methods{settings.signed_in_as ? <> — you are signed in as <strong>{settings.signed_in_as}</strong></> : null}.</div>}
          {METHODS.map(m => {
            const on = !!settings[m.key];
            return (
              <div key={m.key} className={`glc-row ${on ? 'on' : 'off'}`}>
                <div className="glc-text"><div className="glc-label">{m.label}</div><div className="glc-desc">{m.desc}</div></div>
                <div className="glc-actions">
                  <span className={`glc-state ${on ? 'on' : 'off'}`}>{on ? 'ON' : 'OFF'}</span>
                  {settings.can_change && (
                    <button type="button" className="btn btn-secondary btn-sm" disabled={!!busy}
                            onClick={() => saveSettings({ [m.key]: !on })}>{on ? 'Turn off' : 'Turn on'}</button>
                  )}
                </div>
              </div>
            );
          })}
          <div className="ofp-bank">
            <label className="glc-label" htmlFor="ofp-bank-text">Bank details shown and emailed to customers</label>
            <textarea id="ofp-bank-text" className="form-input" rows={5} value={instructions} disabled={!settings.can_change}
                      placeholder={'Account name: …\nBank: …\nBranch: …\nAccount number: …'}
                      onChange={e => setInstructions(e.target.value)} />
            <div className="ofp-bank-row">
              <label>Customer must pay within
                <input type="number" min={1} max={168} className="form-input ofp-hours" value={holdHours} disabled={!settings.can_change}
                       onChange={e => setHoldHours(e.target.value)} /> hours</label>
              {settings.can_change && (
                <button type="button" className="btn btn-primary btn-sm" disabled={!!busy}
                        onClick={() => saveSettings({ bank_instructions: instructions, bank_hold_hours: Number(holdHours) || 12 })}>
                  Save bank details
                </button>
              )}
            </div>
            <div className="adm-sub ofp-wrap">The airline may release a held booking sooner than this — staff should confirm deposits promptly.</div>
          </div>
        </div>
      )}

      {!settingsOnly && (<>
      <h4 className="ofp-h">Payments waiting for confirmation</h4>
      <div className="adm-chips">
        {[['awaiting', 'Awaiting'], ['ticketed', 'Ticketed'], ['failed', 'Not ticketed'], ['cancelled', 'Cancelled'], ['all', 'All']].map(([k, l]) => (
          <button key={k} className="adm-chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
        ))}
        <button className="adm-chip" onClick={loadPayments}>↻ Refresh</button>
      </div>
      {!payments ? <div className="loading-state"><div className="spinner"></div></div> : payments.length === 0 ? (
        <p className="adm-sub">Nothing here.</p>
      ) : (
        <div className="adm-table-wrap">
          <table className="adm-table">
            <thead><tr><th>PNR / reference</th><th>Method</th><th>Customer</th><th>Amount</th><th>Pay by</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {payments.map(p => {
                const [label, cls] = STATUS[p.status] || [p.status, 'muted'];
                return (
                  <tr key={p.id}>
                    <td><strong>{p.locator_code}</strong>
                      {isAmadeus(p) && <div className="adm-sub">Amadeus booking {p.locator_code.slice(3)}</div>}
                      <div className="adm-sub">{fmtDateTime(p.created_at)}</div></td>
                    <td>{p.method === 'cash' ? 'Cash' : 'Bank transfer'}{p.started_by && <div className="adm-sub">by {p.started_by}</div>}</td>
                    <td>{p.customer_name || '—'}{p.customer_email && <div className="adm-sub">{p.customer_email}</div>}</td>
                    <td className="mono nowrap">{money(p.amount, p.currency)}
                      {p.received_amount != null && <div className="adm-sub">received {money(p.received_amount, p.currency)}{p.bank_reference ? ` · ${p.bank_reference}` : ''}</div>}</td>
                    <td className="nowrap">{p.pay_by ? fmtDateTime(p.pay_by) : '—'}</td>
                    <td><span className={`rl-chip ${cls}`}>{label}</span>
                      {p.ticket_number && <div className="adm-sub">ticket {p.ticket_number}</div>}
                      {p.confirmed_by && <div className="adm-sub">by {p.confirmed_by}</div>}
                      {p.error && <div className="recon-issue">{p.error}</div>}</td>
                    <td>
                      {p.status === 'awaiting' && open !== p.id && (
                        <div className="ofp-actions">
                          <button className="btn btn-primary btn-sm" disabled={!!busy}
                                  onClick={() => { setOpen(p.id); setForm({ received_amount: String(p.amount), bank_reference: '', note: '' }); }}>Confirm received</button>
                          <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => cancel(p)}>Cancel</button>
                        </div>
                      )}
                      {(p.status === 'ticketed' || p.status === 'failed') && !isAmadeus(p) && (
                        <button className="btn btn-secondary btn-sm" onClick={() => setView({ payment: p, justIssued: false })}>
                          {p.status === 'ticketed' ? 'View ticket & share' : 'Check booking'}</button>
                      )}
                      {open === p.id && (
                        <div className="ofp-form">
                          <label>Amount received ({p.currency})
                            <input className="form-input" type="number" step="0.01" value={form.received_amount}
                                   onChange={e => setForm(f => ({ ...f, received_amount: e.target.value }))} /></label>
                          <label>{p.method === 'cash' ? 'Receipt no.' : 'Bank reference / slip no.'}
                            <input className="form-input" value={form.bank_reference}
                                   onChange={e => setForm(f => ({ ...f, bank_reference: e.target.value }))} /></label>
                          <label>Note
                            <input className="form-input" value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} /></label>
                          <div className="ofp-actions">
                            <button className="btn btn-primary btn-sm" disabled={!!busy || !Number(form.received_amount)} onClick={() => confirm(p)}>
                              {busy === `c${p.id}` ? 'Issuing ticket…' : 'Confirm & issue ticket'}</button>
                            <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => setOpen(null)}>Back</button>
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </>)}
      {view && <IssuedTicketPopup API_BASE={API_BASE} adminToken={adminToken} payment={view.payment} justIssued={view.justIssued}
                                  onClose={() => setView(null)} onSessionExpired={onSessionExpired} />}
    </div>
  );
}
