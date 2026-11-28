import assert from 'node:assert/strict';
import test from 'node:test';
import { createCipheriv, createHash, generateKeyPairSync, randomBytes, randomUUID, sign } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
import { migrateDatabase, ownerAuth } from '../owner/auth.js';
import { workspacePool } from '../owner/database.js';
import { workspaceHandler } from '../owner/routes.js';
import { hash, seal, unseal } from '../owner/security.js';

test('workspace secrets authenticate their company and provider', () => {
  const key = Buffer.alloc(32, 7), id = randomUUID();
  const value = seal(key, id, 'hrd', { token: 'secret-fixture' });
  assert(!JSON.stringify(value).includes('secret-fixture'));
  assert.deepEqual(unseal(key, id, 'hrd', value), { token: 'secret-fixture' });
  assert.throws(() => unseal(key, randomUUID(), 'hrd', value));
  assert.throws(() => unseal(Buffer.alloc(32, 8), id, 'hrd', value));
});

test('a project administrator can provision the private schema alongside existing tables', async t => {
  const db = await PGlite.create();
  t.after(() => db.close());
  await db.exec(`
    CREATE TABLE public.inventory (id text PRIMARY KEY);
    INSERT INTO public.inventory VALUES ('legacy-inventory');
    CREATE ROLE fixture_project_admin CREATEROLE;
    GRANT CREATE ON DATABASE postgres TO fixture_project_admin;
    GRANT SELECT ON public.inventory TO fixture_project_admin;
    SET SESSION AUTHORIZATION fixture_project_admin;
  `);
  await db.exec(await readFile(new URL('../owner/provision.sql', import.meta.url), 'utf8'));
  const { rows: namespaces } = await db.query(`
    SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname = 'chintamani_owner'
  `);
  assert.deepEqual(namespaces, [{ owner: 'chintamani_owner' }]);
  assert.deepEqual((await db.query('SELECT * FROM public.inventory')).rows, [{ id: 'legacy-inventory' }]);
  await db.exec('SET ROLE chintamani_owner');
  await assert.rejects(db.query('SELECT * FROM public.inventory'), { code: '42501' });
});

