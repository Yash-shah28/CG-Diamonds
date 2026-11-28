import { createCipheriv, createDecipheriv, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { ApiError } from '../lib/inventory-service.js';
import { withTransaction } from './auth.js';
import { hash, keyHash, seal, unseal, uuid } from './security.js';

export const BACKUP_LIMIT = 64 * 1024 * 1024;
const columns = 'device_id, revision, sha256, size, object_key, saved_at, cipher_format';
const jsonHeaders = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' };

function cryptKey(key) {
  return createHmac('sha256', key).update('chintamani-owner-backup-v1').digest();
}

function objectCipherKey(master, objectKey) {
  return createHmac('sha256', cryptKey(master)).update('object:' + objectKey).digest();
}

function encrypt(key, scope, raw) {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(Buffer.from(scope));
  const content = Buffer.concat([cipher.update(raw), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), content]);
}

function decrypt(key, scope, stored) {
  const cipher = createDecipheriv('aes-256-gcm', key, stored.subarray(0, 12));
  cipher.setAAD(Buffer.from(scope));
  cipher.setAuthTag(stored.subarray(12, 28));
  return Buffer.concat([cipher.update(stored.subarray(28)), cipher.final()]);
}

async function exactBackup(request, size, digest) {
  const chunks = [];
  let received = 0;
  for await (const chunk of request.body) {
    received += chunk.length;
    if (received > size) throw new ApiError(413, 'Backup exceeds the declared size');
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks);
  if (!matches(raw, size, digest)) {
    throw new ApiError(400, 'Backup file or its checksum is invalid');
  }
  return raw;
}

function matches(raw, size, digest) {
  return raw.length === size && raw.length >= 4 && raw.readUInt32LE(0) === 0x04034b50 && hash(raw) === digest;
}

const summary = (row) => row && { device_id: row.device_id, revision: Number(row.revision),
  sha256: row.sha256, size: row.size, saved_at: row.saved_at, direct: row.cipher_format === 2 };
const out = data => Response.json(data, { headers: jsonHeaders });
const find = async (db, id) => (await db.query(`SELECT ${columns} FROM owner_backups WHERE workspace_id=$1`, [id])).rows[0];

function checkBackupInfo({ device, digest, size, revision }) {
  if (!uuid(device) || !keyHash(digest) || !Number.isSafeInteger(size) || size < 4 || size > BACKUP_LIMIT
      || !Number.isSafeInteger(revision) || revision < 0) {
    throw new ApiError(400, 'Invalid backup information or size');
  }
}

function checkRevision(prior, { device, digest, revision }) {
  if (prior && prior.device_id !== device) throw new ApiError(409, 'Another PC owns this workspace backup');
  if (prior?.sha256 === digest) return prior;
  if (revision !== Number(prior?.revision || 0)) throw new ApiError(409, 'The workspace backup has changed');
}

async function commit(pool, id, metadata, objectKey, format, writeVerifiedObject) {
  return withTransaction(pool, async db => {
    const locked = await db.query('SELECT backup_enabled FROM owner_workspaces WHERE id=$1 FOR UPDATE', [id]);
    if (!locked.rows[0]?.backup_enabled) throw new ApiError(403, 'Company backup is not enabled');
    const prior = checkRevision(await find(db, id), metadata);
    if (prior) return prior;
    await writeVerifiedObject();
    const { device, revision, digest, size } = metadata;
    const { rows } = await db.query(`
      INSERT INTO owner_backups(workspace_id,device_id,revision,sha256,size,object_key,cipher_format)
      VALUES($1,$2,$3,$4,$5,$6,$7)
      ON CONFLICT(workspace_id) DO UPDATE SET revision=EXCLUDED.revision,sha256=EXCLUDED.sha256,
      size=EXCLUDED.size,object_key=EXCLUDED.object_key,cipher_format=EXCLUDED.cipher_format,saved_at=now() RETURNING ${columns}`,
    [id, device, revision + 1, digest, size, objectKey, format]);
    return rows[0];
  });
}

async function command(request) {
  if (!request.body) throw new ApiError(400, 'Invalid backup request');
  let size = 0;
  const chunks = [];
  for await (const chunk of request.body) {
    size += chunk.length;
    if (size > 4096) throw new ApiError(413, 'Backup request is too large');
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new ApiError(400, 'Invalid backup request'); }
}

async function prepare(request, id, pool, storage, key, limit) {
  const info = await command(request);
  checkBackupInfo(info || {});
  if (info.size > limit) throw new ApiError(413, 'Backup exceeds the configured storage limit');
  const saved = checkRevision(await find(pool, id), info);
  if (saved) return out({ backup: summary(saved) });
  const upload = randomUUID();
  const object = `owner-backups/${id}/${upload}.bin`;
  const stage = `owner-backup-staging/${id}/${upload}.bin`;
  const ticket = seal(key, id, 'backup-upload', {
    device: info.device, digest: info.digest, size: info.size, revision: info.revision,
    upload, expires: Date.now() + 300_000,
  });
  return out({ ticket, encryption: objectCipherKey(key, object).toString('base64'),
    upload_url: await storage.authorizeBackup(stage, info.size + 28) });
}

