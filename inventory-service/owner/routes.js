import { createHash, randomUUID, verify } from 'node:crypto';
import { ApiError, createService } from '../lib/inventory-service.js';
import { changeOwnerPassword, createWorkspace, features, issueSession, limitLogin, platformReset, withTransaction, workspaceFor } from './auth.js';
import { integrations } from './integrations.js';
import { ownerBackup } from './backup.js';
import { buyerId, compareVersions, credentialsFor, FEATURES, hash, keyHash, PROVIDERS, requireAdmin, seal, unseal, uuid, validEmail, validVersion, verifyPrivateManifest } from './security.js';

const legacyActions = new Set(['status', 'authorize', 'commit', 'name', 'feedback', 'revoke', 'suspend']);
const kinds = ['Natural', 'Lab Grown'];
const scopeFeature = kind => kind === 'Natural' ? 'natural' : 'lab_grown';
const JSON_LIMIT = 4 * 1024 * 1024;

function out(data, status = 200) {
  return Response.json(data, { status, headers: { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' } });
}

async function input(request, limit = JSON_LIMIT) {
  if (!request.body || Number(request.headers.get('content-length') || 0) > limit) throw new ApiError(400, 'Invalid request body');
  let bytes = 0;
  const chunks = [];
  for await (const chunk of request.body) {
    bytes += chunk.length;
    if (bytes > limit) throw new ApiError(413, 'Request is too large');
    chunks.push(chunk);
  }
  try {
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (data && typeof data === 'object' && !Array.isArray(data)) return data;
  } catch {}
  throw new ApiError(400, 'Invalid JSON request');
}

function passes(workspace, feature) {
  if (!workspace.features.includes(feature)) throw new ApiError(403, 'This feature is disabled for your company');
}

function safeProfiles(profiles, workspace) {
  if (!Array.isArray(profiles) || profiles.length > 2) throw new ApiError(400, 'Invalid inventory profiles');
  const unique = new Set();
  return profiles.map(profile => {
    if (!profile || !kinds.includes(profile.kind) || unique.has(profile.kind)
        || typeof profile.id !== 'string' || typeof profile.path !== 'string' || typeof profile.sheet !== 'string'
        || typeof profile.mapping !== 'object' || !profile.mapping || Array.isArray(profile.mapping)) {
      throw new ApiError(400, 'Invalid inventory profile');
    }
    passes(workspace, scopeFeature(profile.kind));
    unique.add(profile.kind);
    const { id, kind, path, sheet, mapping, enabled, required, numeric, decimal_fields, string_fields, identity, request_format } = profile;
    return { id, kind, path, sheet, mapping, enabled: enabled !== false, required, numeric, decimal_fields, string_fields, identity, request_format };
  });
}

function safeBuyer(value, feedUrl) {
  if (!value || !buyerId(value.id) || !keyHash(value.key_hash) || typeof value.name !== 'string' || !value.name.trim()
      || !Array.isArray(value.kinds) || !value.kinds.length || value.kinds.some(kind => !kinds.includes(kind))
      || !['increase', 'discount'].includes(value.mode) || typeof value.percent !== 'string'
      || !Number.isFinite(Number(value.percent)) || Number(value.percent) < 0 || Number(value.percent) > 100
      || (value.email && !validEmail(value.email))) throw new ApiError(400, 'Invalid buyer settings');
  return { name: value.name.slice(0, 120), email: value.email || '', kinds: [...new Set(value.kinds)], mode: value.mode,
    percent: value.percent, active: value.active !== false && value.active !== 0, feed_url: feedUrl };
}

export function legacyBridge(env, storage, fetcher = fetch) {
  const base = env.LEGACY_PUBLIC_ORIGIN || 'https://chintamani-updates.vercel.app';
  const origin = new URL(base);
  if (origin.protocol !== 'https:' || origin.origin !== base || origin.username || origin.password) throw new Error('Invalid LEGACY_PUBLIC_ORIGIN');
  const feedUrl = base + '/api/v1/inventory';
  let portable;
  if (env.WORKSPACE_PORTABLE_BUYER_API === 'true') {
    if (!env.LEGACY_BRIDGE_TOKEN) throw new Error('LEGACY_BRIDGE_TOKEN is required for the portable buyer API');
    portable = createService(storage, hash(env.LEGACY_BRIDGE_TOKEN));
  }
  return {
    feedUrl,
    portable,
    async admin(body) {
      if (!env.LEGACY_BRIDGE_TOKEN) throw new ApiError(503, 'Buyer publishing has not been configured');
      if (portable) {
        const result = await portable.admin('Bearer ' + env.LEGACY_BRIDGE_TOKEN, body);
        return result.upload_url ? { ...result, upload_if_absent: true } : result;
      }
      let response;
      try {
        response = await fetcher(base + '/api/v1/admin', {
          method: body ? 'POST' : 'GET', redirect: 'error', signal: AbortSignal.timeout(20000),
          headers: { Authorization: 'Bearer ' + env.LEGACY_BRIDGE_TOKEN, 'Content-Type': 'application/json' },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
      } catch { throw new ApiError(503, 'Buyer hosting is temporarily unavailable'); }
      const data = await response.json();
      if (!response.ok || data.ok !== true) {
        if ([400, 404, 409].includes(response.status)) throw new ApiError(response.status, 'Buyer publishing request could not be applied');
        throw new ApiError(503, 'Buyer hosting could not verify the request');
      }
      return data;
    },
  };
}

async function ownerBuyer(pool, workspace, id) {
  if (!buyerId(id)) throw new ApiError(400, 'Invalid buyer ID');
  const { rows } = await pool.query('SELECT * FROM owner_buyers WHERE id=$1 AND workspace_id=$2', [id, workspace.id]);
  if (!rows[0]) throw new ApiError(404, 'Buyer not found');
  return rows[0];
}

async function ownerMigration(pool, workspace, data, encryptionKey, legacy) {
  if (!uuid(data.migration_id) || !Array.isArray(data.buyers) || data.buyers.length > 2000
      || !data.credentials || typeof data.credentials !== 'object' || Array.isArray(data.credentials)) throw new ApiError(400, 'Invalid migration package');
  if (workspace.migration_id === data.migration_id) return { migrated: true };
  if (workspace.migration_id) throw new ApiError(409, 'This workspace has already been migrated');
  const profiles = safeProfiles(data.profiles, workspace);
  const buyers = [];
  if (data.buyers.length) {
    passes(workspace, 'buyer_feeds');
    if (!workspace.legacy_import) throw new ApiError(403, 'Legacy import is unavailable for this workspace');
  }
  for (const buyer of data.buyers) {
    const settings = safeBuyer(buyer, legacy.feedUrl);
    for (const kind of settings.kinds) passes(workspace, scopeFeature(kind));
    const observed = await legacy.admin({ action: 'status', client_id: buyer.id });
    if (observed.key_hash && observed.key_hash !== buyer.key_hash) throw new ApiError(409, 'A buyer key differs from its live record. Resolve the pending local change before importing.');
    if (!observed.key_hash && (buyer.published > 0 || buyer.revision > 0)) throw new ApiError(409, 'A previously published buyer could not be verified');
    if (observed.key_hash && observed.enabled !== settings.active) throw new ApiError(409, 'A buyer access change is still pending');
    buyers.push({ ...buyer, settings: { ...settings, verified_revision: observed.revision || 0, records: observed.records || 0 } });
  }
  const providers = Object.entries(data.credentials).map(([provider, values]) => {
    if (!Object.hasOwn(PROVIDERS, provider)) throw new ApiError(400, 'Invalid credential provider');
    passes(workspace, provider);
    return [provider, seal(encryptionKey, workspace.id, provider, credentialsFor(provider, values))];
  });
  await withTransaction(pool, async db => {
    const current = await db.query('SELECT migration_id FROM owner_workspaces WHERE id=$1 FOR UPDATE', [workspace.id]);
    if (current.rows[0].migration_id === data.migration_id) return;
    if (current.rows[0].migration_id) throw new ApiError(409, 'This workspace has already been migrated');
    for (const buyer of buyers) {
      try {
        await db.query('INSERT INTO owner_buyers(id,workspace_id,key_hash,settings) VALUES($1,$2,$3,$4)', [buyer.id, workspace.id, buyer.key_hash, buyer.settings]);
      } catch (error) {
        if (error.code === '23505') throw new ApiError(409, 'A buyer is already assigned');
        throw error;
      }
    }
    for (const [provider, encrypted] of providers) {
      await db.query('INSERT INTO owner_credentials(workspace_id,provider,encrypted) VALUES($1,$2,$3) ON CONFLICT(workspace_id,provider) DO NOTHING', [workspace.id, provider, encrypted]);
    }
    await db.query('UPDATE owner_workspaces SET migration_id=$1,settings=$2 WHERE id=$3',
      [data.migration_id, { profiles }, workspace.id]);
  });
  return { migrated: true };
}

async function adminCommand(pool, auth, storage, publicKey, data) {
  const id = data.workspace_id;
  if (data.action === 'create-workspace') return createWorkspace(pool, auth, data);
  if (data.action === 'register-release') {
    const manifest = verifyPrivateManifest(data.envelope, publicKey);
    const stream = await storage.stream(manifest.object_key);
    const checksum = createHash('sha256');
    let size = 0;
    for await (const chunk of stream) {
      size += chunk.length;
      if (size > manifest.size) throw new ApiError(400, 'Private installer size does not match');
      checksum.update(chunk);
    }
    if (size !== manifest.size || checksum.digest('hex') !== manifest.sha256) throw new ApiError(400, 'Private installer does not match');
    const { rowCount } = await pool.query('INSERT INTO owner_releases(version,object_key,envelope) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
      [manifest.version, manifest.object_key, data.envelope]);
    if (!rowCount) throw new ApiError(409, 'An installer is already registered for this version');
    return { version: manifest.version };
  }
  if (!uuid(id)) throw new ApiError(400, 'Invalid company ID');
  if (data.action === 'reset-password') return platformReset(pool, auth, id);
  return withTransaction(pool, async db => {
    const selected = await db.query('SELECT * FROM owner_workspaces WHERE id=$1 FOR UPDATE', [id]);
    if (!selected.rows[0]) throw new ApiError(404, 'Company not found');
    if (data.action === 'set-features') {
      await db.query('UPDATE owner_workspaces SET features=$1 WHERE id=$2', [features(data.features), id]);
    } else if (data.action === 'set-backup') {
      if (typeof data.enabled !== 'boolean') throw new ApiError(400, 'Set whether company backup is enabled');
      await db.query('UPDATE owner_workspaces SET backup_enabled=$1 WHERE id=$2', [data.enabled, id]);
    } else if (data.action === 'transfer-backup') {
      if (!uuid(data.device_id)) throw new ApiError(400, 'Invalid backup device ID');
      if (!(await db.query('UPDATE owner_backups SET device_id=$1 WHERE workspace_id=$2', [data.device_id, id])).rowCount) {
        throw new ApiError(404, 'No backup exists to hand off');
      }
    } else if (data.action === 'assign-release') {
      if (!validVersion(data.version) || !(await db.query('SELECT 1 FROM owner_releases WHERE version=$1', [data.version])).rowCount) {
        throw new ApiError(404, 'Release not found');
      }
      await db.query('INSERT INTO owner_release_assignments(workspace_id,version) VALUES($1,$2) ON CONFLICT DO NOTHING', [id, data.version]);
    } else if (data.action === 'unassign-release') {
      await db.query('DELETE FROM owner_release_assignments WHERE workspace_id=$1 AND version=$2', [id, data.version]);
    } else if (data.action === 'set-publishing') {
      if (typeof data.enabled !== 'boolean' || (data.enabled && (!selected.rows[0].migration_id && selected.rows[0].legacy_import))) {
        throw new ApiError(409, 'Finish the legacy migration before enabling publishing');
      }
      await db.query('UPDATE owner_workspaces SET publishing_enabled=$1 WHERE id=$2', [data.enabled, id]);
    } else throw new ApiError(400, 'Unknown platform administration action');
    await db.query('INSERT INTO owner_admin_history(workspace_id,action) VALUES($1,$2)', [id, data.action]);
    return { updated: true };
  });
}

async function buyerApi(request, portable, feedbackOnly = false) {
  if (!portable) throw new ApiError(503, 'Portable inventory has not been activated');
  const origin = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, x-api-key, Authorization', 'Cache-Control': 'private, no-store',
    'Access-Control-Expose-Headers': 'X-Inventory-Records, X-Inventory-Snapshot, X-Inventory-Published-At, X-Request-ID',
    'X-Request-ID': randomUUID() };
  const key = request.headers.get('x-api-key') || request.headers.get('authorization')?.match(/^Bearer (\S+)$/)?.[1];
  try {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: origin });
    if (request.method === 'POST') return Response.json(await portable.feedback(key, await input(request, 524288)), { headers: origin });
    if (request.method !== 'GET' || feedbackOnly) return new Response(null, { status: 405, headers: origin });
    const result = await portable.inventory(key);
    const original = await new Response(result.stream).json();
    const split = Array.isArray(original.diamonds) ? {
      natural: original.diamonds.filter(row => row['Diamond Type'] !== 'LAB_GROWN'),
      lab_grown: original.diamonds.filter(row => row['Diamond Type'] === 'LAB_GROWN'),
    } : { natural: original.natural || [], lab_grown: original.lab_grown || [] };
    await portable.recordAccess(result.client_id, {
      company_name: result.company_name, naturalCount: split.natural.length, labGrownCount: split.lab_grown.length,
      totalCount: result.records, userAgent: (request.headers.get('user-agent') || '').slice(0, 100),
    }).catch(() => {});
    return Response.json(split, { headers: { ...origin, 'X-Inventory-Records': String(result.records),
      'X-Inventory-Snapshot': result.snapshot_id, 'X-Inventory-Published-At': result.published_at } });
  } catch (error) {
    return Response.json({ ok: false, error: error instanceof ApiError ? error.message : 'Inventory is temporarily unavailable' },
      { status: error instanceof ApiError ? error.status : 503, headers: origin });
  }
}

export function workspaceHandler({ pool, auth, storage, encryptionKey, publicKey, env = process.env, fetcher = fetch, transportFactory }) {
  const legacy = legacyBridge(env, storage, fetcher);
  const dispatch = integrations({ pool, storage, encryptionKey, fetcher, transportFactory });
  return async function handle(request, remoteIp = 'unknown') {
    try {
      const url = new URL(request.url), path = url.pathname, method = request.method;
      if (['/api/v1/inventory', '/api/v1/feedback'].includes(path)) return await buyerApi(request, legacy.portable, path.endsWith('/feedback'));
      if (['/api/latest', '/api/download'].includes(path) && ['GET', 'HEAD'].includes(method)) {
        if (!legacy.portable) throw new ApiError(503, 'Portable inventory is unavailable');
        const stored = await storage.json('channels/stable.json');
        if (!stored) return new Response(null, { status: 404 });
        const envelope = stored.value;
        const payload = Buffer.from(envelope.payload, 'base64');
        if (payload.length > 32000 || !verify(null, payload, publicKey, Buffer.from(envelope.signature, 'base64'))) {
          throw new ApiError(503, 'Public release could not be verified');
        }
        const release = JSON.parse(payload);
        if (release.schema !== 1 || release.app !== 'chintamani-gems' || !validVersion(release.version)) {
          throw new ApiError(503, 'Public release could not be verified');
        }
        if (path.endsWith('/latest')) return out(envelope);
        const address = new URL(release.url);
        if (address.protocol !== 'https:' || address.username || address.password) throw new ApiError(503, 'Public release is unavailable');
        return new Response(null, { status: 302, headers: { Location: address.href, 'Cache-Control': 'private, no-store' } });
      }
      if (path === '/owner/sign-in' && method === 'POST') {
        const data = await input(request, 4096);
        const login = typeof data.login === 'string' ? data.login.trim().toLowerCase() : '';
        await limitLogin(pool, login, remoteIp);
        const token = await issueSession(auth, login, data.password);
        const headers = new Headers({ Authorization: 'Bearer ' + token });
        const { workspace } = await workspaceFor(pool, auth, headers, true);
        return out({ token, must_change_password: workspace.must_change_password });
      }
      if (path === '/platform/owner' && method === 'POST') {
        requireAdmin(request.headers.get('authorization'), env.WORKSPACE_ADMIN_TOKEN_SHA256);
        return out(await adminCommand(pool, auth, storage, publicKey, await input(request)));
      }
      if (!path.startsWith('/owner/')) throw new ApiError(404, 'Not found');
      const { workspace, user } = await workspaceFor(pool, auth, request.headers, ['/owner/password', '/owner/sign-out'].includes(path));
      if (path === '/owner/sign-out' && method === 'POST') {
        await auth.api.signOut({ headers: request.headers });
        return out({ signed_out: true });
      }
      if (path === '/owner/password' && method === 'POST') return out(await changeOwnerPassword(pool, auth, request.headers, workspace, user, await input(request, 4096)));
      if (path === '/owner/bootstrap' && method === 'GET') {
        const { rows } = await pool.query('SELECT provider,encrypted FROM owner_credentials WHERE workspace_id=$1', [workspace.id]);
        const providerRefs = {};
        const rapaport = rows.find(row => row.provider === 'rapaport');
        if (rapaport && workspace.features.includes('rapaport')) {
          try {
            providerRefs.rapaport = hash(unseal(encryptionKey, workspace.id, 'rapaport', rapaport.encrypted).client_id);
          } catch {} // An unavailable provider credential should not prevent owner sign-in.
        }
        const buyers = workspace.features.includes('buyer_feeds')
          ? (await pool.query('SELECT id,key_hash,settings FROM owner_buyers WHERE workspace_id=$1 ORDER BY id', [workspace.id])).rows : [];
        return out({ id: workspace.id, name: workspace.name, slug: workspace.slug, features: workspace.features.filter(feature => FEATURES.includes(feature)),
          providers: rows.map(row => row.provider), migrated: Boolean(workspace.migration_id),
          publishing_enabled: workspace.publishing_enabled, feed_url: legacy.feedUrl, settings: workspace.settings, buyers,
          backup_enabled: workspace.backup_enabled, legacy_import: workspace.legacy_import, provider_refs: providerRefs });
      }
      if (path === '/owner/backup' || /^\/owner\/backup\/(download|prepare|finish|grant)$/.test(path)) {
        return await ownerBackup(request, path, { pool, workspace, storage, encryptionKey,
          direct: env.WORKSPACE_DIRECT_BACKUPS === 'true', maxSize: env.WORKSPACE_BACKUP_MAX_BYTES });
      }
      if (path === '/owner/migrate' && method === 'POST') return out(await ownerMigration(pool, workspace, await input(request), encryptionKey, legacy));
      if (path === '/owner/settings' && method === 'PUT') {
        const data = await input(request);
        await pool.query("UPDATE owner_workspaces SET settings=jsonb_set(settings,'{profiles}',$2::jsonb) WHERE id=$1",
          [workspace.id, JSON.stringify(safeProfiles(data.profiles, workspace))]);
        return out({ saved: true });
      }
      const credential = path.match(/^\/owner\/credentials\/(hrd|rapaport|email)$/);
      if (credential && method === 'PUT') {
        const provider = credential[1];
        passes(workspace, provider);
        const encrypted = seal(encryptionKey, workspace.id, provider, credentialsFor(provider, await input(request, 10000)));
        await pool.query('INSERT INTO owner_credentials(workspace_id,provider,encrypted) VALUES($1,$2,$3) ON CONFLICT(workspace_id,provider) DO UPDATE SET encrypted=EXCLUDED.encrypted',
          [workspace.id, provider, encrypted]);
        return out({ saved: true });
      }
      if (path === '/owner/integrations' && method === 'POST') return out(await dispatch(workspace, await input(request)));
      if (path === '/owner/releases' && method === 'GET') {
        const current = url.searchParams.get('current') || '0.0.0';
        if (!validVersion(current)) throw new ApiError(400, 'Invalid installed version');
        const { rows } = await pool.query('SELECT r.* FROM owner_releases r JOIN owner_release_assignments a USING(version) WHERE a.workspace_id=$1', [workspace.id]);
        const release = rows.filter(row => compareVersions(row.version, current) > 0).sort((a, b) => compareVersions(b.version, a.version))[0];
        if (!release) return new Response(null, { status: 204, headers: { 'Cache-Control': 'private, no-store' } });
        return out({ envelope: release.envelope, download_url: await storage.download(release.object_key) });
      }
      if (path === '/owner/buyers' && method === 'PUT') {
        passes(workspace, 'buyer_feeds');
        if (!workspace.publishing_enabled) throw new ApiError(409, 'Company publishing has not been activated');
        const data = await input(request);
        const settings = safeBuyer(data, legacy.feedUrl);
        for (const kind of settings.kinds) passes(workspace, scopeFeature(kind));
        const inserted = await pool.query(`
          INSERT INTO owner_buyers(id,workspace_id,key_hash,settings) VALUES($1,$2,$3,$4)
          ON CONFLICT(id) DO UPDATE SET key_hash=EXCLUDED.key_hash,settings=EXCLUDED.settings
          WHERE owner_buyers.workspace_id=EXCLUDED.workspace_id
          AND (owner_buyers.key_hash=EXCLUDED.key_hash OR $5=owner_buyers.key_hash)`,
          [data.id, workspace.id, data.key_hash, settings, data.previous_key_hash || null]);
        if (!inserted.rowCount) throw new ApiError(409, 'Buyer already assigned or key changed');
        return out({ saved: true });
      }
      if (path === '/owner/inventory-admin' && ['POST', 'GET'].includes(method)) {
        passes(workspace, 'buyer_feeds');
        if (method === 'GET') return out({ ok: true, mode: 'owner-scoped', enabled: workspace.publishing_enabled });
        const data = await input(request);
        if (data.action === 'auth-audit') {
          if (legacy.portable) return out({ ok: true, entries: [], next_cursor: null });
          const page = await legacy.admin(data);
          const { rows } = await pool.query('SELECT id FROM owner_buyers WHERE workspace_id=$1', [workspace.id]);
          const allowed = new Set(rows.map(row => row.id));
          return out({ ok: true, next_cursor: page.next_cursor,
            entries: (page.entries || []).filter(event => allowed.has(event.client_id)) });
        }
        if (!legacyActions.has(data.action)) throw new ApiError(400, 'Unknown inventory action');
        const buyer = await ownerBuyer(pool, workspace, data.client_id);
        if (!['status', 'feedback'].includes(data.action) && !workspace.publishing_enabled) throw new ApiError(409, 'Company publishing has not been activated');
        if (['authorize', 'commit'].includes(data.action)) {
          if (data.key_hash !== buyer.key_hash || !buyer.settings.active || !Array.isArray(data.kinds)
              || data.kinds.length !== buyer.settings.kinds.length || data.kinds.some(kind => !buyer.settings.kinds.includes(kind))) {
            throw new ApiError(403, 'Buyer key or inventory scope does not match its workspace');
          }
          for (const kind of data.kinds) passes(workspace, scopeFeature(kind));
        }
        const result = await legacy.admin(data);
        if (data.action === 'commit') {
          await pool.query(`UPDATE owner_buyers SET settings=settings || $3::jsonb WHERE id=$1 AND workspace_id=$2
            AND COALESCE((settings->>'verified_revision')::integer, 0) <= $4`,
            [buyer.id, workspace.id, JSON.stringify({ verified_revision: data.revision, records: data.records }), data.revision]);
        }
        return out(result);
      }
      throw new ApiError(404, 'Not found');
    } catch (error) {
      const status = error instanceof ApiError ? error.status : error.code === '23505' ? 409 : 500;
      return out({ ok: false, error: error instanceof ApiError ? error.message :
        status === 409 ? 'This resource is already assigned' : 'Workspace service could not complete the request' }, status);
    }
  };
}
