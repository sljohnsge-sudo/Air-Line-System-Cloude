// Admin › Bank & Cash Payments › issued-ticket pop-up (11/11/2026). Opens by
// itself right after staff press "Confirm & issue ticket" (and from "View
// ticket" on a ticketed row) so staff can see, live from Travelport and the
// airline, that the ticket really was issued properly — ticket numbers,
// coupon status, the airline's flight confirmation — and then share it to
// the customer's email / mobile on record (same rules as Booking Lookup:
// staff pick a contact, never type one).
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { LiveStatusDetails } from './LiveBookingPanel.jsx';
import { fmtDateTime } from './dateFormat.js';

const money = (a, c) => (a || a === 0 ? `${c || ''} ${Number(a).toLocaleString(undefined, { minimumFractionDigits: 2 })}`.trim() : '—');

// Is the ticket issued properly? Read only from the live Travelport answer.
function checkTicket(d) {
  const problems = [];
  if (d.booking_status === 'cancelled') problems.push('The booking is cancelled on Travelport.');
  if (d.booking_status === 'airline_cancelled') problems.push('The airline has cancelled the flights.');
  const pending = (d.flights || []).filter(f => f.state !== 'confirmed');
  if (pending.length && d.booking_status !== 'airline_cancelled')
    problems.push(`Not confirmed by the airline: ${pending.map(f => f.flight).join(', ')}.`);
  const tickets = d.tickets || [];
  const pax = (d.travelers || []).length;
  if (!tickets.length) problems.push('Travelport shows no ticket number on this booking.');
  else if (pax && tickets.length < pax) problems.push(`${tickets.length} ticket${tickets.length === 1 ? '' : 's'} for ${pax} passengers.`);
  const bad = tickets.flatMap(t => (t.coupons || []).filter(c => /void|refund|exchang/i.test(c.status || ''))
    .map(c => `${t.number} ${c.flight} ${c.status}`));
  if (bad.length) problems.push(`Ticket coupons not usable: ${bad.join(', ')}.`);
  // OpenForUse, or OtherAirlineControl once the airline takes it over for check-in.
  const open = tickets.reduce((n, t) => n + (t.coupons || []).filter(c => ['OpenForUse', 'OtherAirlineControl'].includes(c.status)).length, 0);
  const noDetail = tickets.filter(t => t.error).length;
  return { ok: problems.length === 0, problems, tickets: tickets.length, pax, open, noDetail };
}

