import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createPublicKey } from 'node:crypto';
import { ownerRequest } from '../api/owner-runtime.js';

test('Vercel rewrite keeps the signed update query and the owner request body', async () => {
  const origin = 'https://owners.example.test';
  const update = ownerRequest(new Request(origin + '/api/owner-runtime?__owner_path=/owner/releases&current=0.9.0'), origin);
  assert.equal(update.url, origin + '/owner/releases?current=0.9.0');
  const auth = ownerRequest(new Request(origin + '/api/owner-runtime?__owner_path=/owner/sign-in', {
    method: 'POST', body: JSON.stringify({ login: 'owner' }),
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer fixture' },
  }), origin);
  assert.equal(auth.headers.get('Authorization'), 'Bearer fixture');
  assert.deepEqual(await auth.json(), { login: 'owner' });
  assert.equal(ownerRequest(new Request(origin + '/api/owner-runtime?__owner_path=/api/v1/admin'), origin), null);
  assert.equal(ownerRequest(new Request(origin + '/api/owner-runtime?__owner_path=/healthz&__owner_path=/platform/owner'), origin), null);
  const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url)));
  assert(config.rewrites.some(rewrite => rewrite.source === '/owner/:path*'));
  assert.equal(config.outputDirectory, 'public');
});

test('the Vercel and desktop key files verify the same Windows release signatures', async () => {
  const files = ['../owner/update-public-key.pem', '../../drc_sync/assets/update-public-key.pem'];
  const [backend, desktop] = await Promise.all(files.map(file => readFile(new URL(file, import.meta.url), 'utf8')));
  const der = key => createPublicKey(key).export({ type: 'spki', format: 'der' });
  assert.deepEqual(der(backend), der(desktop));
});
