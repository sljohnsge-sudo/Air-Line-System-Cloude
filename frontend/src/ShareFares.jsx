// Share fares from the search results with friends by WhatsApp or email.
// The customer ticks one or more fare cards; each tick stores a summary of
// that fare as shown on the card at that moment (flights, cabin, baggage,
// price). Sharing is for signed-in customers: ticking a fare while signed
// out asks them to create a free account or sign in first, and brings them
// back to the same results afterwards (App.jsx, tb_after_sign_in).
//
// Each shared fare goes as a picture of the fare card (drawn here on a
// canvas from the fare data — drawFareCard) plus the text summary and the
// link:
//   WhatsApp  phones: the picture + text through the share sheet (pick
//             WhatsApp); computers: download the picture, then WhatsApp
//             opens with the text and link to attach it to.
//   Email     sent from Tick and Book's own address to the address the
//             customer types (POST /api/customers/share-fare/email), or the
//             customer's own mail app with the text only.
//
// The picture and text can't be booked and their price is only "at the time
// of sharing". The link re-runs the same search live and shows the shared
// flights first, with the airline's price right now — that, never the
// message, is what gets booked.
import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import whatsappIcon from './assets/whatsapp_icon.png';
import { encryptForUrl } from './urlCrypto.js';
import { fmtDateWeekday, fmtDateTime } from './dateFormat.js';

export const MAX_SHARE_FARES = 5;

const fmtTime = (iso) => iso?.split('T')[1]?.slice(0, 5) || iso?.split(' ')[1]?.slice(0, 5) || '';
const isoDay = (iso) => iso?.split('T')[0] || iso?.split(' ')[0] || '';
const fmtDate = (iso) => {
  const d = isoDay(iso);
  if (!d) return '';
  return fmtDateWeekday(d);
};
const fmtDuration = (iso) => iso ? iso.replace('PT', '').replace('H', 'h ').replace('M', 'm').trim() : '';
const cabinName = (c) => c === 'PremiumEconomy' ? 'Premium Economy' : (c || 'Economy');
const titleCase = (s) => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

const legsOf = (flight) => flight.legs?.length
  ? [...flight.legs].sort((a, b) => (a.departure_time || '').localeCompare(b.departure_time || ''))
  : [flight];
const segsOf = (leg) => leg.segments?.length ? leg.segments : [leg];

// The same flights in a later live search for the same dates: their flight
// numbers (offer ids and prices change between searches, these don't). Kept
// short because it travels in the shared link.
export const flightIdentity = (flight) =>
  legsOf(flight).map(l => segsOf(l).map(s => s.flight_number).join('.')).join('-');

