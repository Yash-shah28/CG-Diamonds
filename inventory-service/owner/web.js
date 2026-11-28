import { readFile } from 'node:fs/promises';
import { credentialKey } from './security.js';
import { workspaceHandler } from './routes.js';

export async function ownerWeb({ pool, auth, storage, env, ready }) {
  const [publicKey, page, browserScript] = await Promise.all([
    readFile(new URL('./update-public-key.pem', import.meta.url)),
    readFile(new URL('./download.html', import.meta.url)),
    readFile(new URL('./download.js', import.meta.url)),
  ]);
  const dispatch = workspaceHandler({
    pool, auth, storage, publicKey, encryptionKey: credentialKey(env.WORKSPACE_CREDENTIAL_KEY), env,
  });
  return async function fetchOwner(request, ip) {
    const path = new URL(request.url).pathname;
    if (request.method === 'GET' && path === '/healthz') {
      if (ready) await ready();
      return Response.json({ status: 'ready' }, { headers: { 'Cache-Control': 'no-store' } });
    }
    if (request.method === 'GET' && (path === '/get-app' || path === '/get-app.js')) {
      const javascript = path.endsWith('.js');
      return new Response(javascript ? browserScript : page, { headers: {
        'Content-Type': javascript ? 'text/javascript; charset=utf-8' : 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
      } });
    }
    return dispatch(request, ip);
  };
}
