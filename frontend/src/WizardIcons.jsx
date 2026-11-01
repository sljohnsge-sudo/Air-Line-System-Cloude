// Small line icons for the booking wizard (stepper, passenger form and the
// "Your Flight" side card). Inline SVG, drawn in currentColor.
import React from 'react';

const Svg = ({ size = 16, children, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
    {children}
  </svg>
);

export const IconUser = (p) => <Svg {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" /></Svg>;
export const IconUsers = (p) => <Svg {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6" /><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.3c2.1.7 3.5 2.8 3.5 5.7" /></Svg>;
export const IconCalendar = (p) => <Svg {...p}><rect x="3" y="4.5" width="18" height="16.5" rx="2" /><path d="M3 9.5h18M8 2.5v4M16 2.5v4" /></Svg>;
export const IconGender = (p) => <Svg {...p}><circle cx="10" cy="14" r="5" /><path d="M13.6 10.4 20 4M15 4h5v5M10 19v3M8 21h4" /></Svg>;
export const IconPassport = (p) => <Svg {...p}><rect x="4.5" y="2.5" width="15" height="19" rx="2" /><circle cx="12" cy="10" r="3.2" /><path d="M8.5 17h7" /></Svg>;
export const IconFlag = (p) => <Svg {...p}><path d="M5 21V4M5 4h11l-2 4 2 4H5" /></Svg>;
export const IconGlobe = (p) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z" /></Svg>;
export const IconPhone = (p) => <Svg {...p}><path d="M5 3.5h3.5l1.7 4.3-2.2 1.4a11 11 0 0 0 6.8 6.8l1.4-2.2 4.3 1.7V19a2 2 0 0 1-2 2A16.5 16.5 0 0 1 3 5.5a2 2 0 0 1 2-2z" /></Svg>;
export const IconMail = (p) => <Svg {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3.5 6 8.5 7 8.5-7" /></Svg>;
export const IconMeal = (p) => <Svg {...p}><path d="M6 2.5v8M3.5 2.5v5a2.5 2.5 0 0 0 5 0v-5M6 10.5V21.5M17.5 21.5V2.5c-2.5 1.2-3.5 4-3.5 7v4h3.5" /></Svg>;
export const IconWheelchair = (p) => <Svg {...p}><circle cx="11" cy="3.8" r="1.6" /><path d="M11 6.5V13h6l2.5 5.5M11 9.5h5" /><path d="M8 10.2a6 6 0 1 0 8.4 7.6" /></Svg>;
export const IconPlane = (p) => <Svg {...p}><path d="M21 15.5v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0v5l-8 5v2l8-2.5V18l-2 1.5V21l3.5-1 3.5 1v-1.5L13 18v-5z" /></Svg>;
export const IconSeat = (p) => <Svg {...p}><path d="M6 3.5v9.5a2 2 0 0 0 2 2h7.5l2.5 5.5M6 15l-1.5 5.5M9 19.5h7" /><path d="M9 11h5.5" /></Svg>;
export const IconCard = (p) => <Svg {...p}><rect x="2.5" y="5" width="19" height="14" rx="2" /><path d="M2.5 10h19M6 15h4" /></Svg>;
export const IconTag = (p) => <Svg {...p}><path d="M3 12V4h8l10 10-8 8z" /><circle cx="7.5" cy="8.5" r="1.3" /></Svg>;
export const IconBag = (p) => <Svg {...p}><rect x="4" y="7" width="16" height="13" rx="2" /><path d="M9 7V4.5h6V7M9 11v5M15 11v5" /></Svg>;
export const IconInfo = (p) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></Svg>;
export const IconChevronLeft = (p) => <Svg {...p}><path d="m15 6-6 6 6 6" /></Svg>;
export const IconChevronRight = (p) => <Svg {...p}><path d="m9 6 6 6-6 6" /></Svg>;
export const IconChevronDown = (p) => <Svg {...p}><path d="m6 9 6 6 6-6" /></Svg>;
export const IconBed = (p) => <Svg {...p}><path d="M3 18V7M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5" /><circle cx="7" cy="11" r="2" /></Svg>;
export const IconSwap = (p) => <Svg {...p}><path d="M4 8h14l-3-3M20 16H6l3 3" /></Svg>;
export const IconRoute = (p) => <Svg {...p}><circle cx="6" cy="18" r="2.2" /><circle cx="18" cy="6" r="2.2" /><path d="M8 18h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7" /></Svg>;