const fmtMinutes = (m) => m > 0 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`.replace(/^0h /, '') : '';
const minutesBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 60000);
const terminal = (t) => t ? ` T${t}` : '';
// "SecondCheckedBag" -> "Second Checked Bag"
const bagName = (t) => (t || 'Baggage').replace(/([a-z])([A-Z])/g, '$1 $2');
// Days between two ISO dates' calendar days (arrival "+1").
const dayShift = (fromIso, toIso) => {
  const a = Date.parse(isoDay(fromIso));
  const b = Date.parse(isoDay(toIso));
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 86400000) : 0;
};

// Everything on one fare card, as the customer sees it now: each flight of
// each leg with times, terminals, aircraft and stopover, plus fare, baggage,
// seat selection and price.
export function buildShareItem({ flight, cabin, fareOption, price, currency, grandTotal, paxLabel }) {
  const legs = legsOf(flight).map((leg, i, all) => {
    const segs = segsOf(leg);
    const first = segs[0];
    const last = segs[segs.length - 1];
    const segments = segs.map((s, k) => {
      const next = segs[k + 1];
      const stopMinutes = next ? (s.layover_minutes ?? minutesBetween(s.arrival_time, next.departure_time)) : 0;
      return {
        flightNo: s.flight_number,
        // Another airline's flight on this ticket (a 2-letter code alone adds nothing to the flight number).
        carrier: s.carrier_name && s.carrier_name.length > 3 && s.carrier_name !== flight.airline ? s.carrier_name : null,
        aircraft: s.aircraft_type || (segs.length === 1 ? leg.aircraft_type || flight.aircraft_type : null),
        dep: `${fmtTime(s.departure_time)} ${s.departure_airport}${terminal(s.departure_terminal)}`,
        arr: `${fmtTime(s.arrival_time)} ${s.arrival_airport}${terminal(s.arrival_terminal)}`,
        arrDate: isoDay(s.arrival_time) !== isoDay(first.departure_time) ? fmtDate(s.arrival_time) : null,
        duration: fmtDuration(s.duration),
        stopover: next ? `${s.arrival_airport}${fmtMinutes(stopMinutes) ? ` · ${fmtMinutes(stopMinutes)}` : ''}` : null,
      };
    });
    return {
      outbound: i === 0,
      label: all.length === 2 ? (i === 0 ? 'Depart' : 'Return') : all.length > 2 ? `Flight ${i + 1}` : 'Depart',
      from: first.departure_airport,
      to: last.arrival_airport,
      date: fmtDate(first.departure_time),
      stops: segs.length === 1 ? 'Non-stop' : `${segs.length - 1} stop${segs.length > 2 ? 's' : ''}`,
      duration: fmtDuration(leg.duration),
      depTime: fmtTime(first.departure_time),
      arrTime: fmtTime(last.arrival_time),
      arrShift: dayShift(first.departure_time, last.arrival_time),
      segments,
    };
  });
  const bags = (fareOption?.baggage_allowance || []).map(b => `${bagName(b.type)}: ${b.allowance}`);
  const total = `${currency} ${Math.round(grandTotal || price).toLocaleString()}`;
  return {
    key: flightIdentity(flight),
    airline: `${flight.airline}${flight.airline_code ? ` (${flight.airline_code})` : ''}`,
    airlineName: flight.airline || '',
    airlineCode: flight.airline_code || '',
    logoUrl: flight.airline_logo_url || '',
    // "Economy Classic" says the cabin already; "Saver" alone doesn't.
    fare: fareOption?.brand_name
      ? (titleCase(fareOption.brand_name).includes(cabinName(cabin)) ? titleCase(fareOption.brand_name) : `${cabinName(cabin)} · ${titleCase(fareOption.brand_name)}`)
      : cabinName(cabin),
    cabin: `${cabinName(cabin)} Class`,
    seats: flight.seat_map_heuristic === true ? 'Seat selection available' : 'Seat selection not available',
    seatsLeft: flight.seats_remaining != null && flight.seats_remaining < 5 ? `Only ${flight.seats_remaining} seats left` : null,
    legs,
    bags,
    total,
    paxLabel,
    sharedAt: new Date().toISOString(),
    card: screenCardData(flight, { price, grandTotal, currency, paxLabel }),
    label: `${flight.airline} · ${legs[0].from} → ${legs[0].to} · ${total}`,
  };
}

// Link back to our site: the same search, run live, shared flights first.
function shareLink(items, search) {
  const token = encryptForUrl(JSON.stringify({ ...search, step: null, shared: items.map(i => i.key) }));
  const base = (import.meta.env.VITE_PUBLIC_SITE_URL || window.location.origin).replace(/\/$/, '');
  return token ? `${base}/results/${token}` : base;
}

// WhatsApp shows *text* as bold and _text_ as italic; email gets plain text.
export function shareText(items, link, { bold, italic }) {
  const pad = '      ';
  const blocks = items.map((it, n) => [
    `${bold(`${items.length > 1 ? `${n + 1}. ` : ''}${it.airline}`)}`,
    `${it.fare}`,
    ...it.legs.flatMap(l => [
      '',
      `${l.outbound ? '🛫' : '🛬'} ${bold(`${l.label.toUpperCase()} · ${l.from} → ${l.to}`)}`,
      `${pad}${l.date} · ${l.stops}${l.duration ? ` · ${l.duration} total` : ''}`,
      ...l.segments.flatMap(s => [
        `${pad}${bold(s.flightNo)}${s.carrier ? ` · ${s.carrier}` : ''}${s.aircraft ? ` · ${s.aircraft}` : ''}`,
        `${pad}${s.dep} → ${s.arr}${s.arrDate ? ` (${s.arrDate})` : ''}${s.duration ? ` · ${s.duration}` : ''}`,
        ...(s.stopover ? [`${pad}⏳ Stopover in ${s.stopover}`] : []),
      ]),
    ]),
    '',
    `💺 ${it.cabin} · ${it.seats}${it.seatsLeft ? ` · ${it.seatsLeft}` : ''}`,
    ...(it.bags.length ? [`🧳 ${it.bags.join(' · ')}`] : []),
    `💰 ${bold(it.total)}${it.paxLabel ? ` for ${it.paxLabel}` : ''}`,
  ].join('\n'));
  return [
    `✈️ ${bold(items.length > 1 ? `${items.length} flight options for you` : 'A flight option for you')}`,
    ...blocks,
    [
      `👉 ${bold('See the live price & book:')}`,
      link,
      italic('Prices shown are from when this was shared and change with seat availability. The live price on the link is the price you pay.'),
    ].join('\n'),
  ].join(items.length > 1 ? '\n\n━━━━━━━━━━\n\n' : '\n\n');
}

// ── Picture of a fare card ──────────────────────────────────────────────────
// Drawn from the fare data (no screenshot library). Tick and Book colours.
const C = {
  navy: '#123B70', navyDeep: '#0C2A52', sky: '#72BCEB', ice: '#EAF4FC', champagne: '#F5E9D8',
  coral: '#E98270', text: '#24364B', muted: '#52657C', border: '#D6E3EF', green: '#15803d', white: '#FFFFFF',
};
const FONT = "'Plus Jakarta Sans', Arial, Helvetica, sans-serif";
const IMG_W = 1080;
const PAD = 56;

const font = (ctx, size, weight = 400) => { ctx.font = `${weight} ${size}px ${FONT}`; };

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function fit(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > maxW) t = t.slice(0, -1);
  return `${t}…`;
}

function wrap(ctx, text, maxW) {
  const lines = [];
  let line = '';
  for (const word of String(text).split(' ')) {
    const test = line ? `${line} ${word}` : word;
    if (line && ctx.measureText(test).width > maxW) { lines.push(line); line = word; } else line = test;
  }
  if (line) lines.push(line);
  return lines.map(l => fit(ctx, l, maxW));
}

// The airline's logo only when it can't taint the canvas (our own site or
// an inline data image); otherwise the airline code in a circle.
function usableLogo(url) {
  if (!url) return null;
  if (url.startsWith('data:image/')) return url;
  try {
    const u = new URL(url, window.location.href);
    return u.origin === window.location.origin ? u.href : null;
  } catch { return null; }
}

async function loadImage(src) {
  if (!src) return null;
  try {
    const img = new Image();
    img.src = src;
    await img.decode();
    return img;
  } catch { return null; }
}

// The site's one date format (DD/MM/YYYY HH:mm), in the sharer's local time.
const fmtSharedAt = (iso) => fmtDateTime(iso ? new Date(iso) : new Date());


// ── Picture that looks like the fare card on the results page ──────────────
// (11/11/2026) The owner wants the shared picture to be the card the customer
// sees: airline column, both legs with times / airports / dates / stop chip /
// layover / flight times, price column — plus a slim Tick and Book footer.
// Drawn on a canvas in the page's own colours; the airline logo comes from
// our backend (/api/airline-logo/XX) so it never taints the canvas.
const SC = {
  text: '#0f172a', sub: '#1e293b', muted: '#64748b', faint: '#94a3b8', line: '#cbd5e1', border: '#e2e8f0',
  crimson: '#c3122e', panel: '#fdf1f2', green: '#15803d', greenDark: '#1d6b3a', white: '#ffffff',
  chipBg: '#f1f5f9', layBg: '#fdebd3', layText: '#9a3412', navy: '#0C2A52', sky: '#72BCEB',
};
const CARD_W = 1500;
const LEFT_W = 290;
const RIGHT_W = 230;
const LEG_H = 168;
const HEAD_H = 46;
const FOOT_H = 58;
const SCALE = 2;

const dmy = (iso) => { const d = isoDay(iso); if (!d) return ''; const [y, m, dd] = d.split('-'); return `${dd}/${m}/${y}`; };
const dm = (iso) => dmy(iso).slice(0, 5);

export function screenCardData(flight, { price, grandTotal, currency, paxLabel }) {
  const legs = legsOf(flight).map((leg) => {
    const segs = segsOf(leg);
    const first = segs[0];
    const last = segs[segs.length - 1];
    return {
      depTime: fmtTime(first.departure_time), depApt: first.departure_airport || '', depTerm: first.departure_terminal || '',
      depDate: dmy(first.departure_time),
      arrTime: fmtTime(last.arrival_time), arrApt: last.arrival_airport || '', arrTerm: last.arrival_terminal || '',
      arrDate: dmy(last.arrival_time),
      segDur: segs.map(s => fmtDuration(s.duration)),
      stops: segs.slice(0, -1).map((s, k) => {
        const next = segs[k + 1];
        const mins = s.layover_minutes ?? minutesBetween(s.arrival_time, next.departure_time);
        const a = dmy(s.arrival_time);
        const b = dmy(next.departure_time);
        return {
          apt: s.arrival_airport || '', term: s.arrival_terminal || '',
          times: `${fmtTime(s.arrival_time)} → ${fmtTime(next.departure_time)}`,
          date: !b || a === b ? a : `${dm(s.arrival_time)} → ${dm(next.departure_time)}`,
          layover: fmtMinutes(mins),
        };
      }),
    };
  });
  const firstSeg = segsOf(legsOf(flight)[0])[0] || {};
  const maxStops = Math.max(...legs.map(l => l.stops.length));
  return {
    totalDuration: fmtDuration(flight.duration || legsOf(flight)[0]?.duration),
    flightNo: firstSeg.flight_number || flight.flight_number || '',
    aircraft: flight.aircraft_type || firstSeg.aircraft_type || '',
    fareSource: (flight.fare_source || 'GDS').toUpperCase(),
    provider: flight.source === 'AD' ? 'AD · Amadeus' : 'TP · Travelport',
    stopsLabel: maxStops === 0 ? 'Non-stop' : `${maxStops} Stop${maxStops > 1 ? 's' : ''}`,
    seatSelection: flight.seat_map_heuristic === true,
    currency: currency || 'LKR',
    amount: Math.round(grandTotal || price || 0).toLocaleString(),
    paxLabel: paxLabel || '',
    legs,
  };
}

function pill(ctx, x, y, text, { bg, fg, border, size = 14, weight = 800, padX = 9, h = 24, align = 'left', r = 5 }) {
  font(ctx, size, weight);
  const w = ctx.measureText(text).width + padX * 2;
  const left = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
  roundRect(ctx, left, y, w, h, r);
  if (bg) { ctx.fillStyle = bg; ctx.fill(); }
  if (border) { ctx.strokeStyle = border; ctx.lineWidth = 1.5; ctx.stroke(); }
  ctx.fillStyle = fg;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  ctx.fillText(text, left + padX, y + h / 2 + 1);
  ctx.textBaseline = 'alphabetic';
  return w;
}

function textAt(ctx, text, x, y, { size = 16, weight = 400, color = SC.text, align = 'left', max } = {}) {
  font(ctx, size, weight);
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.fillText(max ? fit(ctx, text, max) : text, x, y);
  ctx.textAlign = 'left';
}

function dashed(ctx, x1, x2, y, color, dash) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.setLineDash(dash || []);
  ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
  ctx.restore();
}

export function screenCardHeight(card) {
  const body = Math.max(card.legs.length * LEG_H, 320);
  return HEAD_H + body + FOOT_H;
}

function paintScreenCard(ctx, it, { logo, siteHost }) {
  const card = it.card;
  const W = CARD_W;
  const H = screenCardHeight(card);
  const bodyTop = HEAD_H;
  const bodyH = H - HEAD_H - FOOT_H;
  ctx.fillStyle = SC.white;
  ctx.fillRect(0, 0, W, H);

  // Header: Total Duration
  if (card.totalDuration) textAt(ctx, `Total Duration: ${card.totalDuration}`, W / 2, 30, { size: 17, weight: 800, color: '#1a1a1a', align: 'center' });

  // Left column
  ctx.fillStyle = SC.border;
  ctx.fillRect(LEFT_W, bodyTop, 1, bodyH);
  const cx = LEFT_W / 2;
  let y = bodyTop + 14;
  roundRect(ctx, cx - 32, y, 64, 64, 12);
  ctx.fillStyle = SC.white; ctx.fill();
  ctx.strokeStyle = SC.border; ctx.lineWidth = 1.5; ctx.stroke();
  if (logo) {
    ctx.drawImage(logo, cx - 26, y + 6, 52, 52);
  } else {
    textAt(ctx, it.airlineCode || '✈', cx, y + 41, { size: 20, weight: 800, color: SC.crimson, align: 'center' });
  }
  y += 92;
  textAt(ctx, it.airlineName || '', cx, y, { size: 18, weight: 700, color: SC.sub, align: 'center', max: LEFT_W - 24 });
  y += 14;
  // badges row
  font(ctx, 14, 800);
  const badges = [
    [it.airlineCode, '#f1f5f9', '#64748b'],
    [card.fareSource, card.fareSource === 'NDC' ? '#f5d0fe' : card.fareSource === 'LCC' ? '#fef08a' : '#bae6fd',
      card.fareSource === 'NDC' ? '#701a75' : card.fareSource === 'LCC' ? '#713f12' : '#0369a1'],
    [card.provider, card.provider.startsWith('AD') ? '#ddd6fe' : '#bbf7d0', card.provider.startsWith('AD') ? '#4c1d95' : '#14532d'],
  ].filter(b => b[0]);
  const widths = badges.map(b => ctx.measureText(b[0]).width + 18);
  let bx = cx - (widths.reduce((a, b) => a + b, 0) + 8 * (badges.length - 1)) / 2;
  badges.forEach((b, i) => { pill(ctx, bx, y, b[0], { bg: b[1], fg: b[2], size: 14, padX: 9, h: 26, r: 4 }); bx += widths[i] + 8; });
  y += 56;
  if (card.flightNo) { textAt(ctx, card.flightNo, cx, y, { size: 16, weight: 600, color: SC.faint, align: 'center' }); y += 32; }
  if (card.aircraft) { textAt(ctx, `✈ ${card.aircraft}`, cx, y, { size: 15, weight: 500, color: SC.faint, align: 'center', max: LEFT_W - 24 }); y += 18; }
  pill(ctx, cx, y, card.stopsLabel, { bg: '#fef3c7', fg: '#92400e', border: '#fde68a', size: 15, weight: 700, padX: 14, h: 28, align: 'center', r: 14 });
  y += 40;
  if (card.seatSelection) pill(ctx, cx, y, '✓ Seat Selection Available', { bg: '#ecfdf3', fg: SC.green, border: '#a7dcb6', size: 14, weight: 700, padX: 12, h: 30, align: 'center', r: 6 });

  // Right (price) column
  const rx = W - RIGHT_W;
  ctx.fillStyle = SC.panel;
  ctx.fillRect(rx, bodyTop, RIGHT_W, bodyH);
  const pcx = rx + RIGHT_W / 2;
  const pTop = bodyTop + Math.max(28, bodyH / 2 - 110);
  pill(ctx, pcx, pTop, it.cabin || 'Economy Class', { fg: SC.greenDark, border: SC.greenDark, size: 16, weight: 700, padX: 16, h: 34, align: 'center', r: 17 });
  textAt(ctx, `from ${card.currency}`, pcx, pTop + 76, { size: 18, weight: 500, color: SC.muted, align: 'center' });
  textAt(ctx, card.amount, pcx, pTop + 116, { size: 36, weight: 800, color: SC.greenDark, align: 'center' });
  if (card.paxLabel) textAt(ctx, `for ${card.paxLabel}`, pcx, pTop + 144, { size: 15, weight: 600, color: SC.greenDark, align: 'center' });
  textAt(ctx, 'price at the time of sharing', pcx, pTop + 170, { size: 13, weight: 500, color: SC.faint, align: 'center' });

  // Legs
  const mx0 = LEFT_W;
  const mx1 = rx;
  const legH = bodyH / card.legs.length;
  card.legs.forEach((leg, i) => {
    const top = bodyTop + i * legH;
    if (i > 0) dashed(ctx, mx0 + 16, mx1 - 16, top, SC.border, [6, 6]);
    pill(ctx, mx0 + 22, top + 10, i === 0 ? 'Depart' : card.legs.length === 2 ? 'Return' : `Flight ${i + 1}`,
      { bg: '#eef2f7', fg: '#475569', size: 12, weight: 800, padX: 8, h: 22, r: 6 });
    const cy = top + legH / 2 + 10;
    const lx = mx0 + 30;
    const rxText = mx1 - 28;
    // departure
    textAt(ctx, leg.depTime, lx, cy - 4, { size: 30, weight: 800, color: SC.text });
    textAt(ctx, leg.depApt, lx, cy + 22, { size: 19, weight: 800, color: SC.crimson });
    if (leg.depTerm) { font(ctx, 19, 800); const w = ctx.measureText(leg.depApt).width; textAt(ctx, ` Terminal ${leg.depTerm}`, lx + w, cy + 22, { size: 13, weight: 500, color: SC.muted }); }
    textAt(ctx, leg.depDate, lx, cy + 46, { size: 16, weight: 500, color: SC.muted });
    // arrival
    textAt(ctx, leg.arrTime, rxText, cy - 4, { size: 30, weight: 800, color: SC.text, align: 'right' });
    let termW = 0;
    if (leg.arrTerm) {
      font(ctx, 13, 500); termW = ctx.measureText(` Terminal ${leg.arrTerm}`).width;
      textAt(ctx, ` Terminal ${leg.arrTerm}`, rxText, cy + 22, { size: 13, weight: 500, color: SC.muted, align: 'right' });
    }
    textAt(ctx, leg.arrApt, rxText - termW, cy + 22, { size: 19, weight: 800, color: SC.crimson, align: 'right' });
    textAt(ctx, leg.arrDate, rxText, cy + 46, { size: 16, weight: 500, color: SC.muted, align: 'right' });
    // route line
    const ly = cy + 8;
    const x1 = lx + 120;
    const x2 = rxText - 150;
    const n = leg.stops.length;
    const chipX = leg.stops.map((_, k) => x1 + (x2 - x1) * (k + 1) / (n + 1));
    const bounds = [x1, ...chipX, x2];
    for (let k = 0; k < bounds.length - 1; k++) {
      const a = bounds[k] + (k > 0 ? 58 : 0);
      const b = bounds[k + 1] - (k < bounds.length - 2 ? 58 : 0);
      dashed(ctx, a, b, ly, SC.line);
      const dur = leg.segDur[k];
      if (dur) pill(ctx, (a + b) / 2, ly + 14, dur, { bg: '#f8fafc', fg: '#475569', size: 14, weight: 600, padX: 9, h: 24, align: 'center', r: 5 });
    }
    leg.stops.forEach((st, k) => {
      const x = chipX[k];
      dashed(ctx, x - 58, x + 58, ly, SC.line, [3, 5]);
      textAt(ctx, st.times, x, ly - 40, { size: 15, weight: 800, color: SC.muted, align: 'center' });
      textAt(ctx, st.date, x, ly - 24, { size: 12, weight: 500, color: SC.faint, align: 'center' });
      pill(ctx, x, ly - 14, `${st.apt}${st.term ? ` T${st.term}` : ''}`, { bg: SC.white, fg: SC.crimson, border: SC.line, size: 17, weight: 800, padX: 14, h: 28, align: 'center', r: 14 });
      if (st.layover) pill(ctx, x, ly + 22, `⟳ ${st.layover}`, { bg: SC.layBg, fg: SC.layText, size: 16, weight: 800, padX: 12, h: 30, align: 'center', r: 7 });
    });
    if (n === 0) {
      // plane mark in the middle of a non-stop line
      textAt(ctx, '✈', (x1 + x2) / 2, ly + 6, { size: 18, weight: 700, color: SC.crimson, align: 'center' });
    }
  });

  // Footer
  const fy = H - FOOT_H;
  ctx.fillStyle = SC.navy;
  ctx.fillRect(0, fy, W, FOOT_H);
  textAt(ctx, 'Tick and Book', 28, fy + 37, { size: 22, weight: 800, color: SC.white });
  font(ctx, 22, 800);
  const bw = ctx.measureText('Tick and Book').width;
  textAt(ctx, 'Flights  |  Hotels  |  Travel', 28 + bw + 16, fy + 36, { size: 14, weight: 600, color: SC.sky });
  textAt(ctx, `Shared ${fmtSharedAt(it.sharedAt)} · fares change — see today's price${siteHost ? ` on ${siteHost}` : ''}`,
    W - 28, fy + 36, { size: 14, weight: 500, color: '#dbeafe', align: 'right' });
  return H;
}

