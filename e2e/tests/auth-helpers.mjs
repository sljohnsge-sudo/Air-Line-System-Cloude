// Shared helpers for the sign-in tests (16/11/2026). No real keys: forged
// tokens are signed with a random key made here, so a site that accepts one
// would be accepting anyone.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

// Temporary test accounts from e2e/tools/auth_accounts.py (laptop only). When
// the file is missing (server, CI) the tests that need an account are skipped.
export function testAccounts() {
  const file = path.join(here, '..', '.auth-accounts.json');
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

const b64url = (buf) => Buffer.from(buf).toString('base64').replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');

export function forgedToken(payload, { alg = 'HS256', key = crypto.randomBytes(32) } = {}) {
  const head = b64url(JSON.stringify({ alg, typ: 'JWT' }));
  const body = b64url(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, ...payload }));
  if (alg === 'none') return `${head}.${body}.`;
  const sig = b64url(crypto.createHmac('sha256', key).update(`${head}.${body}`).digest());
  return `${head}.${body}.${sig}`;
}

// A real token with its contents changed but the original signature kept.
export function tamperedToken(token, changes) {
  const [head, body, sig] = token.split('.');
  const payload = JSON.parse(Buffer.from(body.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString());
  return `${head}.${b64url(JSON.stringify({ ...payload, ...changes }))}.${sig}`;
}

export function tokenPayload(token) {
  const body = token.split('.')[1];
  return JSON.parse(Buffer.from(body.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString());
}

export const bearer = (token) => ({ Authorization: `Bearer ${token}` });
