// Tick and Book's own customer contact details. Since 16/11/2026 staff set
// them in Admin › Website Contacts & Texts (siteContent.js); until saved
// there, the old frontend/.env values (VITE_SUPPORT_*) are used. Each is
// shown on e-tickets and support notes only when set. Customers must never
// see George Steuart Travel's name, address, phone or emails: GS also runs a
// travel agency, and linking the two brings price-comparison calls to its
// agents.
import { siteText } from './siteContent.js';

export const supportPhone = () => siteText('support_phone').trim();
export const supportEmail = () => siteText('support_email').trim();
export const supportAddress = () => siteText('support_address').trim();
export const supportWhatsapp = () => siteText('support_whatsapp').trim();

// "Tel: … | WhatsApp: … | Email: …" — empty when none is set.
export const supportContactLine = () =>
  [supportPhone() && `Tel: ${supportPhone()}`, supportWhatsapp() && `WhatsApp: ${supportWhatsapp()}`,
   supportEmail() && `Email: ${supportEmail()}`].filter(Boolean).join(' | ');

// How to reach support, as the end of a sentence.
export const supportHow = () => {
  const phone = supportPhone(), email = supportEmail();
  if (phone && email) return `at ${phone} or email ${email}`;
  if (phone) return `at ${phone}`;
  if (email) return `by email at ${email}`;
  return 'through My Account → Manage my booking';
};
