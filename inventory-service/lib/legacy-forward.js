import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const paths = new Set(['/api/v1/inventory', '/api/v1/feedback', '/api/latest', '/api/download']);
const requestHeaders = ['x-api-key', 'authorization', 'content-type', 'user-agent', 'origin', 'access-control-request-method', 'access-control-request-headers'];
const exposedHeaders = ['content-type', 'allow', 'cache-control', 'x-content-type-options',
  'access-control-allow-origin', 'access-control-allow-methods', 'access-control-allow-headers', 'access-control-expose-headers',
  'x-inventory-records', 'x-inventory-snapshot', 'x-inventory-published-at', 'x-request-id', 'location'];

export async function forwardLegacyRequest(req, res, path, env = process.env, fetcher = fetch) {
  const configured = env.OWNER_BUYER_FORWARD_ORIGIN;
  if (!configured) return false;
  try {
    const origin = new URL(configured);
    if (origin.protocol !== 'https:' || configured !== origin.origin || origin.username || origin.password || !paths.has(path)) {
      throw new Error('Invalid legacy route destination');
    }
    const headers = new Headers();
    for (const key of requestHeaders) if (typeof req.headers[key] === 'string') headers.set(key, req.headers[key]);
    let body;
    if (req.method === 'POST') {
      body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body || {});
      if (Buffer.byteLength(body) > 524288) {
        res.status(413).json({ ok: false, error: 'Feedback payload is too large' });
        return true;
      }
    }
    const response = await fetcher(origin.origin + path, {
      method: req.method, headers, body, redirect: 'manual', signal: AbortSignal.timeout(55000),
    });
    if (path.startsWith('/api/v1/') && response.status >= 300 && response.status < 400) {
      throw new Error('Buyer APIs must be forwarded without redirects');
    }
    for (const key of exposedHeaders) if (response.headers.has(key)) res.setHeader(key, response.headers.get(key));
    res.status(response.status);
    if (response.body && req.method !== 'HEAD') await pipeline(Readable.fromWeb(response.body), res);
    else res.end();
  } catch {
    if (res.headersSent) res.destroy();
    else res.status(503).json({ ok: false, error: 'Inventory service temporarily unavailable' });
  }
  return true;
}
