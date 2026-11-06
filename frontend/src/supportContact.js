// Tick and Book's own customer contact details, from frontend/.env
// (VITE_SUPPORT_PHONE / VITE_SUPPORT_EMAIL / VITE_SUPPORT_ADDRESS). Each is
// shown on e-tickets and support notes only when set. Customers must never
// see George Steuart Travel's name, address, phone or emails: GS also runs a
// travel agency, and linking the two brings price-comparison calls to its
// agents.
export const SUPPORT_PHONE = (import.meta.env.VITE_SUPPORT_PHONE || '').trim();
export const SUPPORT_EMAIL = (import.meta.env.VITE_SUPPORT_EMAIL || '').trim();
export const SUPPORT_ADDRESS = (import.meta.env.VITE_SUPPORT_ADDRESS || '').trim();

// "Tel: … | Email: …" — empty when neither is set.
export const supportContactLine = () =>
  [SUPPORT_PHONE && `Tel: ${SUPPORT_PHONE}`, SUPPORT_EMAIL && `Email: ${SUPPORT_EMAIL}`].filter(Boolean).join(' | ');

// How to reach support, as the end of a sentence.
export const supportHow = () => {
  if (SUPPORT_PHONE && SUPPORT_EMAIL) return `at ${SUPPORT_PHONE} or email ${SUPPORT_EMAIL}`;
  if (SUPPORT_PHONE) return `at ${SUPPORT_PHONE}`;
  if (SUPPORT_EMAIL) return `by email at ${SUPPORT_EMAIL}`;
  return 'through My Account → Manage my booking';
};
