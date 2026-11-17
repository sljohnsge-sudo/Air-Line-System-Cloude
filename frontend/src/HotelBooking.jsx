import { useState, useEffect, useMemo, useRef } from 'react';
import HotelDestinationSearch from './HotelDestinationSearch.jsx';
import { fmtDate } from './dateFormat.js';
import './HotelBooking.css';

// Hotel booking — search -> results -> hotel -> guest details -> review ->
// confirmation. Every figure on these screens comes from the live Travelport
// Stays APIs through our backend (/api/hotels/*): Search by Location (+ pages
// 2..5), Property Details, Availability, Rules and Create Reservation. Nothing
// here is sample data; a value Travelport does not return is simply not shown.

const API_BASE = '/api';
// Hotels are paid on their own, by card, in LKR: Travelport is asked for LKR
// (requestedCurrency) and sends its own rate from the hotel's currency, which
// is exactly what the customer is charged (backend services/hotel_checkout).
const PAY_CURRENCY = 'LKR';
const CHECKOUT_KEY = 'hb_checkout';

const isoDate = (d) => d.toISOString().split('T')[0];
const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00`); d.setDate(d.getDate() + n); return isoDate(d); };
const nightsBetween = (a, b) => Math.max(1, Math.round((new Date(`${b}T00:00:00`) - new Date(`${a}T00:00:00`)) / 86400000));
const money = (cur, v) => (v === null || v === undefined || v === '' ? '' :
  `${cur || ''} ${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim());
const cleanRoomName = (s) => {
  const t = (s || 'Room').replace(/\s+/g, ' ').replace(/^(Flexible Rate|Prior To Stay),\s*/i, '').trim().replace(/,$/, '');
  const dot = t.indexOf('. ');
  return dot > 0 && dot < 80 ? t.slice(0, dot) : t;  // "Classic Twin Room. This double room…" -> title
};
const roomDetail = (s) => {
  const t = (s || '').replace(/\s+/g, ' ').trim();
  const dot = t.indexOf('. ');
  return dot > 0 && dot < 80 ? t.slice(dot + 2) : '';
};
const ROOMS_SHOWN = 10;
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const jsonOrThrow = (r) => (r.ok ? r.json() : r.json().catch(() => ({})).then(d => { throw new Error(d.detail || r.statusText); }));

// Amenity filters, matched against the amenity text Travelport returns.
const AMENITY_FILTERS = [
  { key: 'pool', label: 'Swimming pool', re: /pool/i },
  { key: 'wifi', label: 'Free Wi-Fi / internet', re: /wireless|wi-?fi|internet/i },
  { key: 'parking', label: 'Parking', re: /parking/i },
  { key: 'gym', label: 'Fitness centre', re: /gym|fitness|health club/i },
  { key: 'restaurant', label: 'Restaurant', re: /restaurant/i },
  { key: 'spa', label: 'Spa', re: /spa|sauna|massage/i },
  { key: 'roomservice', label: 'Room service', re: /room service/i },
];
const DISTANCES = [1, 3, 5, 10];
const SORTS = [
  ['recommended', 'Recommended'],
  ['price_asc', 'Price (lowest first)'],
  ['price_desc', 'Price (highest first)'],
  ['stars', 'Star rating'],
  ['distance', 'Distance from centre'],
];

// Content source of a hotel / rate: Travelport's Identifier.authority.
const SOURCES = { TVPT: 'Travelport', BKNG: 'Booking.com', ETGR: 'Expedia TAAP' };
const SourceBadge = ({ code }) => (code ? (
  <span className={`hb-source hb-source-${code.toLowerCase()}`} title={`Content source: ${SOURCES[code] || code} (${code})`}>
    {code}<em>{SOURCES[code] || ''}</em>
  </span>
) : null);

// Room photos: the hotel's own guest-room / suite / room-amenity pictures
// from Property Details (OTA picture categories 6, 7, 21). Availability has
// no per-room images, so a room gets a photo only when every room and bed
// type the photo's caption names ("Superior", "Suite", "King", "Twin"…) is
// in the room's name too; otherwise it gets none.
const ROOM_PHOTO_CATEGORIES = [6, 7, 21];
const ROOM_TYPE_WORDS = ['suite', 'studio', 'superior', 'deluxe', 'classic', 'executive', 'premium', 'family', 'apartment', 'villa', 'club', 'junior'];
const BED_WORDS = ['king', 'queen', 'twin', 'double', 'single', 'bedroom'];
const words = (s) => new Set((s || '').toLowerCase().match(/[a-z]+/g) || []);
const photosForRoom = (roomName, roomPhotos) => {
  const rw = words(roomName);
  return roomPhotos.filter(p => {
    const cw = words(p.caption);
    const types = ROOM_TYPE_WORDS.filter(w => cw.has(w));
    const beds = BED_WORDS.filter(w => cw.has(w));
    // Every room type and bed type the caption names must be the room's too
    // ("Classic Room, King" is not a photo of a twin room).
    if (![...types, ...beds].every(w => rw.has(w))) return false;
    return types.length + beds.length > 0;
  });
};

const Stars = ({ n }) => (n > 0 ? <span className="hb-stars" aria-label={`${n} star hotel`}>{'★'.repeat(Math.round(n))}</span> : null);

function Steps({ step }) {
  const items = [['hotel', 'Choose room'], ['guest', 'Your details'], ['review', 'Review & pay']];
  const at = items.findIndex(([k]) => k === step);
  return (
    <ol className="hb-steps">
      {items.map(([k, label], i) => (
        <li key={k} className={i < at ? 'is-done' : i === at ? 'is-current' : ''}>
          <span className="hb-step-dot">{i < at ? '✓' : i + 1}</span>{label}
        </li>
      ))}
    </ol>
  );
}

