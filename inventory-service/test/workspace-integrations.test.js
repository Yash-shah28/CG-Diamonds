import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import { integrations } from '../owner/integrations.js';
import { hash, seal } from '../owner/security.js';

test('provider jobs use workspace vault entries and deduplicate successful submissions', async () => {
  const company = { id: randomUUID(), features: ['natural', 'rapaport', 'email'] };
  const uploadId = randomUUID();
  const buyerId = '1'.repeat(32), key = 'cg_read_test-only-fixture';
  const credentials = { rapaport: { client_id: 'fixture-client', client_secret: 'fixture-secret' },
    email: { sender: 'owner@yahoo.com', password: 'fixture-app-password' } };
  const encryptionKey = Buffer.alloc(32, 4);
  const jobs = new Map();
  const pool = {
    async query(sql, args) {
      assert.equal(args[0], company.id);
      if (sql.includes('FROM owner_credentials')) {
        return { rows: [{ encrypted: seal(encryptionKey, company.id, args[1], credentials[args[1]]) }] };
      }
      if (sql.includes('FROM owner_buyers')) {
        assert.equal(args[1], buyerId);
        return { rows: [{ key_hash: hash(key), settings: { email: 'buyer@example.test', feed_url: 'https://legacy.example.test/api/v1/inventory' } }] };
      }
      const requestId = args[1];
      if (sql.includes('INSERT INTO owner_jobs')) {
        if (jobs.has(requestId)) return { rowCount: 0 };
        jobs.set(requestId, { provider: args[2], operation: args[3], state: 'dispatching' });
        return { rowCount: 1 };
      }
      if (sql.includes("state='completed'")) {
        Object.assign(jobs.get(requestId), { state: 'completed', result: args[2] });
        return { rowCount: 1 };
      }
      if (sql.includes('SELECT provider,operation,state,result')) return { rows: [jobs.get(requestId)] };
      throw Error('Unexpected query for isolated provider test');
    },
  };
  const bytes = Buffer.from('Stock #,Availability,Shape,Weight,PricePerCarat\r\n1,G,Round,1.02,1200\r\n');
  let uploads = 0;
  let messages = 0;
  const run = integrations({ pool, encryptionKey, storage: { async bytes() { return bytes; } },
    async fetcher(url, options) {
      if (url.includes('authztoken')) {
        assert.deepEqual(JSON.parse(options.body), credentials.rapaport);
        return Response.json({ access_token: 'fixture-access' });
      }
      assert.equal(options.headers.Authorization, 'Bearer fixture-access');
      assert.equal(options.body.get('ReplaceAll'), 'false');
      assert.equal(await options.body.get('File').text(), bytes.toString());
      uploads++;
      return Response.json({ uploadId });
    },
    transportFactory(value) {
      assert.deepEqual(value, credentials.email);
      return { async sendMail(message) {
        messages++;
        assert.equal(message.to, 'buyer@example.test');
        assert.match(message.text, /cg_read_test-only-fixture/);
        assert.equal(message.attachments[0].content.toString(), '%PDF fixture');
      } };
    },
  });
  const natural = { provider: 'rapaport', operation: 'upload', request_id: randomUUID(), size: bytes.length, sha256: hash(bytes) };
  const email = { provider: 'email', operation: 'send', request_id: randomUUID(), buyer_id: buyerId, key,
    guide: Buffer.from('%PDF fixture').toString('base64') };
  assert.deepEqual(await run(company, natural), { uploadId });
  assert.deepEqual(await run(company, natural), { uploadId });
  assert.deepEqual(await run(company, email), { sent: true });
  assert.deepEqual(await run(company, email), { sent: true });
  assert.equal(uploads, 1);
  assert.equal(messages, 1);
  assert(!JSON.stringify([...jobs.values()]).includes(key));
  await assert.rejects(run({ ...company, features: ['natural'] }, natural), { status: 403 });
});
