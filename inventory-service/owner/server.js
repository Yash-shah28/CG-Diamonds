import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { ownerAuth, migrateDatabase } from './auth.js';
import { workspacePool } from './database.js';
import { privateStorage } from './storage.js';
import { ownerWeb } from './web.js';

const env = process.env;
if (!env.DATABASE_URL) throw new Error('DATABASE_URL is required');
const origin = new URL(env.WORKSPACE_ORIGIN);
if ((origin.protocol !== 'https:' && !(origin.protocol === 'http:' && origin.hostname === '127.0.0.1')) ||
    origin.origin !== env.WORKSPACE_ORIGIN) throw new Error('Set WORKSPACE_ORIGIN to the service origin');
const pool = workspacePool(env.DATABASE_URL, { sslCA: env.DATABASE_SSL_CA });
const auth = ownerAuth(pool, env);
if (process.argv.includes('--migrate')) {
  try { await migrateDatabase(pool, auth); }
  finally { await pool.end(); }
} else {
  await pool.query('SELECT 1 FROM owner_backups LIMIT 1');
  const storage = privateStorage(env);
  await storage.ready();
  const handle = await ownerWeb({ pool, auth, storage, env });
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, origin);
      const body = ['GET', 'HEAD'].includes(req.method) ? undefined : Readable.toWeb(req);
      const request = new Request(new URL(url.pathname + url.search, origin), {
        method: req.method, headers: req.headers, body, ...(body ? { duplex: 'half' } : {}),
      });
      const ip = env.WORKSPACE_TRUST_PROXY_IP === 'true' ? String(req.headers['x-real-ip'] || req.socket.remoteAddress) : req.socket.remoteAddress;
      const response = await handle(request, ip);
      res.writeHead(response.status, Object.fromEntries(response.headers));
      if (response.body) await pipeline(Readable.fromWeb(response.body), res);
      else res.end();
    } catch {
      if (res.headersSent) res.destroy();
      else { res.writeHead(500); res.end(); }
    }
  });
  server.listen(Number(env.PORT || 8080), env.WORKSPACE_BIND || '127.0.0.1');
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => pool.end()));
}