function GuestsPicker({ adults, setAdults, rooms, setRooms, childAges, setChildAges }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);
  const row = (label, sub, value, set, min, max) => (
    <div className="hb-counter">
      <div><strong>{label}</strong>{sub && <span>{sub}</span>}</div>
      <div className="hb-counter-ctl">
        <button type="button" onClick={() => set(Math.max(min, value - 1))} disabled={value <= min} aria-label={`Fewer ${label}`}>−</button>
        <span aria-live="polite">{value}</span>
        <button type="button" onClick={() => set(Math.min(max, value + 1))} disabled={value >= max} aria-label={`More ${label}`}>+</button>
      </div>
    </div>
  );
  const setChildren = (n) => setChildAges(a => (n > a.length ? [...a, ...Array(n - a.length).fill(8)] : a.slice(0, n)));
  const total = adults + childAges.length;
  return (
    <div className="hb-field hb-guests" ref={ref}>
      <span className="hb-field-label">Rooms & guests</span>
      <button type="button" className="hb-field-value" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {plural(rooms, 'room')}, {plural(total, 'guest')}
      </button>
      {open && (
        <div className="hb-pop">
          {row('Rooms', '', rooms, setRooms, 1, 9)}
          {row('Adults', 'Age 18+', adults, setAdults, 1, 9)}
          {row('Children', 'Age 0–17', childAges.length, setChildren, 0, 6)}
          {childAges.length > 0 && (
            <div className="hb-child-ages">
              {childAges.map((age, i) => (
                <label key={i}>Child {i + 1} age
                  <select value={age} onChange={e => setChildAges(a => a.map((x, j) => (j === i ? Number(e.target.value) : x)))}>
                    {Array.from({ length: 18 }, (_, k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                </label>
              ))}
            </div>
          )}
          <button type="button" className="btn btn-primary hb-pop-done" onClick={() => setOpen(false)}>Done</button>
        </div>
      )}
    </div>
  );
}

export default function HotelBooking({ customerProfile, customerToken }) {
  // ── Search form ──
  const today = isoDate(new Date());
  const [destination, setDestination] = useState('');
  const [checkIn, setCheckIn] = useState(addDays(today, 1));
  const [checkOut, setCheckOut] = useState(addDays(today, 2));
  const [adults, setAdults] = useState(2);
  const [rooms, setRooms] = useState(1);
  const [childAges, setChildAges] = useState([]);

  // ── Results (the search that produced them is frozen in `query`) ──
  const [query, setQuery] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [hotels, setHotels] = useState([]);
  const [pagesLeft, setPagesLeft] = useState(0);
  const [pagesTotal, setPagesTotal] = useState(0);
  const searchSeq = useRef(0);

  // ── Filters / sort ──
  const [nameFilter, setNameFilter] = useState('');
  const [starFilter, setStarFilter] = useState([]);
  const [maxDistance, setMaxDistance] = useState(0);
  const [amenityFilter, setAmenityFilter] = useState([]);
  const [priceCap, setPriceCap] = useState(0);
  const [sort, setSort] = useState('recommended');
  const [filtersOpen, setFiltersOpen] = useState(false);

  // ── Hotel / room / checkout ──
  const [step, setStep] = useState('results'); // results | hotel | guest | review | confirmed
  const [hotel, setHotel] = useState(null);
  const [details, setDetails] = useState(null);
  const [offers, setOffers] = useState([]);
  const [offersLoading, setOffersLoading] = useState(false);
  const [offersError, setOffersError] = useState('');
  const [mealsOnly, setMealsOnly] = useState(false);
  const [allRooms, setAllRooms] = useState(false);
  const [roomSource, setRoomSource] = useState('all');
  const [sourceFilter, setSourceFilter] = useState([]);
  const [rules, setRules] = useState({});           // offer_id -> {loading, error, summary}
  const [room, setRoom] = useState(null);
  const [priceNotice, setPriceNotice] = useState('');
  const [gallery, setGallery] = useState(-1);
  const [guest, setGuest] = useState({ first_name: '', last_name: '', email: '', phone: '' });
  const [agree, setAgree] = useState(false);
  const [booking, setBooking] = useState(false);
  const [bookError, setBookError] = useState('');
  const [confirmation, setConfirmation] = useState(null);
  const topRef = useRef(null);

  const scrollTop = () => topRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // ── Search (page 1 at once, pages 2..5 added as they arrive) ──
  const runSearch = (e) => {
    if (e) e.preventDefault();
    if (!destination || !checkIn || !checkOut || checkOut <= checkIn) return;
    const id = ++searchSeq.current;
    const q = { destination, checkIn, checkOut, adults, rooms, childAges: [...childAges], nights: nightsBetween(checkIn, checkOut) };
    setQuery(q); setStep('results'); setHotel(null); setRoom(null); setConfirmation(null);
    setSearching(true); setSearchError(''); setHotels([]); setPagesLeft(0); setPagesTotal(0);
    setNameFilter(''); setStarFilter([]); setMaxDistance(0); setAmenityFilter([]); setPriceCap(0); setSourceFilter([]); setSort('recommended');
    fetch(`${API_BASE}/hotels/search`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        location_type: 'cityIATACode', location_value: destination, check_in_date: checkIn, check_out_date: checkOut,
        adults, rooms, children_ages: childAges.length ? childAges : null, currency: PAY_CURRENCY,
      }),
    })
      .then(jsonOrThrow)
      .then(d => {
        if (id !== searchSeq.current) return;
        setHotels(d.properties || []);
        setSearching(false);
        const pages = Math.min(d.pages || 1, 5);
        setPagesTotal(pages);
        if (!d.identifier || pages < 2) return;
        setPagesLeft(pages - 1);
        for (let page = 2; page <= pages; page++) {
          const qs = new URLSearchParams({ identifier: d.identifier, page, check_in_date: checkIn, check_out_date: checkOut });
          fetch(`${API_BASE}/hotels/search/page?${qs}`)
            .then(r => (r.ok ? r.json() : { properties: [] }))
            .catch(() => ({ properties: [] }))
            .then(more => {
              if (id !== searchSeq.current) return;
              setHotels(prev => {
                const have = new Set(prev.map(h => `${h.chain_code}-${h.property_code}`));
                return prev.concat((more.properties || []).filter(h => !have.has(`${h.chain_code}-${h.property_code}`)));
              });
              setPagesLeft(n => Math.max(n - 1, 0));
            });
        }
      })
      .catch(err => { if (id === searchSeq.current) { setSearchError(err.message); setSearching(false); } });
  };

  // ── Filtering ──
  // Hotels can price in different currencies; compare_price is each one's
  // rate in the requested currency, using Travelport's own conversion rate.
  const cmp = (h) => (h.compare_price && Number(h.compare_price.value) > 0 ? Number(h.compare_price.value) : null);
  const priced = hotels.filter(h => cmp(h) !== null);
  const priceMax = Math.ceil(Math.max(0, ...priced.map(cmp)));
  const currency = (priced[0] || {}).compare_price?.currency || '';
  const starCounts = useMemo(() => hotels.reduce((m, h) => { if (h.stars) m[h.stars] = (m[h.stars] || 0) + 1; return m; }, {}), [hotels]);
  const amenityCounts = useMemo(() => Object.fromEntries(AMENITY_FILTERS.map(f =>
    [f.key, hotels.filter(h => (h.amenities || []).some(a => f.re.test(a))).length])), [hotels]);

  const shown = useMemo(() => {
    let list = hotels.filter(h => {
      if (nameFilter && !h.property_name.toLowerCase().includes(nameFilter.toLowerCase())) return false;
      if (starFilter.length && !starFilter.includes(Math.round(h.stars || 0))) return false;
      if (maxDistance && !(h.distance !== null && h.distance <= maxDistance)) return false;
      if (priceCap && cmp(h) !== null && cmp(h) > priceCap) return false;
      if (sourceFilter.length && !sourceFilter.includes(h.source)) return false;
      for (const k of amenityFilter) {
        const f = AMENITY_FILTERS.find(x => x.key === k);
        if (!(h.amenities || []).some(a => f.re.test(a))) return false;
      }
      return true;
    });
    if (sort === 'price_asc') list = [...list].sort((a, b) => (cmp(a) ?? Infinity) - (cmp(b) ?? Infinity));
    if (sort === 'price_desc') list = [...list].sort((a, b) => (cmp(b) ?? -1) - (cmp(a) ?? -1));
    if (sort === 'stars') list = [...list].sort((a, b) => (b.stars || 0) - (a.stars || 0));
    if (sort === 'distance') list = [...list].sort((a, b) => (a.distance ?? 1e9) - (b.distance ?? 1e9));
    return list;
  }, [hotels, nameFilter, starFilter, maxDistance, priceCap, amenityFilter, sourceFilter, sort]);
  const sourceCounts = useMemo(() => hotels.reduce((m, h) => { if (h.source) m[h.source] = (m[h.source] || 0) + 1; return m; }, {}), [hotels]);

  const activeFilters = (nameFilter ? 1 : 0) + starFilter.length + (maxDistance ? 1 : 0) + (priceCap ? 1 : 0) + amenityFilter.length + sourceFilter.length;
  const clearFilters = () => { setNameFilter(''); setStarFilter([]); setMaxDistance(0); setPriceCap(0); setAmenityFilter([]); setSourceFilter([]); };
  const toggle = (setter) => (v) => setter(list => (list.includes(v) ? list.filter(x => x !== v) : [...list, v]));

  // ── Hotel page ──
  const openHotel = (h) => {
    setHotel(h); setStep('hotel'); setDetails(null); setOffers([]); setOffersError(''); setRules({});
    setRoom(null); setMealsOnly(false); setAllRooms(false); setRoomSource('all'); setGallery(-1); scrollTop();
    setOffersLoading(true);
    fetch(`${API_BASE}/hotels/availability`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chain_code: h.chain_code, property_code: h.property_code, check_in_date: query.checkIn, check_out_date: query.checkOut,
        adults: query.adults, rooms: query.rooms, children_ages: query.childAges.length ? query.childAges : null, currency: PAY_CURRENCY,
      }),
    })
      .then(jsonOrThrow)
      .then(d => setOffers((d.offers || []).sort((a, b) => Number(a.total_price) - Number(b.total_price))))
      .catch(err => setOffersError(err.message))
      .finally(() => setOffersLoading(false));
    fetch(`${API_BASE}/hotels/properties/${encodeURIComponent(h.chain_code)}/${encodeURIComponent(h.property_code)}?image_size=Large`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => setDetails(d))
      .catch(() => setDetails(null));
  };

  const loadRules = (offer) => {
    if (!offer.offer_id || rules[offer.offer_id]?.summary || rules[offer.offer_id]?.loading) return Promise.resolve(rules[offer.offer_id]?.summary);
    setRules(r => ({ ...r, [offer.offer_id]: { loading: true } }));
    return fetch(`${API_BASE}/hotels/rules/${encodeURIComponent(offer.offer_id)}`)
      .then(jsonOrThrow)
      .then(d => { setRules(r => ({ ...r, [offer.offer_id]: { summary: d.summary } })); return d.summary; })
      .catch(err => { setRules(r => ({ ...r, [offer.offer_id]: { error: err.message } })); return null; });
  };

  const chooseRoom = (offer) => {
    setPriceNotice('');
    loadRules(offer).then(summary => {
      let chosen = { ...offer };
      // Rules re-prices the offer; if the hotel's price moved, book the new one.
      if (summary && Number(summary.total_price) > 0 && Math.abs(Number(summary.total_price) - Number(offer.total_price)) > 0.009) {
        setPriceNotice(`The hotel updated this room's price from ${money(offer.currency, offer.total_price)} to ${money(summary.currency || offer.currency, summary.total_price)}.`);
        chosen = { ...chosen, base_price: summary.base_price ?? chosen.base_price, total_taxes: summary.total_taxes ?? chosen.total_taxes,
          total_fees: 0, total_price: summary.total_price, currency: summary.currency || chosen.currency };
        if (offer.converted?.rate) {
          chosen.converted = { ...offer.converted, total: Math.round(Number(summary.total_price) * offer.converted.rate * 100) / 100 };
        }
      }
      setRoom(chosen);
      setGuest(g => ({
        first_name: g.first_name || customerProfile?.full_name?.split(' ')[0] || '',
        last_name: g.last_name || customerProfile?.full_name?.split(' ').slice(1).join(' ') || '',
        email: g.email || customerProfile?.email || '',
        phone: g.phone || customerProfile?.phone || '',
      }));
      setAgree(false); setBookError(''); setStep('guest'); scrollTop();
    });
  };

  // Pay first, then the room is reserved: the server re-prices the rate,
  // sets the LKR amount and sends the customer to NDB's card page. What this
  // screen needs after the bank sends them back is kept in sessionStorage.
  const startPayment = () => {
    if (!hotel || !room || !agree) return;
    setBooking(true); setBookError('');
    fetch(`${API_BASE}/hotels/checkout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(customerToken ? { Authorization: `Bearer ${customerToken}` } : {}) },
      body: JSON.stringify({
        offer_id: room.offer_id,
        guest,
        property_name: hotel.property_name,
        city: hotel.address?.city,
        country_code: hotel.address?.country_code,
        return_url: `${window.location.origin}/hotels`,
      }),
    })
      .then(jsonOrThrow)
      .then(d => {
        try {
          sessionStorage.setItem(CHECKOUT_KEY, JSON.stringify({
            order_ref: d.order_ref, query, guest, hotel: { ...hotel, image_urls: (hotel.image_urls || []).slice(0, 1) },
            room, rules: rules[room.offer_id]?.summary || null, photo: photos[0]?.url || null,
          }));
        } catch { /* storage unavailable: the confirmation still comes from the server */ }
        window.location.assign(d.payment_page_url);
      })
      .catch(err => { setBookError(err.message); setBooking(false); });
  };

  // Back from the bank: /hotels?hotel_order=HTL...&reqid=... (or &payment=cancelled).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const orderRef = params.get('hotel_order');
    if (!orderRef) return;
    const reqid = params.get('reqid');
    const cancelled = params.get('payment') === 'cancelled';
    window.history.replaceState({}, '', window.location.pathname);
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(CHECKOUT_KEY) || 'null'); } catch { saved = null; }
    if (saved && saved.order_ref !== orderRef) saved = null;
    if (saved) {
      setQuery(saved.query); setHotel(saved.hotel); setRoom(saved.room); setGuest(saved.guest);
      if (saved.rules) setRules({ [saved.room.offer_id]: { summary: saved.rules } });
    }
    if (cancelled || !reqid) {
      fetch(`${API_BASE}/hotels/checkout/${encodeURIComponent(orderRef)}/cancel`, { method: 'POST' }).catch(() => {});
      if (saved) { setStep('review'); setAgree(false); }
      setBookError('Payment was cancelled - nothing was charged and no room was reserved.');
      return;
    }
    setStep('paying');
    fetch(`${API_BASE}/hotels/checkout/${encodeURIComponent(orderRef)}/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reqid }),
    })
      .then(jsonOrThrow)
      .then(d => {
        try { sessionStorage.removeItem(CHECKOUT_KEY); } catch { /* ignore */ }
        setConfirmation({ ...d.order, photo: saved?.photo || null }); setStep('confirmed');
      })
      .catch(err => { setBookError(err.message); setStep('payfail'); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const roomRules = room ? rules[room.offer_id]?.summary : null;
  const photos = useMemo(() => {
    const seen = new Set();
    return [...(details?.images || []).map(i => ({ url: i.url, caption: i.caption })), ...(hotel?.image_urls || []).map(url => ({ url }))]
      .filter(p => p.url && !seen.has(p.url) && seen.add(p.url));
  }, [details, hotel]);
  const amenityGroups = useMemo(() => {
    const g = {};
    (details?.amenities || []).forEach(a => { const c = a.category || 'Other'; (g[c] = g[c] || []).push(a.description); });
    return Object.entries(g).sort((a, b) => a[0].localeCompare(b[0]));
  }, [details]);
  const shownOffers = offers.filter(o => (!mealsOnly || (o.meals || []).includes('Breakfast'))
    && (roomSource === 'all' || o.authority === roomSource));
  const offerSources = useMemo(() => offers.reduce((m, o) => { if (o.authority) m[o.authority] = (m[o.authority] || 0) + 1; return m; }, {}), [offers]);
  const roomPhotos = useMemo(() => (details?.images || [])
    .filter(i => ROOM_PHOTO_CATEGORIES.includes(i.category) && i.url)
    .filter((p, i, a) => a.findIndex(x => x.url === p.url) === i), [details]);
  const openPhoto = (url) => { const i = photos.findIndex(p => p.url === url); if (i >= 0) setGallery(i); };
  const mapUrl = hotel?.latitude && hotel?.longitude ? `https://www.google.com/maps/search/?api=1&query=${hotel.latitude},${hotel.longitude}` : '';

  // ── Pieces ──
  const searchBar = (
    <form className="hb-search glass-panel" onSubmit={runSearch}>
      <div className="hb-search-dest"><HotelDestinationSearch value={destination} onChange={setDestination} /></div>
      <label className="hb-field">
        <span className="hb-field-label">Check-in</span>
        <input type="date" className="hb-field-value" required min={addDays(today, 1)} value={checkIn}
          onChange={e => { setCheckIn(e.target.value); if (checkOut <= e.target.value) setCheckOut(addDays(e.target.value, 1)); }} />
      </label>
      <label className="hb-field">
        <span className="hb-field-label">Check-out</span>
        <input type="date" className="hb-field-value" required min={addDays(checkIn, 1)} value={checkOut} onChange={e => setCheckOut(e.target.value)} />
      </label>
      <GuestsPicker adults={adults} setAdults={setAdults} rooms={rooms} setRooms={setRooms} childAges={childAges} setChildAges={setChildAges} />
      <button type="submit" className="btn btn-primary hb-search-btn" disabled={!destination || searching}>
        {searching ? 'Searching…' : 'Search hotels'}
      </button>
    </form>
  );

  const stayLine = query && (
    <>{fmtDate(query.checkIn)} – {fmtDate(query.checkOut)} · {plural(query.nights, 'night')} · {plural(query.adults + query.childAges.length, 'guest')} · {plural(query.rooms, 'room')}</>
  );

  const priceBox = room && (
    <div className="hb-price-box">
      <div><span>Room ({plural(query.nights, 'night')})</span><span>{money(room.currency, room.base_price)}</span></div>
      {(Number(room.total_taxes) > 0 || Number(room.total_fees) > 0) && (
        <div><span>Taxes and fees</span><span>{money(room.currency, (Number(room.total_taxes) || 0) + (Number(room.total_fees) || 0))}</span></div>
      )}
      <div className="hb-price-total"><span>Hotel total</span><span>{money(room.currency, room.total_price)}</span></div>
      {room.converted?.currency === PAY_CURRENCY && room.currency !== PAY_CURRENCY && (
        <div className="hb-price-pay"><span>You pay</span><span>{money(PAY_CURRENCY, room.converted.total)}</span></div>
      )}
      {room.converted?.rate && room.currency !== PAY_CURRENCY && (
        <p className="hb-rate-note">1 {room.currency} = {Number(room.converted.rate).toFixed(4)} {PAY_CURRENCY} (Travelport rate)</p>
      )}
    </div>
  );

  const cancelInfo = (summary) => {
    if (!summary) return null;
    if (summary.free_cancellation_until) {
      return <span className="hb-good">Free cancellation until {fmtDate(summary.free_cancellation_until)}{summary.free_cancellation_time ? `, ${summary.free_cancellation_time.slice(0, 5)} hotel time` : ''}</span>;
    }
    if (summary.cancel_penalties?.length && !summary.refundable) return <span className="hb-warn">Non-refundable</span>;
    return null;
  };

  const summaryCard = room && hotel && (
    <aside className="hb-summary glass-panel">
      {photos[0] && <div className="hb-summary-img" style={{ backgroundImage: `url(${photos[0].url})` }} />}
      <div className="hb-summary-body">
        <h3>{hotel.property_name} <Stars n={hotel.stars} /></h3>
        {(hotel.address?.line || hotel.address?.city) && <p className="hb-muted">{[hotel.address.line, hotel.address.city].filter(Boolean).join(', ')}</p>}
        <div className="hb-summary-dates">
          <div><span>Check-in</span><strong>{fmtDate(query.checkIn)}</strong>{roomRules?.check_in_time && <em>from {roomRules.check_in_time}</em>}</div>
          <div><span>Check-out</span><strong>{fmtDate(query.checkOut)}</strong>{roomRules?.check_out_time && <em>until {roomRules.check_out_time}</em>}</div>
        </div>
        <p className="hb-muted">{plural(query.nights, 'night')} · {plural(query.adults, 'adult')}{query.childAges.length ? ` · ${plural(query.childAges.length, 'child')}` : ''} · {plural(query.rooms, 'room')}</p>
        <div className="hb-summary-room">
          <SourceBadge code={room.authority} />
          <strong>{cleanRoomName(room.room_description)}</strong>
          {room.rate_name && <span>{room.rate_name}</span>}
          {room.beds?.length > 0 && <span>{room.beds.join(', ')}</span>}
          <span>{room.meals?.length ? `Includes ${room.meals.join(', ').toLowerCase()}` : 'Room only'}</span>
          {cancelInfo(roomRules)}
          {roomRules?.payment === 'PostPay' && <span>Pay at the property</span>}
        </div>
        {priceNotice && <div className="hb-notice">{priceNotice}</div>}
        {priceBox}
      </div>
    </aside>
  );

  // ── Screens ──
  if (step === 'paying') {
    return (
      <div className="hb" ref={topRef}>
        <section className="hb-confirm glass-panel">
          <div className="spinner" style={{ margin: '0 auto 1rem' }}></div>
          <h2>Confirming your payment and reserving the room…</h2>
          <p className="hb-muted">Please keep this page open. It takes a few seconds.</p>
        </section>
      </div>
    );
  }

  if (step === 'payfail') {
    return (
      <div className="hb" ref={topRef}>
        <section className="hb-confirm glass-panel hb-confirm-fail">
          <div className="hb-confirm-icon" aria-hidden="true">!</div>
          <h2>We couldn't complete this hotel booking</h2>
          <p>{bookError}</p>
          <div className="hb-confirm-actions">
            <button type="button" className="btn btn-primary" onClick={() => { setBookError(''); setRoom(null); setHotel(null); setStep('results'); }}>Back to hotel search</button>
          </div>
        </section>
      </div>
    );
  }

  if (step === 'confirmed' && confirmation) {
    const c = confirmation;
    return (
      <div className="hb" ref={topRef}>
        <section className="hb-confirm glass-panel">
          <div className="hb-confirm-icon" aria-hidden="true">✓</div>
          <h2>Paid and confirmed</h2>
          <p className="hb-muted">Thank you{c.guest_name ? `, ${c.guest_name}` : ''}. Your room at {c.property_name} is reserved.</p>
          <div className="hb-confirm-grid">
            <div><span>Hotel confirmation no.</span><strong>{c.confirmation_number || '—'}</strong></div>
            <div><span>Booking reference (PNR)</span><strong>{c.locator_code || '—'}</strong></div>
            <div><span>Hotel</span><strong>{c.property_name}</strong></div>
            <div><span>Room</span><strong>{cleanRoomName(c.room_description)}</strong></div>
            {c.rate_source && <div><span>Rate source</span><strong>{c.rate_source} · {SOURCES[c.rate_source] || c.rate_source}</strong></div>}
            <div><span>Check-in</span><strong>{fmtDate(c.check_in_date)}{roomRules?.check_in_time ? `, from ${roomRules.check_in_time}` : ''}</strong></div>
            <div><span>Check-out</span><strong>{fmtDate(c.check_out_date)}{roomRules?.check_out_time ? `, until ${roomRules.check_out_time}` : ''}</strong></div>
            <div><span>Paid by card</span><strong>{money(c.charge_currency, c.charge_amount)}</strong></div>
            {c.hotel_currency && c.hotel_currency !== c.charge_currency && <div><span>Hotel total</span><strong>{money(c.hotel_currency, c.hotel_total)}</strong></div>}
            <div><span>Payment reference</span><strong>{c.order_ref}</strong></div>
            {roomRules && <div><span>Cancellation</span><strong>{cancelInfo(roomRules) || 'See hotel policy'}</strong></div>}
          </div>
          <div className="hb-confirm-actions">
            <button type="button" className="btn btn-secondary" onClick={() => window.print()}>Print</button>
            <button type="button" className="btn btn-primary" onClick={() => { setConfirmation(null); setRoom(null); setHotel(null); setStep('results'); }}>Search more hotels</button>
          </div>
        </section>
      </div>
    );
  }

  if ((step === 'guest' || step === 'review') && room && hotel) {
    const payAmount = room.currency === PAY_CURRENCY ? Number(room.total_price)
      : (room.converted?.currency === PAY_CURRENCY ? Number(room.converted.total) : 0);
    const guestOk = guest.first_name.trim() && guest.last_name.trim() && /\S+@\S+\.\S+/.test(guest.email) && guest.phone.replace(/\D/g, '').length >= 7;
    return (
      <div className="hb" ref={topRef}>
        <button type="button" className="hb-back" onClick={() => { setStep(step === 'review' ? 'guest' : 'hotel'); scrollTop(); }}>
          ← {step === 'review' ? 'Back to your details' : 'Back to rooms'}
        </button>
        <Steps step={step} />
        <div className="hb-checkout">
          <div className="hb-checkout-main">
            {step === 'guest' ? (
              <form className="glass-panel hb-panel" onSubmit={(e) => { e.preventDefault(); if (guestOk) { setStep('review'); scrollTop(); } }}>
                <h2>Lead guest details</h2>
                <p className="hb-muted">Enter the name exactly as on the passport or ID the guest will show at check-in.</p>
                <div className="hb-form-row">
                  <label className="form-group"><span className="form-label">First name *</span>
                    <input className="form-input" required maxLength={50} autoComplete="given-name" value={guest.first_name} onChange={e => setGuest(g => ({ ...g, first_name: e.target.value }))} /></label>
                  <label className="form-group"><span className="form-label">Last name *</span>
                    <input className="form-input" required maxLength={50} autoComplete="family-name" value={guest.last_name} onChange={e => setGuest(g => ({ ...g, last_name: e.target.value }))} /></label>
                </div>
                <div className="hb-form-row">
                  <label className="form-group"><span className="form-label">Email *</span>
                    <input type="email" className="form-input" required autoComplete="email" value={guest.email} onChange={e => setGuest(g => ({ ...g, email: e.target.value }))} /></label>
                  <label className="form-group"><span className="form-label">Phone *</span>
                    <input type="tel" className="form-input" required minLength={7} maxLength={20} autoComplete="tel" value={guest.phone} onChange={e => setGuest(g => ({ ...g, phone: e.target.value }))} /></label>
                </div>
                <button type="submit" className="btn btn-primary hb-wide" disabled={!guestOk}>Continue to review</button>
              </form>
            ) : (
              <div className="glass-panel hb-panel">
                <h2>Review your booking</h2>
                <div className="hb-review-grid">
                  <div><span>Guest</span><strong>{guest.first_name} {guest.last_name}</strong><em>{guest.email} · {guest.phone}</em></div>
                  <div><span>Stay</span><strong>{fmtDate(query.checkIn)} – {fmtDate(query.checkOut)}</strong><em>{plural(query.nights, 'night')}, {plural(query.rooms, 'room')}</em></div>
                  <div><span>Room</span><strong>{cleanRoomName(room.room_description)}</strong><em>{room.rate_name || (room.meals?.length ? `Includes ${room.meals.join(', ').toLowerCase()}` : 'Room only')}</em></div>
                  <div><span>You pay</span><strong>{payAmount ? money(PAY_CURRENCY, payAmount) : money(room.currency, room.total_price)}</strong>
                    <em>{payAmount && room.currency !== PAY_CURRENCY ? `Hotel total ${money(room.currency, room.total_price)}, incl. taxes` : 'incl. taxes and fees shown by the hotel'}</em></div>
                </div>
                {roomRules?.cancel_penalties?.length > 0 && (
                  <div className="hb-policy">
                    <h3>Cancellation policy</h3>
                    <ul>{roomRules.cancel_penalties.map((p, i) => <li key={i}>{p.description || (p.refundable ? `Free cancellation until ${fmtDate(p.until)}` : `Cancellation fee ${p.amount ? money(p.amount.currency, p.amount.value) : `${p.percent}%`}${p.from ? ` from ${fmtDate(p.from)}` : ''}`)}</li>)}</ul>
                  </div>
                )}
                {roomRules?.text_blocks?.length > 0 && (
                  <details className="hb-policy">
                    <summary>Hotel terms and information</summary>
                    <dl>{roomRules.text_blocks.map((b, i) => <div key={i}><dt>{b.title}</dt><dd>{b.text}</dd></div>)}</dl>
                  </details>
                )}
                {rules[room.offer_id]?.error && <div className="hb-notice">The hotel's cancellation rules could not be loaded ({rules[room.offer_id].error}). Please check them with us before confirming.</div>}
                <label className="hb-agree">
                  <input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} />
                  <span>I have checked the guest details, the room and the cancellation policy above.</span>
                </label>
                {bookError && <div className="error-banner">{bookError}</div>}
                {!payAmount && <div className="hb-notice">This room can't be paid online in {PAY_CURRENCY}. Please choose another room.</div>}
                <button type="button" className="btn btn-primary hb-wide hb-confirm-btn" disabled={!agree || booking || !payAmount} onClick={startPayment}>
                  {booking ? 'Opening the secure payment page…' : `Pay ${money(PAY_CURRENCY, payAmount)} & book`}
                </button>
                <p className="hb-pay-note">You'll pay on NDB Bank's secure card page. The room is reserved with the hotel only after your payment succeeds. This hotel payment is separate from any flight booking.</p>
              </div>
            )}
          </div>
          {summaryCard}
        </div>
      </div>
    );
  }

  if (step === 'hotel' && hotel) {
    return (
      <div className="hb" ref={topRef}>
        <button type="button" className="hb-back" onClick={() => { setStep('results'); setHotel(null); }}>← Back to results</button>
        <Steps step="hotel" />
        <section className="glass-panel hb-hotel">
          <div className="hb-hotel-head">
            <div>
              <h2>{hotel.property_name} <Stars n={hotel.stars} /></h2>
              <p className="hb-muted">
                {[hotel.address?.line, hotel.address?.city].filter(Boolean).join(', ')}
                {hotel.distance !== null && hotel.distance !== undefined && ` · ${hotel.distance} km from centre`}
                {mapUrl && <> · <a href={mapUrl} target="_blank" rel="noopener noreferrer">Show on map</a></>}
              </p>
            </div>
            {Number(hotel.lowest_price) > 0 && (
              <div className="hb-from"><span>From</span><strong>{money(hotel.lowest_price_currency, hotel.lowest_price)}</strong><span>per night</span></div>
            )}
          </div>
          {photos.length > 0 && (
            <div className={`hb-gallery n${Math.min(photos.length, 5)}`}>
              {photos.slice(0, 5).map((p, i) => (
                <button type="button" key={p.url} className="hb-gallery-tile" style={{ backgroundImage: `url(${p.url})` }} onClick={() => setGallery(i)} aria-label={p.caption || `Photo ${i + 1}`}>
                  {i === 4 && photos.length > 5 && <span>+{photos.length - 5} photos</span>}
                </button>
              ))}
            </div>
          )}
          <nav className="hb-tabs">
            <a href="#hb-rooms">Rooms</a>
            {amenityGroups.length > 0 && <a href="#hb-amenities">Amenities</a>}
            <a href="#hb-policies">Policies</a>
          </nav>
          <p className="hb-stay">{stayLine}</p>
        </section>

        <section id="hb-rooms" className="glass-panel hb-panel">
          <div className="hb-rooms-head">
            <h3>Available rooms{offers.length ? <span className="hb-count">{offers.length}</span> : null}</h3>
            <div className="hb-rooms-tools">
              {Object.keys(offerSources).length > 1 && (
                <div className="hb-seg" role="group" aria-label="Rate source">
                  <button type="button" className={roomSource === 'all' ? 'is-on' : ''} onClick={() => setRoomSource('all')}>All {offers.length}</button>
                  {Object.entries(offerSources).map(([code, n]) => (
                    <button type="button" key={code} className={roomSource === code ? 'is-on' : ''} onClick={() => setRoomSource(code)}>
                      {code} · {SOURCES[code] || code} {n}
                    </button>
                  ))}
                </div>
              )}
              {offers.some(o => (o.meals || []).includes('Breakfast')) && (
                <label className="hb-toggle"><input type="checkbox" checked={mealsOnly} onChange={e => setMealsOnly(e.target.checked)} /> Breakfast included</label>
              )}
            </div>
          </div>
          {roomPhotos.length > 0 && (
            <div className="hb-room-photos" aria-label="Room photos from the hotel">
              {roomPhotos.slice(0, 12).map(p => (
                <button type="button" key={p.url} style={{ backgroundImage: `url(${p.url})` }} onClick={() => openPhoto(p.url)} title={p.caption} aria-label={p.caption || 'Room photo'}>
                  {p.caption && <span>{p.caption}</span>}
                </button>
              ))}
            </div>
          )}
          {offersLoading ? (
            <div className="loading-state"><div className="spinner"></div><p>Getting live room rates from the hotel…</p></div>
          ) : offersError ? (
            <div className="error-banner">{offersError}</div>
          ) : shownOffers.length === 0 ? (
            <p className="hb-muted">{offers.length ? 'No rooms with breakfast for these dates.' : 'No rooms are available at this hotel for your dates.'}</p>
          ) : (
            <div className="hb-room-list">
              {(allRooms ? shownOffers : shownOffers.slice(0, ROOMS_SHOWN)).map(o => {
                const r = rules[o.offer_id];
                const detail = roomDetail(o.room_description);
                const pics = photosForRoom(o.room_description, roomPhotos);
                return (
                  <article key={o.offer_id} className={`hb-room${pics.length ? ' has-photo' : ''}`}>
                    {pics.length > 0 && (
                      <button type="button" className="hb-room-photo" style={{ backgroundImage: `url(${pics[0].url})` }}
                        onClick={() => openPhoto(pics[0].url)} aria-label={`Photo: ${pics[0].caption || 'room'}`}>
                        {pics.length > 1 && <span>{pics.length} photos</span>}
                      </button>
                    )}
                    <div className="hb-room-info">
                      <div className="hb-card-top"><SourceBadge code={o.authority} /></div>
                      <h4>{cleanRoomName(o.room_description)}</h4>
                      {detail && <p className="hb-room-detail">{detail}</p>}
                      <div className="hb-tags">
                        {o.beds?.map(b => <span key={b} className="hb-tag">{b}</span>)}
                        <span className={`hb-tag ${o.meals?.length ? 'is-good' : ''}`}>{o.meals?.length ? `${o.meals.join(' & ')} included` : 'Room only'}</span>
                        {o.smoking === 'No' && <span className="hb-tag">Non-smoking</span>}
                        {o.accessible === 'Yes' && <span className="hb-tag">Accessible room</span>}
                      </div>
                      {o.rate_name && <p className="hb-muted">{o.rate_name}</p>}
                      <div className="hb-room-rules">
                        {r?.summary ? (cancelInfo(r.summary) || <span className="hb-muted">See the hotel's cancellation terms at the next step</span>)
                          : r?.loading ? <span className="hb-muted">Checking cancellation policy…</span>
                          : r?.error ? <span className="hb-warn">Cancellation policy unavailable</span>
                          : <button type="button" className="hb-link" onClick={() => loadRules(o)}>Show cancellation policy</button>}
                      </div>
                    </div>
                    <div className="hb-room-price">
                      <strong>{money(o.currency, o.total_price)}</strong>
                      <span>total for {plural(query.nights, 'night')}{query.rooms > 1 ? `, ${plural(query.rooms, 'room')}` : ''}</span>
                      {o.converted?.currency === PAY_CURRENCY && o.currency !== PAY_CURRENCY && <span className="hb-lkr">≈ {money(PAY_CURRENCY, o.converted.total)}</span>}
                      {Number(o.total_taxes) > 0 && <span>incl. {money(o.currency, o.total_taxes)} taxes</span>}
                      {query.nights > 1 && <span>≈ {money(o.currency, Number(o.total_price) / query.nights)} per night</span>}
                      <button type="button" className="btn btn-primary" disabled={r?.loading} onClick={() => chooseRoom(o)}>
                        {r?.loading ? 'Checking…' : 'Select room'}
                      </button>
                    </div>
                  </article>
                );
              })}
              {!allRooms && shownOffers.length > ROOMS_SHOWN && (
                <button type="button" className="btn btn-secondary" onClick={() => setAllRooms(true)}>
                  Show all {shownOffers.length} rooms
                </button>
              )}
            </div>
          )}
        </section>

        {amenityGroups.length > 0 && (
          <section id="hb-amenities" className="glass-panel hb-panel">
            <h3>Amenities</h3>
            <div className="hb-amenities">
              {amenityGroups.map(([cat, list]) => (
                <div key={cat}><h4>{cat}</h4><ul>{[...new Set(list)].map(a => <li key={a}>{a}</li>)}</ul></div>
              ))}
            </div>
          </section>
        )}

        <section id="hb-policies" className="glass-panel hb-panel">
          <h3>Policies</h3>
          <dl className="hb-facts">
            {(details?.check_in_time) && <div><dt>Check-in</dt><dd>From {details.check_in_time.slice(0, 5)}</dd></div>}
            {(details?.check_out_time) && <div><dt>Check-out</dt><dd>Until {details.check_out_time.slice(0, 5)}</dd></div>}
            {details?.telephones?.length > 0 && <div><dt>Phone</dt><dd>{details.telephones[0]}</dd></div>}
            {details?.email?.value && <div><dt>Email</dt><dd>{details.email.value}</dd></div>}
            {details?.accessibility_features?.length > 0 && (
              <div><dt>Accessibility</dt><dd>{details.accessibility_features.map(a => a.description).filter(Boolean).join(' · ')}</dd></div>
            )}
          </dl>
          <p className="hb-muted">The room's cancellation policy, check-in times and the hotel's own terms are shown when you choose a room.</p>
        </section>

        {gallery >= 0 && photos[gallery] && (
          <div className="hb-lightbox" role="dialog" aria-modal="true" aria-label="Hotel photos" onClick={() => setGallery(-1)}>
            <figure onClick={e => e.stopPropagation()}>
              <img src={photos[gallery].url} alt={photos[gallery].caption || hotel.property_name} />
              <figcaption>{photos[gallery].caption} <span>{gallery + 1} / {photos.length}</span></figcaption>
              <button type="button" className="hb-lb-prev" onClick={() => setGallery(i => (i - 1 + photos.length) % photos.length)} aria-label="Previous photo">‹</button>
              <button type="button" className="hb-lb-next" onClick={() => setGallery(i => (i + 1) % photos.length)} aria-label="Next photo">›</button>
              <button type="button" className="hb-lb-close" onClick={() => setGallery(-1)} aria-label="Close">×</button>
            </figure>
          </div>
        )}
      </div>
    );
  }

  // ── Search + results ──
  const loadedPct = pagesTotal > 1 ? Math.round(((pagesTotal - pagesLeft) / pagesTotal) * 100) : 100;
  return (
    <div className="hb" ref={topRef}>
      <div className="hb-hero">
        <svg className="hb-hero-trail" viewBox="0 0 600 160" aria-hidden="true" preserveAspectRatio="none">
          <path d="M0 140 C 160 120, 260 40, 420 52 S 560 90, 600 30" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="2 9" strokeLinecap="round" />
        </svg>
        <span className="hb-hero-kicker">Tick and Book · Hotels</span>
        <h2>Land, check in, <span>stay well.</span></h2>
        <p>Live rooms and rates — with meals, taxes and the cancellation deadline shown before you book.</p>
      </div>
      {searchBar}

      {searchError && <div className="error-banner">{searchError}</div>}
      {bookError && !searchError && <div className="hb-notice">{bookError}</div>}
      {searching && <div className="loading-state"><div className="spinner"></div><p>Searching live hotel availability…</p></div>}

      {query && !searching && !searchError && (
        <div className="hb-results">
          <aside className={`hb-filters glass-panel${filtersOpen ? ' is-open' : ''}`}>
            <div className="hb-filters-head">
              <h3>Filters</h3>
              {activeFilters > 0 && <button type="button" className="hb-link" onClick={clearFilters}>Clear all</button>}
              <button type="button" className="hb-filters-close" onClick={() => setFiltersOpen(false)} aria-label="Close filters">×</button>
            </div>
            <label className="hb-filter">
              <span>Hotel name</span>
              <input className="form-input" placeholder="Search by name" value={nameFilter} onChange={e => setNameFilter(e.target.value)} />
            </label>
            {priceMax > 0 && (
              <label className="hb-filter">
                <span>Max price per night{priceCap ? `: ${money(currency, priceCap)}` : ''}</span>
                <input type="range" min={0} max={priceMax} step={Math.max(1, Math.round(priceMax / 50))} value={priceCap || priceMax}
                  onChange={e => setPriceCap(Number(e.target.value) >= priceMax ? 0 : Number(e.target.value))} />
              </label>
            )}
            {Object.keys(sourceCounts).length > 0 && (
              <fieldset className="hb-filter">
                <legend>Content source</legend>
                {Object.entries(sourceCounts).map(([code, n]) => (
                  <label key={code} className="hb-check"><input type="checkbox" checked={sourceFilter.includes(code)} onChange={() => toggle(setSourceFilter)(code)} />
                    <SourceBadge code={code} /><em>{n}</em></label>
                ))}
              </fieldset>
            )}
            {Object.keys(starCounts).length > 0 && (
              <fieldset className="hb-filter">
                <legend>Star rating</legend>
                {[5, 4, 3, 2, 1].filter(s => starCounts[s]).map(s => (
                  <label key={s} className="hb-check"><input type="checkbox" checked={starFilter.includes(s)} onChange={() => toggle(setStarFilter)(s)} />
                    <Stars n={s} /><em>{starCounts[s]}</em></label>
                ))}
              </fieldset>
            )}
            <fieldset className="hb-filter">
              <legend>Distance from centre</legend>
              {DISTANCES.map(d => (
                <label key={d} className="hb-check"><input type="radio" name="hb-dist" checked={maxDistance === d} onChange={() => setMaxDistance(d)} />
                  Less than {d} km<em>{hotels.filter(h => h.distance !== null && h.distance <= d).length}</em></label>
              ))}
              {maxDistance > 0 && <button type="button" className="hb-link" onClick={() => setMaxDistance(0)}>Any distance</button>}
            </fieldset>
            {AMENITY_FILTERS.some(f => amenityCounts[f.key]) && (
              <fieldset className="hb-filter">
                <legend>Facilities</legend>
                {AMENITY_FILTERS.filter(f => amenityCounts[f.key]).map(f => (
                  <label key={f.key} className="hb-check"><input type="checkbox" checked={amenityFilter.includes(f.key)} onChange={() => toggle(setAmenityFilter)(f.key)} />
                    {f.label}<em>{amenityCounts[f.key]}</em></label>
                ))}
              </fieldset>
            )}
          </aside>

          <div className="hb-list">
            <div className="hb-list-head">
              <div>
                <h3>{shown.length} of {plural(hotels.length, 'hotel')} available</h3>
                <p className="hb-muted">{stayLine}</p>
              </div>
              <div className="hb-list-tools">
                <button type="button" className="btn btn-secondary hb-filters-btn" onClick={() => setFiltersOpen(true)}>
                  Filters{activeFilters ? ` (${activeFilters})` : ''}
                </button>
                <label className="hb-sort">Sort by
                  <select value={sort} onChange={e => setSort(e.target.value)}>
                    {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>
              </div>
            </div>
            {pagesLeft > 0 && (
              <div className="hb-progress" aria-live="polite">
                <span>Loading more hotels… {loadedPct}%</span>
                <div><i style={{ width: `${loadedPct}%` }} /></div>
              </div>
            )}

            {hotels.length === 0 && pagesLeft === 0 ? (
              <div className="empty-state glass-panel"><p>No hotels have rooms available for these dates. Try other dates or a nearby city.</p></div>
            ) : shown.length === 0 && pagesLeft === 0 ? (
              <div className="empty-state glass-panel"><p>No hotels match your filters. <button type="button" className="hb-link" onClick={clearFilters}>Clear filters</button></p></div>
            ) : (
              shown.map(h => (
                <article key={`${h.chain_code}-${h.property_code}`} className="hb-card glass-panel">
                  <button type="button" className="hb-card-img" onClick={() => openHotel(h)} aria-label={`View ${h.property_name}`}
                    style={h.image_urls?.[0] ? { backgroundImage: `url(${h.image_urls[0]})` } : undefined} />
                  <div className="hb-card-body">
                    <div className="hb-card-top"><SourceBadge code={h.source} /></div>
                    <h4><button type="button" className="hb-card-title" onClick={() => openHotel(h)}>{h.property_name}</button> <Stars n={h.stars} /></h4>
                    <p className="hb-muted">
                      {[h.address?.line, h.address?.city].filter(Boolean).join(', ')}
                      {h.distance !== null && h.distance !== undefined && ` · ${h.distance} km from centre`}
                    </p>
                    {h.amenities?.length > 0 && (
                      <div className="hb-tags">
                        {AMENITY_FILTERS.filter(f => h.amenities.some(a => f.re.test(a))).slice(0, 4).map(f => <span key={f.key} className="hb-tag">{f.label}</span>)}
                      </div>
                    )}
                  </div>
                  <div className="hb-card-price">
                    {Number(h.lowest_price) > 0 ? (
                      <><span>From</span><strong>{money(h.lowest_price_currency, h.lowest_price)}</strong>
                        {h.compare_price && h.compare_price.currency !== h.lowest_price_currency && <span>≈ {money(h.compare_price.currency, h.compare_price.value)}</span>}
                        <span>per night, before taxes</span></>
                    ) : <span className="hb-muted">Price on the next step</span>}
                    <button type="button" className="btn btn-primary" onClick={() => openHotel(h)}>See rooms</button>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
