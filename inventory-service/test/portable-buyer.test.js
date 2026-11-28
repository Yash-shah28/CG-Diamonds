import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import { Writable } from 'node:stream';
import { forwardLegacyRequest } from '../lib/legacy-forward.js';
import { snapshotPath } from '../lib/inventory-service.js';
import { workspaceHandler } from '../owner/routes.js';
import { hash } from '../owner/security.js';

test('existing buyer key reads the same snapshot through the portable service and Vercel forwarder', async () => {
  const id = 'b'.repeat(32);
  const key = 'cg_read_' + 'legacy-private-key-fixture'.repeat(2);
  const inventory = { natural: [{ StockId: '001', PricePerCarat: '1350.00' }], lab_grown: [] };
  const raw = JSON.stringify(inventory);
  const meta = { client_id: id, revision: 17, sha256: hash(raw) };
  const objects = new Map([
    ['inventory/keys/' + hash(key) + '.json', { client_id: id }],
    ['inventory/control/' + id + '.json', { ...meta, key_hash: hash(key), enabled: true,
      records: 1, snapshot: snapshotPath(meta), published_at: '2026-11-26T02:00:00Z' }],
  ]);
  const storage = {
    async json(path) { return objects.has(path) ? { value: objects.get(path), etag: '1' } : null; },
    async putJson(path, body) { objects.set(path, body); },
    async stream(path) { assert.equal(path, snapshotPath(meta)); return new Response(raw).body; },
  };
  const handle = workspaceHandler({ pool: {}, auth: {}, storage, env: {
    WORKSPACE_PORTABLE_BUYER_API: 'true', LEGACY_BRIDGE_TOKEN: 'fixture-only-token',
  } });
  const response = new Writable({ write(chunk, encoding, next) { this.fragments.push(Buffer.from(chunk)); next(); } });
  response.fragments = [];
  response.headers = {};
  response.setHeader = (key, value) => { response.headers[key] = value; };
  response.status = code => { response.statusCode = code; return response; };
  let observed;
  const fetcher = async (url, options) => {
    observed = { url, options };
    return handle(new Request(url, options));
  };
  assert(await forwardLegacyRequest({ method: 'GET', headers: { 'x-api-key': key } }, response,
    '/api/v1/inventory', { OWNER_BUYER_FORWARD_ORIGIN: 'https://portable.example.test' }, fetcher));
  assert.equal(observed.url, 'https://portable.example.test/api/v1/inventory');
  assert.equal(observed.options.headers.get('x-api-key'), key);
  assert.equal(response.statusCode, 200);
  assert.deepEqual(JSON.parse(Buffer.concat(response.fragments)), inventory);
  assert.equal(response.headers['x-inventory-snapshot'], id + ':17');
  assert(response.headers['x-request-id']);
  assert.equal(objects.get('inventory/access/' + id + '/latest.json').natural_count, 1);
  const wrong = await handle(new Request('https://portable.example.test/api/v1/inventory', { headers: { 'x-api-key': randomUUID() } }));
  assert.equal(wrong.status, 401);
});
