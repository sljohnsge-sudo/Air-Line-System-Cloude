// Website contacts and customer-facing texts that staff edit in Admin ›
// Administration › Website Contacts & Texts (16/11/2026). Loaded once from
// GET /api/site-content (backend services/site_content.py, which holds the
// real defaults). DEFAULTS below are only used if that call fails, so the
// site still reads correctly; keep them the same as the backend's.
const env = (k) => (import.meta.env[k] || '').trim();

const DEFAULTS = {
  support_phone: env('VITE_SUPPORT_PHONE'),
  support_email: env('VITE_SUPPORT_EMAIL'),
  support_whatsapp: '',
  support_address: env('VITE_SUPPORT_ADDRESS'),
  review_passenger_confirmation:
    'I confirm that all passenger details entered (names, passport information, dates of birth) are accurate and match my travel documents exactly. I understand that incorrect information may result in denied boarding, additional airline fees, or ticket cancellation with no refund.',
  review_bank_note:
    'Your booking is confirmed with the airline first and held for you. You then pay by bank transfer or deposit using your booking reference. Your ticket is issued and emailed as soon as our team confirms your payment has arrived.',
  review_cash_note:
    'Your booking is confirmed with the airline first and held for you. Pay in cash at a Tick and Book office using your booking reference; your ticket is issued and emailed as soon as we receive it.',
  eticket_info_title: 'Important Travel Information',
  eticket_info_items: [
    'Please carry a printout of this e-ticket receipt or have it ready on your mobile device during check-in.',
    'Most international flights require passengers to arrive at the airport check-in counter at least 3 hours before departure. Check-in gates close exactly 60 minutes prior.',
    'Passport Validity: Ensure your passport is valid for at least 6 months from the date of your journey. Many destinations require specific entry visas.',
    'Baggage policies may vary. Please contact Tick and Book support or the airline directly to purchase extra allowance.',
    'For support, flight updates, changes or cancellations, please contact the Tick and Book desk {support}.',
  ].join('\n'),
};

let current = { ...DEFAULTS };

export const setSiteContent = (values) => {
  current = { ...DEFAULTS, ...(values && typeof values === 'object' ? values : {}) };
};

export const siteText = (key) => (current[key] ?? '').toString();

// A "list" text as its non-empty lines.
export const siteLines = (key) => siteText(key).split('\n').map(s => s.trim()).filter(Boolean);