test('owner login, legacy migration, backup and company-scoped integrations', { timeout: 65_000 }, async t => {
  let db = await PGlite.create();
  let socket = new PGLiteSocketServer({ db, host: '127.0.0.1', port: 0 });
  let pool;
  t.after(async () => { await pool?.end(); await socket.stop(); await db.close(); });
  await db.exec(`
    CREATE TABLE public."user" (legacy_id text PRIMARY KEY);
    INSERT INTO public."user" VALUES ('existing-api-account');
    CREATE SCHEMA chintamani_owner;
    CREATE ROLE anon;
    GRANT USAGE ON SCHEMA chintamani_owner TO anon;
  `);
  await socket.start();
  pool = workspacePool('postgresql://postgres@' + socket.getServerConn() + '/postgres', { max: 1 });
  await assert.rejects(pool.query('SELECT 1'), /private chintamani_owner schema/);
  await db.exec('REVOKE USAGE ON SCHEMA chintamani_owner FROM anon');
  const env = {
    WORKSPACE_AUTH_SECRET: 'portable-test-secret-shared-at-cutover-0',
    WORKSPACE_ORIGIN: 'https://owners.example.test',
    WORKSPACE_ADMIN_TOKEN_SHA256: hash('platform-test-key'),
    LEGACY_BRIDGE_TOKEN: 'legacy-test-key',
    LEGACY_PUBLIC_ORIGIN: 'https://legacy.example.test',
  };
  const encryptionKey = Buffer.alloc(32, 3);
  const auth = ownerAuth(pool, env);
  await migrateDatabase(pool, auth);
  await migrateDatabase(pool, auth);
  const { rows: legacyAccounts } = await pool.query('SELECT * FROM public."user"');
  assert.deepEqual(legacyAccounts, [{ legacy_id: 'existing-api-account' }]);
  const { rows: namespaces } = await pool.query(`
    SELECT table_schema, table_name FROM information_schema.tables
    WHERE table_name IN ('user', 'owner_workspaces') ORDER BY 1, 2
  `);
  assert.deepEqual(namespaces, [
    { table_schema: 'chintamani_owner', table_name: 'owner_workspaces' },
    { table_schema: 'chintamani_owner', table_name: 'user' },
    { table_schema: 'public', table_name: 'user' },
  ]);
  const keys = generateKeyPairSync('ed25519');
  const inventory = { id: 'a'.repeat(32), key_hash: hash('cg_read_durable-key'), name: 'Legacy buyer', email: 'buyer@example.test',
    kinds: ['Natural'], mode: 'discount', percent: '5', active: 1, revision: 7, published: 1 };
  const installer = Buffer.from('fixture installer');
  const objectKey = 'private-releases/0.9.0/ChintamaniGemsSetup-0.9.0.exe';
  const objects = new Map();
  const storage = {
    async stream(key) {
      assert.equal(key, objectKey);
      return new Response(installer).body;
    },
    async download(key) {
      if (key.startsWith('owner-backups/')) return 'https://private.example.test/' + key;
      assert.equal(key, objectKey);
      return 'https://private.example.test/get-app?expires=120';
    },
    async putBackup(key, bytes) { objects.set(key, bytes); },
    async bytes(key) { return objects.get(key); },
    async authorizeBackup(key) { return 'https://private.example.test/' + key; },
    async removeBackupStage(key) { objects.delete(key); },
  };
  const requests = [];
  const fetcher = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith('/api/v1/admin')) {
      assert.equal(options.headers.Authorization, 'Bearer legacy-test-key');
      const data = JSON.parse(options.body);
      assert.equal(data.client_id, inventory.id);
      assert.equal(data.action, 'status');
      return Response.json({ ok: true, enabled: true, key_hash: inventory.key_hash, revision: 7, records: 1 });
    }
    assert.equal(url, 'https://my.hrdantwerp.com/api/public/GetReport/12345');
    assert.equal(options.headers['API-Token'], 'hrd-fixture-token');
    return new Response('<Report><ReportNumber>12345</ReportNumber></Report>');
  };
  let handle = workspaceHandler({ pool, auth, storage, encryptionKey, publicKey: keys.publicKey, env, fetcher });
  const call = async (path, { method, token, data, status = 200 } = {}) => {
    const headers = new Headers(token ? { Authorization: 'Bearer ' + token } : {});
    if (data) headers.set('Content-Type', 'application/json');
    const response = await handle(new Request(env.WORKSPACE_ORIGIN + path, {
      method: method || (data ? 'POST' : 'GET'), headers, ...(data ? { body: JSON.stringify(data) } : {}),
    }), 'local-test');
    const result = response.status === 204 ? null : await response.json();
    assert.equal(response.status, status, JSON.stringify(result));
    return result;
  };
  const admin = data => call('/platform/owner', { token: 'platform-test-key', data });
  const a = await admin({ action: 'create-workspace', name: 'Company A', slug: 'company-a', login: 'company-a', legacy_import: true, backup_enabled: true, features: ['natural', 'hrd', 'buyer_feeds'] });
  const b = await admin({ action: 'create-workspace', name: 'Company B', slug: 'company-b', login: 'company-b', features: ['natural'] });
  const password = 'New-local-test-passphrase-074';
  async function authenticate(account) {
    const first = await call('/owner/sign-in', { data: { login: account.login, password: account.temporary_password } });
    assert(first.must_change_password);
    await call('/owner/bootstrap', { token: first.token, status: 403 });
    const session = await call('/owner/password', { token: first.token, data: { current_password: account.temporary_password, password } });
    await call('/owner/bootstrap', { token: first.token, status: 401 });
    return session.token;
  }
  const ownerA = await authenticate(a), ownerB = await authenticate(b);
  await admin({ action: 'set-features', workspace_id: a.id, features: ['natural', 'hrd', 'buyer_feeds', 'rapaport'] });
  await call('/owner/credentials/rapaport', { method: 'PUT', token: ownerA,
    data: { client_id: 'example-rapaport-account', client_secret: 'fixture-provider-secret' } });
  const rapaportInfo = await call('/owner/bootstrap', { token: ownerA });
  assert.equal(rapaportInfo.provider_refs.rapaport, hash('example-rapaport-account'));
  assert(!JSON.stringify(rapaportInfo).includes('fixture-provider-secret'));
  assert.equal((await call('/owner/bootstrap', { token: ownerB })).provider_refs.rapaport, undefined);
  await call('/owner/bootstrap', { status: 401 });
  const archive = Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.from('private-workbook-data')]);
  const device = randomUUID();
  const sendBackup = (owner, backupDevice, current, data = archive, digest = hash(archive)) =>
    handle(new Request(env.WORKSPACE_ORIGIN + '/owner/backup', { method: 'PUT', body: data, headers: {
      Authorization: 'Bearer ' + owner, 'Content-Length': String(data.length),
      'X-Backup-Device': backupDevice, 'X-Backup-Revision': String(current), 'X-Backup-Sha256': digest,
    } }));
  assert.equal((await sendBackup(ownerB, device, 0)).status, 403);
  assert.equal((await sendBackup(ownerA, device, 0, Buffer.concat([archive, archive]))).status, 400);
  assert.equal((await sendBackup(ownerA, device, 0)).status, 200);
  assert.equal((await sendBackup(ownerA, device, 0)).status, 200);
  assert.equal((await sendBackup(ownerA, randomUUID(), 1)).status, 409);
  assert.equal(objects.size, 1);
  assert.notEqual(Buffer.compare([...objects.values()][0], archive), 0);
  const restored = await handle(new Request(env.WORKSPACE_ORIGIN + '/owner/backup/download',
    { headers: { Authorization: 'Bearer ' + ownerA } }));
  assert.equal(restored.status, 200);
  assert.deepEqual(Buffer.from(await restored.arrayBuffer()), archive);
  await admin({ action: 'set-backup', workspace_id: b.id, enabled: true });
  assert.equal((await call('/owner/backup', { token: ownerB })).backup, null);
  const oldGrant = await call('/owner/backup/grant', { token: ownerA });
  assert.equal(oldGrant.direct, true);
  assert.equal(oldGrant.revision, 1);
  const changed = Buffer.concat([archive, Buffer.from('-revision-two')]);
  const digest = hash(changed);
  const lease = await call('/owner/backup/prepare', { token: ownerA,
    data: { digest, device, size: changed.length, revision: 1 } });
  await call('/owner/backup/finish', { token: ownerB, data: { ticket: lease.ticket }, status: 403 });
  const encryption = Buffer.from(lease.encryption, 'base64');
  assert.equal(encryption.length, 32);
  assert.notDeepEqual(encryption, encryptionKey);
  const stage = new URL(lease.upload_url).pathname.slice(1);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', encryption, iv);
  cipher.setAAD(Buffer.from(a.id + ':' + digest));
  const encrypted = Buffer.concat([cipher.update(changed), cipher.final()]);
  objects.set(stage, Buffer.alloc(changed.length + 28));
  await call('/owner/backup/finish', { token: ownerA, data: { ticket: lease.ticket }, status: 400 });
  const encryptedArchive = Buffer.concat([iv, cipher.getAuthTag(), encrypted]);
  objects.set(stage, encryptedArchive);
  const savedBackup = await call('/owner/backup/finish', { token: ownerA, data: { ticket: lease.ticket } });
  assert.equal(savedBackup.backup.revision, 2);
  assert.equal(savedBackup.backup.sha256, digest);
  assert(!objects.has(stage));
  const grant = await call('/owner/backup/grant', { token: ownerA });
  assert.equal(grant.encryption, lease.encryption);
  assert.equal(grant.revision, 2);
  const payload = { migration_id: randomUUID(), profiles: [], buyers: [inventory], credentials: { hrd: { token: 'hrd-fixture-token' } } };
  await call('/owner/migrate', { token: ownerA, data: payload });
  await call('/owner/migrate', { token: ownerA, data: payload });
  const { rows: stored } = await pool.query('SELECT * FROM owner_credentials');
  assert.deepEqual(stored.map(row => row.provider).sort(), ['hrd', 'rapaport']);
  assert(!JSON.stringify(stored).includes('hrd-fixture-token'));
  const migrated = await call('/owner/inventory-admin', { token: ownerA, data: { action: 'status', client_id: inventory.id } });
  assert.equal(migrated.key_hash, inventory.key_hash);
  await call('/owner/inventory-admin', { token: ownerA, data: { action: 'revoke', client_id: inventory.id }, status: 409 });
  await call('/owner/inventory-admin', { token: ownerB, data: { action: 'status', client_id: inventory.id }, status: 403 });
  await admin({ action: 'set-features', workspace_id: b.id, features: ['natural', 'buyer_feeds'] });
  await call('/owner/inventory-admin', { token: ownerB, data: { action: 'status', client_id: inventory.id }, status: 404 });
  await call('/owner/integrations', { token: ownerB, data: { provider: 'hrd', operation: 'lookup', report: '12345', request_id: randomUUID() }, status: 403 });
  const report = await call('/owner/integrations', { token: ownerA, data: { provider: 'hrd', operation: 'lookup', report: '12345', request_id: randomUUID() } });
  assert.match(report.xml, /12345/);
  const payloadBytes = Buffer.from(JSON.stringify({ schema: 2, app: 'chintamani-gems', platform: 'windows-x64', version: '0.9.0',
    object_key: objectKey, size: installer.length, sha256: createHash('sha256').update(installer).digest('hex') }));
  await admin({ action: 'register-release', envelope: { payload: payloadBytes.toString('base64'), signature: sign(null, payloadBytes, keys.privateKey).toString('base64') } });
  await admin({ action: 'assign-release', workspace_id: a.id, version: '0.9.0' });
  assert.match((await call('/owner/releases', { token: ownerA })).download_url, /expires=120/);
  await call('/owner/releases', { token: ownerB, status: 204 });

  // Restore the data into a separate PostgreSQL instance before starting the new backend.
  await pool.end();
  await socket.stop();
  const exported = await db.dumpDataDir();
  await db.close();
  db = await PGlite.create({ loadDataDir: exported });
  socket = new PGLiteSocketServer({ db, host: '127.0.0.1', port: 0 });
  await socket.start();
  pool = workspacePool('postgresql://postgres@' + socket.getServerConn() + '/postgres', { max: 1 });
  const replacement = ownerAuth(pool, env);
  handle = workspaceHandler({ pool, auth: replacement, storage, encryptionKey, publicKey: keys.publicKey, env, fetcher });
  const signedIn = await call('/owner/sign-in', { data: { login: a.login, password } });
  assert.equal(signedIn.must_change_password, false);
  assert.equal((await call('/owner/bootstrap', { token: signedIn.token })).id, a.id);
  assert.match((await call('/owner/integrations', { token: signedIn.token, data: {
    provider: 'hrd', operation: 'lookup', report: '12345', request_id: randomUUID(),
  } })).xml, /12345/);
  assert.match((await call('/owner/releases', { token: signedIn.token })).download_url, /expires=120/);
  const reset = await admin({ action: 'reset-password', workspace_id: a.id });
  await call('/owner/bootstrap', { token: signedIn.token, status: 401 });
  assert((await call('/owner/sign-in', { data: { login: a.login, password: reset.temporary_password } })).must_change_password);
  assert.equal(requests.filter(item => item.url.includes('rapnet')).length, 0);
});
