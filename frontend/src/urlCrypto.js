// urlCrypto.js
// ============
// Encrypts the dynamic parts of system-generated URLs (search criteria on
// /results, the PNR on /booking/:locator, wizard sub-steps) so the address
// bar and browser history show an opaque token instead of readable booking
// data — synchronous (AES via crypto-js, not the async Web Crypto API) so it
// slots into App.jsx's existing synchronous "seed state from URL on mount"
// pattern without turning that into an async rewrite.
//
// Honest limit: the decryption key ships inside this JS bundle, same as any
// client-side-only scheme. That means this stops casual shoulder-surfing/
// browser-history reading of a PNR or search route, but it is NOT
// protection against someone who inspects the app's own code — there is no
// way to keep a key secret in code that runs entirely in the browser. Real
// confidentiality against that threat would require the server to hand back
// an opaque token it alone can resolve (a backend change, not requested here).

import CryptoJS from 'crypto-js';

const URL_CRYPTO_KEY = 'FT-2026-url-token-key-v1';

const toBase64Url = (base64) => base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const fromBase64Url = (base64url) => {
  let base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
  while (base64.length % 4) base64 += '=';
  return base64;
};

/** Encrypts a plain string (e.g. "HN6MK2" or "origin=CMB&destination=DXB")
 * into a URL-safe token. Returns null if given falsy input. */
export function encryptForUrl(plaintext) {
  if (!plaintext) return null;
  const ciphertext = CryptoJS.AES.encrypt(plaintext, URL_CRYPTO_KEY).toString();
  return toBase64Url(ciphertext);
}

/** Reverses encryptForUrl. Returns null if the token is missing, malformed,
 * or wasn't produced by this app (e.g. someone hand-typed a URL) — callers
 * treat a null result the same as "no data in the URL". */
export function decryptFromUrl(token) {
  if (!token) return null;
  try {
    const ciphertext = fromBase64Url(token);
    const bytes = CryptoJS.AES.decrypt(ciphertext, URL_CRYPTO_KEY);
    const plaintext = bytes.toString(CryptoJS.enc.Utf8);
    return plaintext || null;
  } catch {
    return null;
  }
}