// Paints the card from the top; returns the height used.
function paintFareCard(ctx, it, { logo, siteHost }) {
  const W = IMG_W;
  const inner = W - PAD * 2;
  ctx.fillStyle = C.white;
  ctx.fillRect(0, 0, W, ctx.canvas.height);
  ctx.textBaseline = 'alphabetic';

  // Header band
  const grad = ctx.createLinearGradient(0, 0, W, 0);
  grad.addColorStop(0, C.navyDeep);
  grad.addColorStop(1, C.navy);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, 116);
  ctx.fillStyle = C.white;
  font(ctx, 40, 800);
  ctx.textAlign = 'left';
  ctx.fillText('Tick and Book', PAD, 64);
  ctx.fillStyle = C.sky;
  font(ctx, 20, 600);
  ctx.fillText('Flight option shared with you', PAD, 96);
  ctx.textAlign = 'right';
  ctx.fillStyle = C.white;
  font(ctx, 18, 500);
  ctx.fillText('Shared', W - PAD, 60);
  font(ctx, 20, 700);
  ctx.fillText(fmtSharedAt(it.sharedAt), W - PAD, 88);

  // Airline
  let y = 152;
  const cx = PAD + 42;
  const cy = y + 42;
  ctx.beginPath();
  ctx.arc(cx, cy, 42, 0, Math.PI * 2);
  ctx.fillStyle = C.ice;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = C.border;
  ctx.stroke();
  if (logo) {
    const s = 60;
    const ratio = Math.min(s / logo.naturalWidth, s / logo.naturalHeight) || 1;
    const w = logo.naturalWidth * ratio;
    const h = logo.naturalHeight * ratio;
    ctx.drawImage(logo, cx - w / 2, cy - h / 2, w, h);
  } else {
    ctx.fillStyle = C.navy;
    font(ctx, 28, 800);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText((it.airlineCode || it.airlineName.slice(0, 2) || '✈').toUpperCase(), cx, cy + 1);
    ctx.textBaseline = 'alphabetic';
  }
  const tx = PAD + 108;
  ctx.textAlign = 'left';
  ctx.fillStyle = C.text;
  font(ctx, 34, 800);
  ctx.fillText(fit(ctx, it.airlineName || it.airline, inner - 108 - 220), tx, y + 36);
  const flightNos = it.legs.flatMap(l => l.segments.map(s => s.flightNo)).filter(Boolean).join(' · ');
  ctx.fillStyle = C.muted;
  font(ctx, 21, 600);
  ctx.fillText(fit(ctx, [it.airlineCode, flightNos].filter(Boolean).join('  |  '), inner - 108 - 220), tx, y + 72);
  // Fare / cabin pill on the right
  font(ctx, 20, 700);
  const pill = fit(ctx, it.fare || it.cabin, 260);
  const pw = ctx.measureText(pill).width + 32;
  roundRect(ctx, W - PAD - pw, y + 18, pw, 44, 22);
  ctx.fillStyle = C.ice;
  ctx.fill();
  ctx.fillStyle = C.navy;
  ctx.textAlign = 'center';
  ctx.fillText(pill, W - PAD - pw / 2, y + 47);
  y += 116;

  // Legs
  for (const leg of it.legs) {
    ctx.strokeStyle = C.border;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(PAD, y);
    ctx.lineTo(W - PAD, y);
    ctx.stroke();
    y += 34;

    // Label pill + date + stops
    font(ctx, 18, 800);
    const lab = leg.label.toUpperCase();
    const lw = ctx.measureText(lab).width + 28;
    roundRect(ctx, PAD, y, lw, 36, 18);
    ctx.fillStyle = leg.outbound ? C.navy : C.sky;
    ctx.fill();
    ctx.fillStyle = leg.outbound ? C.white : C.navyDeep;
    ctx.textAlign = 'center';
    ctx.fillText(lab, PAD + lw / 2, y + 25);
    ctx.textAlign = 'left';
    ctx.fillStyle = C.text;
    font(ctx, 24, 700);
    ctx.fillText(leg.date, PAD + lw + 18, y + 27);
    ctx.textAlign = 'right';
    font(ctx, 22, 800);
    ctx.fillStyle = leg.segments.length > 1 ? C.coral : C.green;
    ctx.fillText(leg.stops, W - PAD, y + 27);
    y += 60;

    // Times, line, airports
    const timeY = y + 52;
    ctx.fillStyle = C.text;
    font(ctx, 60, 800);
    ctx.textAlign = 'left';
    ctx.fillText(leg.depTime, PAD, timeY);
    const depW = ctx.measureText(leg.depTime).width;
    ctx.textAlign = 'right';
    let arrRight = W - PAD;
    if (leg.arrShift > 0) {
      font(ctx, 22, 800);
      ctx.fillStyle = C.coral;
      const shift = `+${leg.arrShift}`;
      ctx.fillText(shift, arrRight, timeY - 30);
      arrRight -= ctx.measureText(shift).width + 6;
      ctx.fillStyle = C.text;
      font(ctx, 60, 800);
    }
    ctx.fillText(leg.arrTime, arrRight, timeY);
    const arrW = ctx.measureText(leg.arrTime).width + (W - PAD - arrRight);

    const x1 = PAD + depW + 36;
    const x2 = W - PAD - arrW - 36;
    const lineY = timeY - 20;
    ctx.strokeStyle = C.sky;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x1, lineY);
    ctx.lineTo(x2, lineY);
    ctx.stroke();
    const dot = (x, fill) => { ctx.beginPath(); ctx.arc(x, lineY, 7, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); };
    dot(x1, C.navy);
    dot(x2, C.navy);
    const stopCount = leg.segments.length - 1;
    for (let k = 1; k <= stopCount; k++) {
      const sx = x1 + ((x2 - x1) * k) / (stopCount + 1);
      ctx.beginPath();
      ctx.arc(sx, lineY, 9, 0, Math.PI * 2);
      ctx.fillStyle = C.white;
      ctx.fill();
      ctx.lineWidth = 3;
      ctx.strokeStyle = C.coral;
      ctx.stroke();
    }
    ctx.textAlign = 'center';
    if (leg.duration) {
      ctx.fillStyle = C.muted;
      font(ctx, 20, 700);
      ctx.fillText(`${leg.duration} total`, (x1 + x2) / 2, lineY - 16);
    }
    const stopsAt = leg.segments.filter(s => s.stopover).map(s => s.stopover).join('  ·  ');
    if (stopsAt) {
      ctx.fillStyle = C.coral;
      font(ctx, 19, 700);
      ctx.fillText(fit(ctx, `via ${stopsAt}`, x2 - x1), (x1 + x2) / 2, lineY + 34);
    }
    y = timeY + 40;
    ctx.fillStyle = C.navy;
    font(ctx, 30, 800);
    ctx.textAlign = 'left';
    ctx.fillText(leg.from, PAD, y);
    ctx.textAlign = 'right';
    ctx.fillText(leg.to, W - PAD, y);
    y += 40;

    // Each flight of the leg
    ctx.textAlign = 'left';
    for (const s of leg.segments) {
      font(ctx, 20, 700);
      ctx.fillStyle = C.text;
      const head = [s.flightNo, s.carrier, s.aircraft].filter(Boolean).join(' · ');
      ctx.fillText(fit(ctx, head, inner * 0.45), PAD, y);
      font(ctx, 20, 500);
      ctx.fillStyle = C.muted;
      const times = `${s.dep} → ${s.arr}${s.arrDate ? ` (${s.arrDate})` : ''}${s.duration ? ` · ${s.duration}` : ''}`;
      ctx.textAlign = 'right';
      ctx.fillText(fit(ctx, times, inner * 0.55 - 12), W - PAD, y);
      ctx.textAlign = 'left';
      y += 32;
      if (s.stopover) {
        font(ctx, 19, 600);
        ctx.fillStyle = C.coral;
        ctx.fillText(fit(ctx, `Stopover in ${s.stopover}`, inner), PAD + 18, y);
        y += 32;
      }
    }
    y += 12;
  }

  // Cabin, baggage, seats + price panel
  y += 8;
  const leftW = inner - 360;
  font(ctx, 20, 500);
  const bagLines = it.bags.length ? wrap(ctx, `Baggage: ${it.bags.join(' · ')}`, leftW - 40) : [];
  const seatLine = `${it.seats}${it.seatsLeft ? ` · ${it.seatsLeft}` : ''}`;
  const leftH = 40 + 34 + bagLines.length * 30 + 30 + 28;
  const panelH = Math.max(leftH, 170);
  roundRect(ctx, PAD, y, inner, panelH, 18);
  ctx.fillStyle = C.ice;
  ctx.fill();
  ctx.textAlign = 'left';
  let ly = y + 48;
  ctx.fillStyle = C.text;
  font(ctx, 24, 800);
  ctx.fillText(fit(ctx, it.cabin, leftW - 40), PAD + 28, ly);
  ly += 36;
  font(ctx, 20, 500);
  ctx.fillStyle = C.muted;
  for (const l of bagLines) { ctx.fillText(l, PAD + 28, ly); ly += 30; }
  ctx.fillText(fit(ctx, seatLine, leftW - 40), PAD + 28, ly);

  // Price block
  const px = W - PAD - 28;
  ctx.textAlign = 'right';
  ctx.fillStyle = C.muted;
  font(ctx, 20, 600);
  ctx.fillText('from', px, y + 44);
  ctx.fillStyle = C.navy;
  font(ctx, 48, 800);
  ctx.fillText(fit(ctx, it.total, 340), px, y + 98);
  ctx.fillStyle = C.muted;
  font(ctx, 19, 600);
  ctx.fillText(fit(ctx, it.paxLabel ? `for ${it.paxLabel}` : 'total price', 340), px, y + 130);
  y += panelH + 36;

  // Note + where to book
  ctx.textAlign = 'left';
  ctx.fillStyle = C.muted;
  font(ctx, 19, 500);
  const note = `Price at the time of sharing (${fmtSharedAt(it.sharedAt)}). Prices change with seat availability — open the link in the message for the live price and to book.`;
  for (const l of wrap(ctx, note, inner)) { ctx.fillText(l, PAD, y); y += 28; }
  if (siteHost) {
    y += 10;
    ctx.fillStyle = C.navy;
    font(ctx, 22, 800);
    ctx.fillText(`Book on ${siteHost}`, PAD, y);
    y += 10;
  }
  return y + PAD - 10;
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob(b => (b ? resolve(b) : reject(new Error('Could not make the picture'))), 'image/png');
    } catch (e) { reject(e); }
  });
}

