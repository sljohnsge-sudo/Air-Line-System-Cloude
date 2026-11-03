// Waiting animation for the booking steps that wait on the airline (seat map
// check, booking confirmation): a plane flying round a ring that keeps
// filling, the step's icon in the middle, and what is being done. Purely
// decorative — it shows no progress figure, since Travelport gives none.
import React from 'react';

const ICONS = {
  seat: (
    <g fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M-9 -12v12a3 3 0 0 0 3 3h12" />
      <path d="M-4 3l-2 9M6 3l2 9M-9 -1h9a3 3 0 0 1 3 3v1" />
    </g>
  ),
  calendar: (
    <g fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="-12" y="-10" width="24" height="22" rx="3" />
      <path d="M-12 -3h24M-6 -14v6M6 -14v6M-6 3h2M1 3h2M-6 8h2M1 8h2" />
    </g>
  ),
  ticket: (
    <g fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M-13 -7h26v4a3 3 0 0 0 0 6v4h-26v-4a3 3 0 0 0 0-6z" />
      <path d="M-5 0l3 3 6-6" />
    </g>
  ),
};

export default function BookingWaitLoader({ icon = 'ticket', title, subtitle, steps = [] }) {
  return (
    <div className="bw-loader" role="status" aria-live="polite">
      <svg className="bw-ring" viewBox="-60 -60 120 120" width="132" height="132" aria-hidden="true">
        <defs>
          <linearGradient id="bwArc" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#72BCEB" />
            <stop offset="100%" stopColor="#123B70" />
          </linearGradient>
        </defs>
        <circle r="54" className="bw-halo" />
        <circle r="44" className="bw-track" />
        <circle r="44" className="bw-arc" stroke="url(#bwArc)" />
        <g className="bw-center">{ICONS[icon]}</g>
        <g className="bw-orbit">
          {/* plane on the ring, nose pointing along the (clockwise) flight path */}
          <g transform="translate(0 -44)">
            <path className="bw-plane" d="M9 0 L-3 -2.2 L-7 -9 L-9.5 -9 L-7 -2.2 L-11 -2 L-13 -5 L-14.5 -5 L-13.5 0 L-14.5 5 L-13 5 L-11 2 L-7 2.2 L-9.5 9 L-7 9 L-3 2.2 Z" />
          </g>
        </g>
      </svg>
      <h4 className="bw-title">{title}<span className="bw-dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span></h4>
      {subtitle && <p className="bw-sub">{subtitle}</p>}
      {steps.length > 0 && (
        <ul className="bw-steps">
          {steps.map((s, i) => <li key={s} style={{ animationDelay: `${i * 0.6}s` }}>{s}</li>)}
        </ul>
      )}
    </div>
  );
}
