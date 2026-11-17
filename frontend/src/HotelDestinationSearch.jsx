import { useState, useEffect, useRef } from 'react';

const API_BASE = '/api';

// Hotel "Where are you going?" box: one search field for a city, region or
// country (replaces the old Country -> City dropdowns, which showed airport
// codes). Suggestions come from /api/reference/hotel-destinations — one row
// per city; picking a country lists its cities. `value` / onChange carry the
// city's IATA code that the hotel search sends, which is never shown here.

const PinIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z" />
  </svg>
);
const GlobeIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
  </svg>
);

const sublabel = (d) => [d.region && d.region !== d.name ? d.region : '', d.country_name].filter(Boolean).join(', ');

export default function HotelDestinationSearch({ value, onChange }) {
  const [text, setText] = useState('');
  const [picked, setPicked] = useState(null);          // the chosen city row
  const [items, setItems] = useState([]);
  const [countryView, setCountryView] = useState(null); // country row whose cities are listed
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null);
  const inputRef = useRef(null);
  const reqId = useRef(0);

  // Parent cleared the destination (e.g. after a booking): clear the box too.
  useEffect(() => {
    if (!value && picked) { setPicked(null); setText(''); }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // composedPath, not contains(): choosing a country redraws the list, so the
    // clicked row is gone from the page by the time this runs (17/11/2026).
    const close = (e) => { if (boxRef.current && !e.composedPath().includes(boxRef.current)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const load = (params) => {
    const id = ++reqId.current;
    setLoading(true);
    fetch(`${API_BASE}/reference/hotel-destinations?${new URLSearchParams(params)}`)
      .then(r => r.json())
      .then(d => { if (id === reqId.current) { setItems(d.destinations || []); setActive(-1); } })
      .catch(() => { if (id === reqId.current) setItems([]); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  };

  // Debounced search while typing.
  useEffect(() => {
    if (countryView || (picked && text === picked.name)) return;
    const q = text.trim();
    if (q.length < 2) { setItems([]); return; }
    const t = setTimeout(() => load({ q }), 250);
    return () => clearTimeout(t);
  }, [text, countryView]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (d) => {
    if (d.type === 'country') {
      setCountryView(d);
      setText(d.name);
      setItems([]);
      load({ country: d.country });
      setOpen(true);
      inputRef.current?.focus();
      return;
    }
    setPicked(d);
    setCountryView(null);
    setText(d.name);
    setOpen(false);
    onChange(d.code);
  };

  const onType = (e) => {
    setText(e.target.value);
    setCountryView(null);
    setOpen(true);
    if (picked) { setPicked(null); onChange(''); }
  };

  const onKeyDown = (e) => {
    if (!open || !items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, items.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); choose(items[active]); }
    else if (e.key === 'Escape') setOpen(false);
  };

  // Shown under the box only once there is something to say (no repeat of the placeholder).
  const hint = picked ? sublabel(picked) : countryView ? `Choose a city in ${countryView.name}` : '';
  const showList = open && (loading || items.length > 0 || text.trim().length >= 2);

  return (
    <div className="hotel-dest" ref={boxRef}>
      <div className={`hotel-dest-box${open ? ' is-open' : ''}`} onClick={() => inputRef.current?.focus()}>
        <span className="hotel-dest-label">Destination</span>
        <div className="hotel-dest-row">
          <span className="hotel-dest-icon"><PinIcon /></span>
          <div className="hotel-dest-text">
            <input
              ref={inputRef}
              className="hotel-dest-input"
              type="text"
              value={text}
              placeholder="City, region or country"
              autoComplete="off"
              aria-label="Destination: city, region or country"
              role="combobox"
              aria-expanded={showList}
              onChange={onType}
              onFocus={() => setOpen(true)}
              onKeyDown={onKeyDown}
            />
            {hint && <span className="hotel-dest-hint">{hint}</span>}
          </div>
        </div>
      </div>

      {showList && (
        <ul className="hotel-dest-list" role="listbox">
          {countryView && <li className="hotel-dest-group">Cities in {countryView.name}</li>}
          {loading && !items.length && <li className="hotel-dest-empty">Searching…</li>}
          {!loading && !items.length && <li className="hotel-dest-empty">No matching city, region or country</li>}
          {items.map((d, i) => (
            <li
              key={`${d.type}-${d.country}-${d.name}-${d.code}`}
              role="option"
              aria-selected={i === active}
              className={`hotel-dest-item${i === active ? ' is-active' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); choose(d); }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="hotel-dest-item-icon">{d.type === 'country' ? <GlobeIcon /> : <PinIcon />}</span>
              <span>
                <span className="hotel-dest-item-name">{d.name}</span>
                <span className="hotel-dest-item-sub">
                  {d.type === 'country' ? 'Country · see all cities' : sublabel(d)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