// One PNG of a fare card: { name, blob, file, url (object URL), dataUrl }.
export async function drawFareCard(item, { siteHost, index = 0 } = {}) {
  try { await document.fonts?.load?.(`800 40px 'Plus Jakarta Sans'`); } catch { /* falls back to Arial */ }
  const logo = item.card && item.airlineCode
    ? await loadImage(`/api/airline-logo/${encodeURIComponent(item.airlineCode)}`)
    : await loadImage(usableLogo(item.logoUrl));
  const render = item.card ? (withLogo) => {
    const out = document.createElement('canvas');
    const h = screenCardHeight(item.card);
    out.width = CARD_W * SCALE;
    out.height = h * SCALE;
    const ctx = out.getContext('2d');
    ctx.scale(SCALE, SCALE);
    paintScreenCard(ctx, item, { logo: withLogo ? logo : null, siteHost });
    return out;
  } : (withLogo) => {
    const scratch = document.createElement('canvas');
    scratch.width = IMG_W;
    scratch.height = 3200;
    const h = Math.ceil(paintFareCard(scratch.getContext('2d'), item, { logo: withLogo ? logo : null, siteHost }));
    const out = document.createElement('canvas');
    out.width = IMG_W;
    out.height = h;
    out.getContext('2d').drawImage(scratch, 0, 0, IMG_W, h, 0, 0, IMG_W, h);
    return out;
  };
  let canvas = render(true);
  let dataUrl;
  try {
    dataUrl = canvas.toDataURL('image/png');
  } catch {
    // The logo tainted the canvas after all: draw the airline code instead.
    canvas = render(false);
    dataUrl = canvas.toDataURL('image/png');
  }
  const blob = await canvasToBlob(canvas);
  const route = item.legs[0] ? `${item.legs[0].from}-${item.legs[0].to}` : 'flight';
  const name = `tick-and-book-${item.airlineCode || 'flight'}-${route}${index ? `-${index + 1}` : ''}.png`.replace(/[^A-Za-z0-9._-]/g, '');
  const file = typeof File === 'function' ? new File([blob], name, { type: 'image/png' }) : null;
  return { name, blob, file, url: URL.createObjectURL(blob), dataUrl };
}

