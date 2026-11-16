// Mobile number field for the Passenger Details step (16/11/2026), in the
// familiar one-box style: [flag +94 ▾ | 0771 234 567]. Clicking the flag
// opens a list with a search box on top — type a country or a code ("sri",
// "india", "94", "+971") — showing flag, country name and code. The countries
// most of our customers use come first, with the passenger's nationality on
// top. Nothing is picked for the customer (13/11/2026 rule: the code is
// their choice). The dial value is digits only, e.g. "94".
//
// Flags are the system's own flag symbols, so no images or outside servers
// are needed. Windows has no flag symbols, so there a small country-code tag
// ("LK") is shown instead.
import React, { useEffect, useMemo, useRef, useState } from 'react';

const POPULAR = ['LK', 'IN', 'MV', 'AE', 'QA', 'SA', 'KW', 'OM', 'GB', 'AU', 'SG', 'MY', 'US', 'CA'];
const NO_FLAG_SYMBOLS = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent || '');

const flagOf = (code) => String.fromCodePoint(...[...code.toUpperCase()].map(ch => 0x1F1E6 + ch.charCodeAt(0) - 65));

function Flag({ code }) {
  if (!code) return <span className="pcf-flag pcf-flag-empty" aria-hidden="true">🌐</span>;
  return NO_FLAG_SYMBOLS
    ? <span className="pcf-flag pcf-flag-tag" aria-hidden="true">{code}</span>
    : <span className="pcf-flag" aria-hidden="true">{flagOf(code)}</span>;
}

export default function PhoneField({ countries, dial, local, onDialChange, onLocalChange, suggestCode, placeholder, invalid }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [pickedCode, setPickedCode] = useState(null); // which "+1" country was picked
  const boxRef = useRef(null);
  const searchRef = useRef(null);
  const listRef = useRef(null);
  const numberRef = useRef(null);

  const byName = useMemo(() => [...countries].sort((a, b) => a.name.localeCompare(b.name)), [countries]);
  const selected = useMemo(() => {
    if (!dial) return null;
    return countries.find(c => c.code === pickedCode && c.dial === dial)
      || countries.find(c => c.code === suggestCode && c.dial === dial)
      || countries.find(c => c.dial === dial) || null;
  }, [countries, dial, pickedCode, suggestCode]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^\+/, '');
    if (q) {
      const digits = /^\d+$/.test(q);
      const hits = byName.filter(c => (digits
        ? c.dial.startsWith(q)
        : c.name.toLowerCase().includes(q) || c.code.toLowerCase() === q));
      const score = (c) => (digits ? (c.dial === q ? 0 : 1)
        : c.name.toLowerCase().startsWith(q) ? 0 : c.name.toLowerCase().split(/[\s(-]+/).some(w => w.startsWith(q)) ? 1 : 2);
      hits.sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
      return hits.map(c => ({ c, group: null }));
    }
    const topCodes = [...new Set([suggestCode, ...POPULAR].filter(Boolean))];
    const top = topCodes.map(code => countries.find(c => c.code === code)).filter(Boolean);
    return [
      ...top.map((c, i) => ({ c, group: i === 0 ? 'Popular' : null })),
      ...byName.map((c, i) => ({ c, group: i === 0 ? 'All countries' : null })),
    ];
  }, [query, byName, countries, suggestCode]);

  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(() => searchRef.current?.focus(), 0);
    const onDown = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', onDown); };
  }, [open]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const toggle = () => { setQuery(''); setActive(0); setOpen(o => !o); };
  const choose = (c) => {
    setPickedCode(c.code);
    onDialChange(c.dial);
    setOpen(false);
    setQuery('');
    setTimeout(() => numberRef.current?.focus(), 0);
  };
  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, rows.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (rows[active]) choose(rows[active].c); }
    else if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
  };

  return (
    <div className="pcf" ref={boxRef}>
      <div className={`pcf-box${invalid ? ' pcf-invalid' : ''}${open ? ' pcf-open' : ''}`}>
        <button type="button" className="pcf-country" aria-haspopup="listbox" aria-expanded={open} onClick={toggle}
                title={selected ? `${selected.name} +${selected.dial}` : 'Choose your country code'}>
          <Flag code={selected?.code} />
          <span className="pcf-dial">{selected ? `+${selected.dial}` : 'Code'}</span>
          <span className="pcf-caret" aria-hidden="true">▾</span>
        </button>
        <input ref={numberRef} type="tel" inputMode="tel" autoComplete="tel-national" className="pcf-number"
               placeholder={placeholder} value={local} onChange={e => onLocalChange(e.target.value)} />
      </div>
      {open && (
        <div className="pcf-pop">
          <input ref={searchRef} className="pcf-search" placeholder="Search country or code"
                 value={query} onChange={e => { setQuery(e.target.value); setActive(0); }} onKeyDown={onKey}
                 aria-label="Search country or code" />
          <ul className="pcf-list" role="listbox" ref={listRef}>
            {rows.length === 0 && <li className="pcf-empty">No country matches “{query}”.</li>}
            {rows.map(({ c, group }, i) => (
              <React.Fragment key={`${group || ''}${c.code}-${i}`}>
                {group && <li className="pcf-group" aria-hidden="true">{group}</li>}
                <li role="option" data-idx={i} aria-selected={selected?.code === c.code}
                    className={`pcf-row${i === active ? ' pcf-active' : ''}${selected?.code === c.code ? ' pcf-selected' : ''}`}
                    onMouseEnter={() => setActive(i)} onMouseDown={e => { e.preventDefault(); choose(c); }}>
                  <Flag code={c.code} />
                  <span className="pcf-name">{c.name}</span>
                  <span className="pcf-row-dial">+{c.dial}</span>
                </li>
              </React.Fragment>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
