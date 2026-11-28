import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { test } from 'node:test';
import { vercelInventorySource, verifyObjectCopy } from '../owner/copy-storage.js';
import { privateStorage } from '../owner/storage.js';

test('legacy private inventory is copied and verified across all source pages', async () => {
  const fixtures = new Map([
    ['inventory/control/buyer.json', Buffer.from('{"enabled":true}')],
    ['inventory/snapshots/buyer/revision.json', Buffer.from('{"natural":[{"Stock #":"3791"}],"lab_grown":[]}')],
  ]);
  const paths = [...fixtures.keys()];
  const source = vercelInventorySource('store_fixture', 'test-token', {
    async list({ storeId, token, prefix, cursor }) {
      assert.equal(storeId, 'store_fixture');
      assert.equal(token, 'test-token');
      assert.equal(prefix, 'inventory/');
      return cursor === 'page-2' ? { blobs: [{ pathname: paths[1] }], hasMore: false } :
        { blobs: [{ pathname: paths[0] }], hasMore: true, cursor: 'page-2' };
    },
    async get(path, { access, useCache }) {
      assert.equal(access, 'private');
      assert.equal(useCache, false);
      return { blob: { contentType: 'application/json' }, stream: Readable.toWeb(Readable.from([fixtures.get(path)])) };
    },
  });
  const copied = new Map([[paths[0], Buffer.from('different')], ['private-releases/keep', Buffer.from('installer')]]);
  const target = {
    async read(path) { return copied.has(path) ? Readable.from([copied.get(path)]) : null; },
    async write(path, body, size, contentType) {
      assert.equal(contentType, 'application/json');
      const chunks = [];
      for await (const chunk of body) chunks.push(chunk);
      const value = Buffer.concat(chunks);
      assert.equal(value.length, size);
      copied.set(path, value);
    },
  };
  await assert.rejects(verifyObjectCopy(source, target), /different checksum/);
  assert.equal(copied.get(paths[0]).toString(), 'different');
  assert.equal(await verifyObjectCopy(source, target, true), 2);
  assert.equal(await verifyObjectCopy(source, target), 2);
  assert.deepEqual(copied.get(paths[0]), fixtures.get(paths[0]));
  assert.deepEqual(copied.get(paths[1]), fixtures.get(paths[1]));
  assert.equal(copied.get('private-releases/keep').toString(), 'installer');
});

test('a portable snapshot upload grant signs its create-only precondition', async () => {
  const storage = privateStorage({
    WORKSPACE_S3_BUCKET: 'inventory-fixture',
    WORKSPACE_S3_ENDPOINT: 'https://storage.example.test',
    WORKSPACE_S3_ACCESS_KEY: 'fixture-access',
    WORKSPACE_S3_SECRET_KEY: 'fixture-secret',
  });
  const grant = new URL(await storage.authorize('inventory/snapshot.json', 24));
  assert.equal(grant.searchParams.get('X-Amz-Expires'), '300');
  assert(grant.searchParams.get('X-Amz-SignedHeaders').split(';').includes('if-none-match'));
});
