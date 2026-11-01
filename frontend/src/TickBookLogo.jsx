// "Tick and Book" logo: a plane taking off along a sky-blue arc over a coral
// and champagne swoosh, the name and the "Flights | Hotels | Travel" line. Drawn as SVG +
// text so it stays sharp at any size. Colours come from the Tick and Book
// palette (index.css :root --tb-*); SVG attributes need the literal hex.
// `light` = white text for the dark sky header; otherwise navy text for
// white backgrounds (footer, e-ticket).
import React from 'react';

export function TickBookMark({ size = 48 }) {
  return (
    <svg width={size * 1.35} height={size} viewBox="0 0 81 60" aria-hidden="true">
      <defs>
        <linearGradient id="tb-swoosh" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#E98270" />
          <stop offset="1" stopColor="#F5E9D8" />
        </linearGradient>
        <linearGradient id="tb-arc" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#72BCEB" />
          <stop offset="1" stopColor="#EAF4FC" />
        </linearGradient>
      </defs>
      {/* coral-to-champagne swoosh underneath */}
      <path d="M4 50c10 9 30 9 48-4-15 7-33 9-48 4z" fill="url(#tb-swoosh)" />
      {/* sky-blue arc the plane climbs along */}
      <path d="M6 44C18 22 40 12 66 12" fill="none" stroke="url(#tb-arc)" strokeWidth="3.2" strokeLinecap="round" />
      {/* plane */}
      <path d="M78 6.5c-1.6-1.6-4.6-.9-6.9 1.1l-7.3 6.4-19.8-5.3-3.3 2.6 15.6 9.1-7.8 6.9-6.4-1.4-2.4 2 6.7 3.7 3.7 6.7 2-2.4-1.4-6.4 6.9-7.8 9.1 15.6 2.6-3.3-5.3-19.8 6.4-7.3c2-2.3 2.7-5.3 1.1-6.9z" fill="#ffffff" stroke="#123B70" strokeWidth="0.7" strokeLinejoin="round" />
    </svg>
  );
}

export default function TickBookLogo({ light = false, size = 44 }) {
  const ink = light ? '#FFFFFF' : '#123B70';
  const accent = '#E98270';
  return (
    <span className="tb-logo" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.55rem', lineHeight: 1 }}>
      <TickBookMark size={size} />
      <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: '0.22rem' }}>
        <span style={{ color: ink, fontFamily: 'var(--font-body)', fontWeight: 800, fontSize: `${Math.round(size * 0.6)}px`, letterSpacing: '-0.01em', whiteSpace: 'nowrap', textShadow: light ? '0 1px 6px rgba(12, 42, 82, 0.55)' : 'none' }}>
          Tick <span style={{ fontWeight: 600, fontSize: '0.78em', color: accent }}>and</span> Book
        </span>
        <span style={{ color: light ? 'rgba(255,255,255,0.92)' : '#24364B', fontFamily: 'var(--font-body)', fontWeight: 500, fontSize: `${Math.max(10, Math.round(size * 0.27))}px`, letterSpacing: '0.02em', whiteSpace: 'nowrap', textShadow: light ? '0 1px 4px rgba(12, 42, 82, 0.6)' : 'none' }}>
          Flights&nbsp;&nbsp;|&nbsp;&nbsp;Hotels&nbsp;&nbsp;|&nbsp;&nbsp;Travel
        </span>
      </span>
    </span>
  );
}