const siteHostOf = (link) => {
  try { return new URL(link).host; } catch { return ''; }
};

// ── Sign-in prompt ───────────────────────────────────────────────────────────
function SignInToSharePrompt({ onSignIn, onClose }) {
  const btn = { border: 'none', borderRadius: '8px', padding: '0.6rem 1rem', fontWeight: 700, fontSize: '0.85rem', cursor: 'pointer' };
  return (
    // Clicks stop here: in React they would still bubble up to the fare card.
    <div role="dialog" aria-modal="true" aria-labelledby="share-signin-title" onClick={e => { e.stopPropagation(); onClose(); }} style={{
      position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(12,42,82,0.45)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#ffffff', borderRadius: '14px', maxWidth: '400px', width: '100%', boxSizing: 'border-box',
        padding: '1.4rem', boxShadow: '0 20px 50px rgba(12,42,82,0.3)', color: C.text, textAlign: 'left',
      }}>
        <h3 id="share-signin-title" style={{ margin: '0 0 0.5rem', fontSize: '1.05rem', color: C.navy }}>
          Create a free account or sign in to share fares
        </h3>
        <p style={{ margin: '0 0 1.1rem', fontSize: '0.85rem', lineHeight: 1.5, color: C.muted }}>
          Share a picture of this fare with a link to the live price by WhatsApp or email.
          You'll come back to these results after signing in.
        </p>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <button type="button" onClick={() => onSignIn('register')} style={{ ...btn, background: C.navy, color: '#ffffff', flex: '1 1 150px' }}>
            Create free account
          </button>
          <button type="button" onClick={() => onSignIn('login')} style={{ ...btn, background: C.ice, color: C.navy, flex: '1 1 100px' }}>
            Sign in
          </button>
        </div>
        <button type="button" onClick={onClose} style={{ ...btn, background: 'none', color: C.muted, padding: '0.6rem 0', marginTop: '0.4rem' }}>
          Not now
        </button>
      </div>
    </div>
  );
}