export default function IssuedTicketPopup({ API_BASE, adminToken, payment, justIssued, onClose, onSessionExpired }) {
  const locator = payment.locator_code;
  const [live, setLive] = useState(null);
  const [liveErr, setLiveErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [share, setShare] = useState(null);
  const [shareErr, setShareErr] = useState('');
  const [shareMsg, setShareMsg] = useState('');
  const [sending, setSending] = useState('');

  const get = useCallback(async (path, opts = {}) => {
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
    });
    if (res.status === 401) { onSessionExpired?.(); throw new Error('Your session expired — please log in again.'); }
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : `Request failed (${res.status})`);
    return d;
  }, [API_BASE, adminToken, onSessionExpired]);

  const loadLive = useCallback(() => {
    setLoading(true); setLiveErr('');
    get(`/bookings/${locator}/live`).then(setLive).catch(e => setLiveErr(e.message)).finally(() => setLoading(false));
  }, [get, locator]);
  const loadShare = useCallback(() => {
    get(`/admin/bookings/${locator}/share-contacts`).then(setShare).catch(e => setShareErr(e.message));
  }, [get, locator]);
  useEffect(() => { loadLive(); loadShare(); }, [loadLive, loadShare]);
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const send = async (c) => {
    setSending(`${c.channel}-${c.value}`); setShareErr(''); setShareMsg('');
    try {
      const d = await get(`/admin/bookings/${locator}/share-ticket`, { method: 'POST', body: JSON.stringify({ channel: c.channel, contact: c.value }) });
      if (d.channel === 'whatsapp') {
        window.open(`https://wa.me/${d.whatsapp_number_digits}?text=${encodeURIComponent(d.share_text)}`, '_blank');
        setShareMsg(`WhatsApp opened with the ticket details for ${d.sent_to} — press Send in WhatsApp.`);
      } else {
        setShareMsg(`Ticket emailed to ${d.sent_to}.`);
      }
    } catch (e) { setShareErr(e.message); }
    setSending('');
  };

  const v = live ? checkTicket(live) : null;
  const ticketed = share?.ticketed;

  return createPortal(
    <div className="modal-overlay itp-overlay" onClick={onClose}>
      <div className="itp" role="dialog" aria-label={`Ticket ${locator}`} onClick={e => e.stopPropagation()}>
        <div className="itp-head">
          <div>
            <div className="itp-kicker">{justIssued ? 'Ticket issued' : 'Ticket'} · {payment.method === 'cash' ? 'Cash' : 'Bank transfer'}</div>
            <h3>{locator}{payment.customer_name ? <span> · {payment.customer_name}</span> : null}</h3>
          </div>
          <button type="button" className="itp-x" aria-label="Close" onClick={onClose}>×</button>
        </div>

        <div className="itp-pay">
          <span>Paid <strong>{money(payment.received_amount ?? payment.amount, payment.currency)}</strong></span>
          {payment.bank_reference && <span>{payment.method === 'cash' ? 'Receipt' : 'Slip'} <strong>{payment.bank_reference}</strong></span>}
          {payment.confirmed_by && <span>Confirmed by <strong>{payment.confirmed_by}</strong></span>}
          {payment.confirmed_at && <span>{fmtDateTime(payment.confirmed_at)}</span>}
        </div>

        {loading && !live && <div className="itp-check wait"><span className="spinner itp-spin"></span> Checking the ticket with Travelport and the airline…</div>}
        {liveErr && <div className="error-banner">Could not check with Travelport: {liveErr}</div>}
        {v && (
          <div className={`itp-check ${v.ok ? 'ok' : 'bad'}`}>
            <strong>{v.ok ? '✓ Ticket issued properly' : '⚠ Check this ticket'}</strong>
            {v.ok ? (
              <span>
                {v.tickets} ticket{v.tickets === 1 ? '' : 's'}{v.pax ? ` for ${v.pax} passenger${v.pax === 1 ? '' : 's'}` : ''}, every flight confirmed by the airline
                {v.open ? `, ${v.open} flight coupon${v.open === 1 ? '' : 's'} valid for travel` : ''}.
                {v.noDetail ? ' The airline did not return coupon detail for one ticket just now — the ticket number is on the booking.' : ''}
              </span>
            ) : <ul>{v.problems.map(p => <li key={p}>{p}</li>)}</ul>}
          </div>
        )}

        {live && (
          <div className="itp-live">
            <div className="itp-live-head">
              <span>Live from Travelport and the airline · checked {fmtDateTime(live.retrieved_at)}</span>
              <button type="button" className="btn btn-secondary btn-sm" disabled={loading} onClick={loadLive}>{loading ? 'Checking…' : 'Check again'}</button>
            </div>
            <LiveStatusDetails data={live} />
          </div>
        )}

        <div className="itp-share">
          <h4>Share the ticket with the customer</h4>
          <div className="adm-sub ofp-wrap">Only the email and mobile number the customer gave, or their account’s verified ones — nothing can be typed in.</div>
          {shareErr && <div className="error-banner">{shareErr}</div>}
          {shareMsg && <div className="ofp-msg">{shareMsg}</div>}
          {share && !ticketed && <div className="adm-sub share-pop-wrap">Not ticketed yet — there is no ticket to share.</div>}
          {share && share.contacts.length === 0 && <div className="adm-sub share-pop-wrap">No email or mobile number on record for this booking.</div>}
          {share?.contacts.map(c => (
            <div key={`${c.channel}-${c.value}`} className="share-pop-row">
              <div>
                <div className="share-pop-value">{c.channel === 'email' ? '✉️' : '📱'} {c.value}</div>
                <div className="adm-sub">{c.source}</div>
              </div>
              <button type="button" className="btn btn-primary btn-sm" disabled={!!sending || !ticketed} onClick={() => send(c)}>
                {sending === `${c.channel}-${c.value}` ? 'Sending…' : c.channel === 'email' ? 'Send email' : 'Open WhatsApp'}
              </button>
            </div>
          ))}
        </div>

        <div className="itp-foot">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
