// One date format for the whole system: DD/MM/YYYY (time as 24-hour HH:mm).
// Accepts what the backend and Travelport send — "2026-11-05",
// "2026-11-05T19:50:00", "2026-11-05 19:50:00", ISO with a zone — or a Date.
// Calendar dates ("2026-11-05", flight local times) are read as written, never
// shifted by the browser's time zone; only zoned timestamps are converted.

const pad = (n) => String(n).padStart(2, '0');
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// -> { y, m, d, hh, mm } or null
function parts(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) {
    if (isNaN(value)) return null;
    return { y: value.getFullYear(), m: value.getMonth() + 1, d: value.getDate(), hh: value.getHours(), mm: value.getMinutes() };
  }
  const s = String(value).trim();
  // a zone (Z / +05:30) means a real instant: show it in local time
  if (/T.*(Z|[+-]\d\d:?\d\d)$/.test(s)) return parts(new Date(s));
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (m) return { y: +m[1], m: +m[2], d: +m[3], hh: m[4] !== undefined ? +m[4] : null, mm: m[5] !== undefined ? +m[5] : null };
  const dt = new Date(s);
  return isNaN(dt) ? null : parts(dt);
}

/** 05/11/2026 */
export function fmtDate(value, fallback = '') {
  const p = parts(value);
  return p ? `${pad(p.d)}/${pad(p.m)}/${p.y}` : (value ? String(value) : fallback);
}

/** 05/11/2026 19:50 (date only if there's no time) */
export function fmtDateTime(value, fallback = '') {
  const p = parts(value);
  if (!p) return value ? String(value) : fallback;
  return p.hh === null ? fmtDate(value) : `${pad(p.d)}/${pad(p.m)}/${p.y} ${pad(p.hh)}:${pad(p.mm)}`;
}

/** Thu, 05/11/2026 */
export function fmtDateWeekday(value, fallback = '') {
  const p = parts(value);
  if (!p) return value ? String(value) : fallback;
  const wd = WEEKDAYS[new Date(p.y, p.m - 1, p.d).getDay()];
  return `${wd}, ${pad(p.d)}/${pad(p.m)}/${p.y}`;
}

/** 19:50 */
export function fmtTime(value, fallback = '') {
  const p = parts(value);
  return p && p.hh !== null ? `${pad(p.hh)}:${pad(p.mm)}` : fallback;
}