// The tick box on a fare card. signedIn === false: ticking asks the customer
// to create an account or sign in first (onSignIn('register' | 'login')).
export function ShareToggle({ checked, onChange, signedIn, onSignIn }) {
  const [asking, setAsking] = useState(false);
  return (
    <>
      <label title="Tick to share this fare by WhatsApp or email" style={{
        display: 'inline-flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer',
        fontSize: '0.68rem', fontWeight: 600, color: checked ? '#245d38' : '#64748b', userSelect: 'none',
      }}>
        <input type="checkbox" checked={checked}
               onChange={e => {
                 if (e.target.checked && signedIn === false) { setAsking(true); return; }
                 onChange(e.target.checked);
               }}
               style={{ accentColor: '#245d38', width: '13px', height: '13px', margin: 0, cursor: 'pointer' }} />
        Share this fare
      </label>
      {/* On <body>: a fare card's transform would otherwise hold the fixed overlay inside the card. */}
      {asking && createPortal(
        <SignInToSharePrompt
          onClose={() => setAsking(false)}
          onSignIn={(mode) => { setAsking(false); onSignIn?.(mode); }}
        />,
        document.body,
      )}
    </>
  );
}

// Shown on results opened from a shared link.
export function SharedFaresBanner({ count, found, onDismiss }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem',
      padding: '0.75rem 1rem', borderRadius: '10px', background: '#f0fdf4', border: '1px solid #86efac', color: '#14532d',
    }}>
      <div style={{ flex: '1 1 260px', fontSize: '0.85rem' }}>
        <strong>Shared with you:</strong>{' '}
        {found > 0
          ? <>the {found === 1 ? 'flight is' : `${found} flights are`} shown first, marked “Shared with you”. Prices are live from the airline now and may differ from the message.</>
          : <>the shared flight{count > 1 ? 's are' : ' is'} no longer available. Here are the flights on sale now for the same trip.</>}
        {found > 0 && found < count && ` ${count - found} of the shared flights are no longer available.`}
      </div>
      <button type="button" onClick={onDismiss} style={{ background: 'none', border: '1px solid #86efac', borderRadius: '6px', padding: '0.35rem 0.7rem', color: '#14532d', fontWeight: 700, fontSize: '0.75rem', cursor: 'pointer' }}>
        Show all flights
      </button>
    </div>
  );
}

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

