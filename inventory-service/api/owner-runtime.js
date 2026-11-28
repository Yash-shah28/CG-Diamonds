import { ownerAuth } from '../owner/auth.js';
import { workspacePool } from '../owner/database.js';
import { privateStorage } from '../owner/storage.js';
import { ownerWeb } from '../owner/web.js';

let runtime;
const ownerRoutes = /^\/(?:owner\/[a-z0-9/-]+|platform\/owner|get-app(?:\.js)?|healthz)$/;

export function ownerRequest(request, origin) {
  const address = new URL(request.url);
  const routes = address.searchParams.getAll('__owner_path');
  const path = routes.length === 1 ? routes[0] : address.pathname;
  if (routes.length > 1 || !ownerRoutes.test(path)) return null;
  address.searchParams.delete('__owner_path');
  const target = new URL(path, origin);
  target.search = address.search;
  return new Request(target, request);
}

async function initialize() {
  const env = { ...process.env, WORKSPACE_DIRECT_BACKUPS: 'true', WORKSPACE_PORTABLE_BUYER_API: 'false' };
  const origin = new URL(env.WORKSPACE_ORIGIN);
  if (origin.protocol !== 'https:' || origin.origin !== env.WORKSPACE_ORIGIN || !env.DATABASE_URL) {
    throw new Error('Owner deployment is not configured');
  }
  const pool = workspacePool(env.DATABASE_URL, { max: 2, sslCA: env.DATABASE_SSL_CA });
  const auth = ownerAuth(pool, env);
  const storage = privateStorage(env);
  const fetchOwner = await ownerWeb({ pool, auth, storage, env, async ready() {
    await Promise.all([pool.query('SELECT 1 FROM owner_backups LIMIT 1'), storage.ready()]);
  } });
  return { origin, fetchOwner };
}

export default {
  async fetch(request) {
    try {
      runtime ||= initialize().catch(error => { runtime = undefined; throw error; });
      const { origin, fetchOwner } = await runtime;
      const owned = ownerRequest(request, origin);
      if (!owned) return new Response(null, { status: 404 });
      return await fetchOwner(owned, request.headers.get('x-vercel-forwarded-for')?.split(',')[0].trim() || 'unknown');
    } catch {
      return Response.json({ ok: false, error: 'Owner service is temporarily unavailable' }, {
        status: 503, headers: { 'Cache-Control': 'no-store' },
      });
    }
  },
};
