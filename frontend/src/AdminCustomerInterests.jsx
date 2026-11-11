// Admin › Customers & Insights › Offers & Interests (11/11/2026). Customers who
// switched special notifications ON in My Profile, with their favourite
// destinations / airlines / hotels; filter (e.g. "Dubai", "Emirates") and send
// them a special offer by email / SMS. Only opted-in customers are listed.
// Backend: services/customer_interests.py.
import { useCallback, useEffect, useState } from 'react';
import { fmtDateTime } from './dateFormat.js';

export default function AdminCustomerInterests({ API_BASE, adminToken, onSessionExpired }) {
  const [q, setQ] = useState('');
  const [topic, setTopic] = useState('');
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [picked, setPicked] = useState([]);
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const api = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}/admin/customer-interests${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : `Request failed (${res.status})`);
    return d;
  }, [API_BASE, adminToken, onSessionExpired]);

  const load = useCallback(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set('q', q.trim());
    if (topic) p.set('topic', topic);
    api(`?${p.toString()}`).then(d => { setData(d); setPicked(d.customers.map(c => c.customer_id)); }).catch(e => setErr(e.message));
  }, [api, q, topic]);
  useEffect(() => { load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic]);

  const send = async () => {
    setBusy(true); setErr(''); setResult(null);
    try { setResult(await api('/send', { method: 'POST', body: JSON.stringify({ subject, message, customer_ids: picked, filters: { q, topic } }) })); load(); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!data) return err ? <div className="error-banner">{err}</div> : <div className="loading-state"><div className="spinner"></div></div>;
  const chips = (list) => list.map(x => <span key={x} className="ci-tag ci-tag-sm">{x}</span>);

  return (
    <div className="aci">
      {err && <div className="error-banner">{err}</div>}
      <div className="recon-tiles">
        <div className="recon-tile ok"><span>Customers who want offers</span><b>{data.opted_in_total}</b><small>switched ON in My Profile</small></div>
        {['destinations', 'airlines', 'hotels'].map(k => (
          <div key={k} className="recon-tile"><span>Top {k}</span>
            <small className="aci-top">{data.top[k].length ? data.top[k].slice(0, 5).map(t => `${t.name} (${t.count})`).join(' · ') : 'none yet'}</small></div>
        ))}
      </div>

      <form className="aci-filter" onSubmit={e => { e.preventDefault(); load(); }}>
        <input className="recon-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Find by favourite — e.g. Dubai, Emirates, Maldives" />
        <select className="form-input aci-sel" value={topic} onChange={e => setTopic(e.target.value)}>
          <option value="">Any interest</option>
          {Object.entries(data.topic_labels).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button className="btn btn-secondary btn-sm" type="submit">Search</button>
      </form>

      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead><tr><th></th><th>Customer</th><th>Destinations</th><th>Airlines</th><th>Hotels / other</th><th>Wants</th><th>By</th></tr></thead>
          <tbody>
            {data.customers.map(c => (
              <tr key={c.customer_id}>
                <td><input type="checkbox" checked={picked.includes(c.customer_id)}
                           onChange={() => setPicked(p => p.includes(c.customer_id) ? p.filter(x => x !== c.customer_id) : [...p, c.customer_id])} /></td>
                <td><strong>{c.name || '—'}</strong><div className="adm-sub">{c.email}</div></td>
                <td>{chips(c.destinations)}</td>
                <td>{chips(c.airlines)}</td>
                <td>{chips(c.hotels)}{c.other && <div className="adm-sub ofp-wrap">{c.other}</div>}</td>
                <td className="adm-sub ofp-wrap">{c.topics.map(t => data.topic_labels[t]).join(', ')}</td>
                <td className="adm-sub">{c.channels.map(t => data.channel_labels[t]).join(', ')}</td>
              </tr>
            ))}
            {!data.customers.length && <tr><td colSpan={7} className="adm-sub">No customers match{q ? ` "${q}"` : ''} yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="aci-send">
        <h4>Send a special offer to the {picked.length} selected customer{picked.length === 1 ? '' : 's'}</h4>
        <input className="form-input" maxLength={200} value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject — e.g. Dubai from LKR 89,900 this weekend" />
        <textarea className="form-input" rows={4} maxLength={4000} value={message} onChange={e => setMessage(e.target.value)} placeholder="Your offer…" />
        <div className="ofp-actions">
          <button className="btn btn-primary btn-sm" disabled={busy || !picked.length || subject.trim().length < 3 || message.trim().length < 5} onClick={send}>
            {busy ? 'Sending…' : 'Send offer'}</button>
        </div>
        <div className="adm-sub ofp-wrap">Sent by email (and SMS where the customer chose it and SMS is set up). WhatsApp: open the customer's number from Customer 360.</div>
        {result && (
          <div className={result.sent ? 'ofp-msg' : 'error-banner'}>
            Sent to {result.sent} of {result.recipients}.{result.not_sent ? ` Not sent: ${result.not_sent}.` : ''}
            {result.problems?.length ? ` (${result.problems.join('; ')})` : ''}
          </div>
        )}
      </div>

      {data.history?.length > 0 && (
        <>
          <h4 className="ofp-h">Offers sent</h4>
          <div className="adm-table-wrap">
            <table className="adm-table">
              <thead><tr><th>When</th><th>Subject</th><th>By</th><th>Customers</th><th>Sent</th><th>Not sent</th></tr></thead>
              <tbody>{data.history.map(h => (
                <tr key={h.id}><td className="nowrap">{fmtDateTime(h.created_at)}</td><td>{h.subject}</td><td>{h.sent_by}</td><td>{h.recipients}</td><td>{h.sent_ok}</td><td>{h.sent_failed}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