// Bar pinned to the bottom of the screen while one or more fares are ticked.
// customerToken + apiBase: for sending by email from our server.
export function ShareFaresBar({ items, search, onClear, customerToken, apiBase = '/api', onSignIn, autoOpen = false, onAutoOpened }) {
  const [copied, setCopied] = useState(false);
  const [panel, setPanel] = useState(null); // 'choose' | 'whatsapp' | 'email' | 'text' | null
  const [images, setImages] = useState(null); // null = drawing, [] = couldn't draw
  const [emailTo, setEmailTo] = useState('');
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState(null); // { ok, text }
  const visible = items.length > 0;
  const tooMany = items.length > MAX_SHARE_FARES;
  const itemsKey = items.map(i => `${i.key}|${i.total}|${i.fare}`).join(',');

  // The share options pop up by themselves: when the first fare is ticked,
  // and when the customer comes back signed in to share (autoOpen).
  const prevCount = useRef(0);
  useEffect(() => {
    if ((items.length > 0 && prevCount.current === 0) || (autoOpen && items.length > 0)) {
      setPanel('choose');
      if (autoOpen) onAutoOpened?.();
    }
    prevCount.current = items.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length, autoOpen]);

  // Room at the bottom of the page so the bar never covers the last card.
  useEffect(() => {
    if (!visible) return undefined;
    const prev = document.body.style.paddingBottom;
    document.body.style.paddingBottom = '110px';
    return () => { document.body.style.paddingBottom = prev; };
  }, [visible]);

  // The pictures are drawn as soon as fares are ticked, so the phone share
  // sheet can open straight from the button press.
  useEffect(() => {
    setStatus(null);
    if (!visible || tooMany) { setImages(null); return undefined; }
    let cancelled = false;
    let made = [];
    setImages(null);
    const host = siteHostOf((import.meta.env.VITE_PUBLIC_SITE_URL || window.location.origin));
    Promise.all(items.map((it, i) => drawFareCard(it, { siteHost: host, index: items.length > 1 ? i : 0 })))
      .then(r => {
        made = r;
        if (cancelled) r.forEach(x => URL.revokeObjectURL(x.url));
        else setImages(r);
      })
      .catch(() => { if (!cancelled) setImages([]); });
    return () => { cancelled = true; made.forEach(x => URL.revokeObjectURL(x.url)); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, visible, tooMany]);

  if (!visible) return null;

  const link = shareLink(items, search);
  const whatsappText = shareText(items, link, { bold: s => `*${s}*`, italic: s => `_${s}_` });
  const plainText = shareText(items, link, { bold: s => s, italic: s => s });
  const subject = items.length > 1 ? `${items.length} flight options for you` : `Flight option: ${items[0].label}`;
  const whatsappUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(whatsappText)}`;
  const files = (images || []).map(x => x.file).filter(Boolean);
  const canShareFiles = (() => {
    try { return files.length > 0 && !!navigator.canShare?.({ files }); } catch { return false; }
  })();
  const isPhone = (() => {
    try { return window.matchMedia('(pointer: coarse)').matches; } catch { return false; }
  })();
  const picturesWord = items.length > 1 ? 'pictures' : 'picture';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked — the WhatsApp and email buttons still work */ }
  };

  // Phones: picture + text through the share sheet. The text is also copied,
  // since some apps keep only the picture when both are shared.
  const shareFiles = async () => {
    try { navigator.clipboard?.writeText(whatsappText).catch(() => {}); } catch { /* clipboard off */ }
    try {
      await navigator.share({ files, text: whatsappText, title: subject });
      setStatus({ ok: true, text: `Shared. The message with the link was also copied — paste it in the chat if only the ${picturesWord} arrived.` });
      return true;
    } catch (err) {
      if (err?.name === 'AbortError') return true;
      return false;
    }
  };

  // Text message: phones share the picture + message through the share
  // sheet (Messages etc.); elsewhere the message app opens with the text and
  // link, and the picture can be downloaded and attached.
  const smsUrl = `sms:?&body=${encodeURIComponent(plainText)}`;
  const onText = async () => {
    setStatus(null);
    if (isPhone && canShareFiles && await shareFiles()) return;
    setPanel(p => (p === 'text' ? null : 'text'));
  };

  const onWhatsApp = async () => {
    setStatus(null);
    if (isPhone && canShareFiles && await shareFiles()) return;
    setPanel(p => (p === 'whatsapp' ? null : 'whatsapp'));
  };

  const sendEmail = async (e) => {
    e.preventDefault();
    const to = emailTo.trim();
    if (!EMAIL_RE.test(to)) { setStatus({ ok: false, text: 'Please enter one valid email address.' }); return; }
    if (!customerToken) { setStatus({ ok: false, text: 'Please sign in to send by email.', signIn: true }); return; }
    if (!images?.length) { setStatus({ ok: false, text: 'The fare picture is still being made. Please try again in a moment.' }); return; }
    setSending(true);
    setStatus(null);
    try {
      const res = await fetch(`${apiBase}/customers/share-fare/email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${customerToken}` },
        body: JSON.stringify({
          to,
          results_path: new URL(link, window.location.origin).pathname,
          text: plainText,
          images: images.map(x => x.dataUrl),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) { setStatus({ ok: false, text: 'Your sign-in has expired. Please sign in again to send by email.', signIn: true }); return; }
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : "We couldn't send the email just now. Please try again in a minute.");
      setStatus({ ok: true, text: `Sent to ${to}.` });
      setEmailTo('');
    } catch (err) {
      setStatus({ ok: false, text: err.message || "We couldn't send the email just now." });
    } finally {
      setSending(false);
    }
  };

  const btn = { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', border: 'none', borderRadius: '6px', padding: '0.5rem 0.9rem', fontWeight: 700, fontSize: '0.8rem', cursor: 'pointer', textDecoration: 'none', whiteSpace: 'nowrap' };
  const small = { fontSize: '0.75rem', color: '#475569', lineHeight: 1.45 };
  const thumbs = images?.length ? (
    <div style={{ display: 'flex', gap: '0.5rem', overflowX: 'auto', paddingBottom: '0.25rem' }}>
      {images.map((img, i) => (
        <a key={img.url} href={img.url} download={img.name} title={`Download picture ${i + 1}`} style={{ flex: '0 0 auto' }}>
          <img src={img.url} alt={`Fare picture ${i + 1}`} style={{ height: '110px', borderRadius: '6px', border: '1px solid #e2e8f0', display: 'block' }} />
        </a>
      ))}
    </div>
  ) : (
    <div style={small}>{images === null && !tooMany ? 'Making the fare picture…' : tooMany ? '' : "The fare picture couldn't be made in this browser — the message and link still work."}</div>
  );

  return (
    <div style={{
      position: 'fixed', left: '50%', bottom: '16px', transform: 'translateX(-50%)', zIndex: 900,
      width: 'min(760px, calc(100% - 32px))', boxSizing: 'border-box', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto',
      background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: '12px', boxShadow: '0 10px 30px rgba(15,23,42,0.18)',
      padding: '0.7rem 0.9rem', display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
    }}>
      {panel === 'choose' && !tooMany && (
        <div style={{ flex: '1 1 100%', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.7rem', display: 'grid', gap: '0.6rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
            <div style={{ fontWeight: 800, fontSize: '0.92rem', color: '#1e293b' }}>Share {items.length > 1 ? `these ${items.length} fares` : 'this fare'}</div>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close" style={{ background: 'none', border: 'none', fontSize: '1.2rem', lineHeight: 1, color: '#64748b', cursor: 'pointer' }}>×</button>
          </div>
          {images?.length ? (
            <img src={images[0].url} alt="The fare picture your friends will receive"
                 style={{ width: '100%', maxWidth: '640px', borderRadius: '8px', border: '1px solid #e2e8f0', display: 'block' }} />
          ) : thumbs}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.5rem' }}>
            <button type="button" onClick={onWhatsApp} style={{ ...btn, justifyContent: 'center', padding: '0.7rem', background: '#25d366', color: '#ffffff' }}>
              <img src={whatsappIcon} alt="" style={{ width: '18px', height: '18px' }} /> WhatsApp
            </button>
            <button type="button" onClick={() => { setStatus(null); setPanel('email'); }} style={{ ...btn, justifyContent: 'center', padding: '0.7rem', background: '#1e293b', color: '#ffffff' }}>
              ✉ Email
            </button>
            <button type="button" onClick={onText} style={{ ...btn, justifyContent: 'center', padding: '0.7rem', background: '#2563eb', color: '#ffffff' }}>
              💬 Text message
            </button>
          </div>
          <div style={small}>Your friends receive this picture with a link to the live price on Tick and Book.</div>
        </div>
      )}
      {panel === 'text' && !tooMany && (
        <div style={{ flex: '1 1 100%', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.7rem', display: 'grid', gap: '0.5rem' }}>
          <div style={{ fontWeight: 800, fontSize: '0.85rem', color: '#1e293b' }}>Send as a text message</div>
          {thumbs}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {(images || []).map((img, i) => (
              <a key={img.url} href={img.url} download={img.name} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>
                ⬇ Download {images.length > 1 ? `picture ${i + 1}` : 'image'}
              </a>
            ))}
            <a href={smsUrl} style={{ ...btn, background: '#2563eb', color: '#ffffff' }}>💬 Open messages</a>
            {canShareFiles && (
              <button type="button" onClick={shareFiles} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>Share with an app…</button>
            )}
          </div>
          <div style={small}>Your messages app opens with the flight details and the link filled in — attach the downloaded {picturesWord} to send it as a picture message.</div>
        </div>
      )}
      {panel === 'whatsapp' && !tooMany && (
        <div style={{ flex: '1 1 100%', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.7rem', display: 'grid', gap: '0.5rem' }}>
          <div style={{ fontWeight: 800, fontSize: '0.85rem', color: '#1e293b' }}>Send on WhatsApp</div>
          {thumbs}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {(images || []).map((img, i) => (
              <a key={img.url} href={img.url} download={img.name} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>
                ⬇ Download {images.length > 1 ? `picture ${i + 1}` : 'image'}
              </a>
            ))}
            <a href={whatsappUrl} target="_blank" rel="noopener noreferrer" style={{ ...btn, background: '#25d366', color: '#ffffff' }}>
              <img src={whatsappIcon} alt="" style={{ width: '16px', height: '16px' }} /> Open WhatsApp
            </a>
            {canShareFiles && (
              <button type="button" onClick={shareFiles} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>Share with an app…</button>
            )}
          </div>
          <div style={small}>
            {images?.length
              ? <>Download the {picturesWord}, then open WhatsApp — the message with the link is filled in. Attach the downloaded {picturesWord} in the chat and send.</>
              : <>Open WhatsApp — the message with the link is filled in.</>}
          </div>
        </div>
      )}
      {panel === 'email' && !tooMany && (
        <form onSubmit={sendEmail} style={{ flex: '1 1 100%', borderBottom: '1px solid #e2e8f0', paddingBottom: '0.7rem', display: 'grid', gap: '0.5rem' }}>
          <div style={{ fontWeight: 800, fontSize: '0.85rem', color: '#1e293b' }}>Send by email</div>
          {thumbs}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <input type="email" required value={emailTo} onChange={e => setEmailTo(e.target.value)} placeholder="Friend's email address"
                   aria-label="Recipient email address" maxLength={254}
                   style={{ flex: '1 1 220px', minWidth: 0, padding: '0.5rem 0.7rem', borderRadius: '6px', border: '1px solid #cbd5e1', fontSize: '0.85rem' }} />
            <button type="submit" disabled={sending || !images?.length} style={{ ...btn, background: C.navy, color: '#ffffff', opacity: sending || !images?.length ? 0.6 : 1 }}>
              {sending ? 'Sending…' : 'Send'}
            </button>
          </div>
          <div style={small}>
            Tick and Book emails the {picturesWord}, the flight details and the link to the live price, with your name as the sender.{' '}
            <a href={`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(plainText)}`} style={{ color: C.navy, fontWeight: 700 }}>
              Or use your own email app
            </a> (text and link only).
          </div>
        </form>
      )}
      {status && (
        <div role="status" style={{ flex: '1 1 100%', fontSize: '0.78rem', fontWeight: 600, color: status.ok ? '#166534' : '#b91c1c' }}>
          {status.text}
          {status.signIn && onSignIn && (
            <button type="button" onClick={() => onSignIn('login')} style={{ marginLeft: '0.5rem', background: 'none', border: 'none', color: C.navy, fontWeight: 800, cursor: 'pointer', textDecoration: 'underline', fontSize: '0.78rem' }}>
              Sign in
            </button>
          )}
        </div>
      )}
      <div style={{ flex: '1 1 180px', minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: '0.85rem', color: '#1e293b' }}>
          {items.length} fare{items.length > 1 ? 's' : ''} selected to share
        </div>
        <div style={{ fontSize: '0.7rem', color: tooMany ? '#b91c1c' : '#64748b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {tooMany ? `Up to ${MAX_SHARE_FARES} fares can be shared at a time — untick ${items.length - MAX_SHARE_FARES}.` : items.map(i => i.label).join('  ·  ')}
        </div>
      </div>
      <button type="button" onClick={onWhatsApp} disabled={tooMany}
              style={{ ...btn, background: '#25d366', color: '#ffffff', opacity: tooMany ? 0.5 : 1 }}>
        <img src={whatsappIcon} alt="" style={{ width: '16px', height: '16px' }} /> WhatsApp
      </button>
      <button type="button" onClick={() => { setStatus(null); setPanel(p => (p === 'email' ? null : 'email')); }} disabled={tooMany}
              style={{ ...btn, background: '#1e293b', color: '#ffffff', opacity: tooMany ? 0.5 : 1 }}>
        ✉ Email
      </button>
      <button type="button" onClick={onText} disabled={tooMany}
              style={{ ...btn, background: '#2563eb', color: '#ffffff', opacity: tooMany ? 0.5 : 1 }}>
        💬 Text
      </button>
      <button type="button" onClick={copy} style={{ ...btn, background: '#f1f5f9', color: '#1e293b' }}>
        {copied ? '✓ Copied' : 'Copy text'}
      </button>
      <button type="button" onClick={() => { setPanel(null); setStatus(null); onClear(); }} style={{ ...btn, background: 'none', color: '#64748b', padding: '0.5rem 0.4rem' }}>
        Clear
      </button>
    </div>
  );
}