async function finish(request, id, pool, storage, key) {
  const data = await command(request);
  let info;
  try { info = unseal(key, id, 'backup-upload', data.ticket); }
  catch { throw new ApiError(403, 'Backup upload authorization is invalid'); }
  checkBackupInfo(info || {});
  if (!uuid(info.upload) || info.expires < Date.now()) throw new ApiError(403, 'Backup upload authorization expired');
  const previous = checkRevision(await find(pool, id), info);
  if (previous) return out({ backup: summary(previous) });
  const object = `owner-backups/${id}/${info.upload}.bin`;
  const stage = `owner-backup-staging/${id}/${info.upload}.bin`;
  const stored = await storage.bytes(stage, info.size + 28);
  let raw;
  try { raw = decrypt(objectCipherKey(key, object), id + ':' + info.digest, stored); }
  catch { throw new ApiError(400, 'Uploaded backup could not be authenticated'); }
  if (!matches(raw, info.size, info.digest)) throw new ApiError(400, 'Uploaded backup checksum does not match');
  const row = await commit(pool, id, info, object, 2, () => storage.putBackup(object, stored));
  if (storage.removeBackupStage) {
    try { await storage.removeBackupStage(stage); } catch {}
  }
  return out({ backup: summary(row) });
}

async function canPresign(pool, id, row, storage, key) {
  if (row.cipher_format === 2) return row;
  const stored = await storage.bytes(row.object_key, BACKUP_LIMIT + 28);
  const scope = id + ':' + row.sha256;
  const raw = decrypt(cryptKey(key), scope, stored);
  if (!matches(raw, row.size, row.sha256)) throw new ApiError(503, 'Backup verification failed');
  const object = `owner-backups/${id}/${randomUUID()}.bin`;
  return withTransaction(pool, async db => {
    const locked = await db.query('SELECT backup_enabled FROM owner_workspaces WHERE id=$1 FOR UPDATE', [id]);
    if (!locked.rows[0]?.backup_enabled) throw new ApiError(403, 'Company backup is not enabled');
    const current = await find(db, id);
    if (current?.cipher_format === 2) return current;
    if (current?.object_key !== row.object_key) throw new ApiError(409, 'The workspace backup has changed');
    await storage.putBackup(object, encrypt(objectCipherKey(key, object), scope, raw));
    return (await db.query(`UPDATE owner_backups SET object_key=$1,cipher_format=2
      WHERE workspace_id=$2 RETURNING ${columns}`, [object, id])).rows[0];
  });
}

export async function ownerBackup(request, path, { pool, workspace, storage, encryptionKey, direct = false, maxSize }) {
  if (!workspace.backup_enabled) throw new ApiError(403, 'Company backup is not enabled');
  const id = workspace.id;
  const limit = /^[1-9]\d*$/.test(maxSize || '') ? Math.min(Number(maxSize), BACKUP_LIMIT) : BACKUP_LIMIT;
  if (request.method === 'POST' && path === '/owner/backup/prepare' && storage.authorizeBackup) {
    return prepare(request, id, pool, storage, encryptionKey, limit);
  }
  if (request.method === 'POST' && path === '/owner/backup/finish' && storage.authorizeBackup) {
    return finish(request, id, pool, storage, encryptionKey);
  }
  if (request.method === 'GET') {
    const row = await find(pool, id);
    if (path === '/owner/backup') return out({ backup: summary(row) || null, direct, max_size: limit });
    if (!row) throw new ApiError(404, 'No company backup is available');
    if (path === '/owner/backup/grant' && storage.download) {
      const signed = await canPresign(pool, id, row, storage, encryptionKey);
      return out({ ...summary(signed), download_url: await storage.download(signed.object_key),
        encryption: objectCipherKey(encryptionKey, signed.object_key).toString('base64') });
    }
    if (path !== '/owner/backup/download' || direct) {
      throw new ApiError(409, 'This service requires a direct backup download');
    }
    const stored = await storage.bytes(row.object_key, BACKUP_LIMIT + 28);
    const key = row.cipher_format === 2 ? objectCipherKey(encryptionKey, row.object_key) : cryptKey(encryptionKey);
    const raw = decrypt(key, id + ':' + row.sha256, stored);
    if (!matches(raw, row.size, row.sha256)) throw new ApiError(503, 'Backup verification failed');
    return new Response(raw, { headers: { ...jsonHeaders, 'Content-Type': 'application/zip', 'Content-Length': String(raw.length),
      'X-Backup-Sha256': row.sha256, 'X-Backup-Revision': String(row.revision) } });
  }
  if (path !== '/owner/backup' || request.method !== 'PUT' || direct) throw new ApiError(405, 'Unsupported backup action');
  const device = request.headers.get('x-backup-device');
  const digest = request.headers.get('x-backup-sha256');
  const size = Number(request.headers.get('content-length'));
  const revision = Number(request.headers.get('x-backup-revision'));
  if (!storage.putBackup || !encryptionKey) throw new ApiError(503, 'Private backup storage is unavailable');
  checkBackupInfo({ device, digest, size, revision });
  if (size > limit) throw new ApiError(413, 'Backup exceeds the configured storage limit');
  if (!request.body) throw new ApiError(400, 'Invalid backup request');
  const payload = await exactBackup(request, size, digest);
  const objectKey = `owner-backups/${id}/${randomUUID()}.bin`;
  const row = await commit(pool, id, { device, digest, size, revision }, objectKey, 1,
    () => storage.putBackup(objectKey, encrypt(cryptKey(encryptionKey), id + ':' + digest, payload)));
  return out({ backup: summary(row) });
}
