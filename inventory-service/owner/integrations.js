import nodemailer from 'nodemailer';
import { ApiError, MAX_SNAPSHOT } from '../lib/inventory-service.js';
import { credentialsFor, hash, unseal, uuid, validEmail } from './security.js';

const RAPAPORT_TOKEN = 'https://authztoken.api.rapaport.com/api/get';
const RAPAPORT_LOTS = 'https://technet.rapnetapis.com/diamondupdateingest/api/public/lots';

async function upstream(fetcher, url, options, service) {
  let response;
  try {
    response = await fetcher(url, { redirect: 'error', signal: AbortSignal.timeout(45000), ...options });
  } catch {
    throw new ApiError(502, `${service} did not confirm its response`);
  }
  if (!response.ok) {
    const failure = new ApiError(response.status === 429 ? 429 : 502, `${service} responded with HTTP ${response.status}`);
    failure.definiteRejection = response.status >= 400 && response.status < 500;
    throw failure;
  }
  return response;
}

async function json(response) {
  try {
    const value = await response.json();
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  } catch {}
  throw new ApiError(502, 'Provider returned an unreadable response');
}

async function rapaportHeaders(fetcher, credentials) {
  const result = await json(await upstream(fetcher, RAPAPORT_TOKEN, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(credentials)
  }, 'Rapaport login'));
  if (typeof result.access_token !== 'string' || !/^\S+$/.test(result.access_token)) throw new ApiError(502, 'Rapaport returned an invalid login token');
  return { Authorization: 'Bearer ' + result.access_token, Accept: 'application/json' };
}

async function rapaportUpload(fetcher, headers, csv, replaceAll) {
  const filename = 'Natural-Inventory.csv';
  if (csv.length <= 5_000_000) {
    const form = new FormData();
    form.set('File', new Blob([csv], { type: 'text/csv' }), filename);
    form.set('DiamondFileFormat', 'Rapnet');
    form.set('ReplaceAll', String(replaceAll));
    form.set('FirstRowHeaders', 'true');
    form.set('SendEmail', 'false');
    return json(await upstream(fetcher, RAPAPORT_LOTS, { method: 'POST', headers, body: form }, 'Rapaport upload'));
  }
  const endpoint = RAPAPORT_LOTS + '/multipart';
  const contentHeaders = { ...headers, 'Content-Type': 'application/json' };
  const instructions = await json(await upstream(fetcher, endpoint, {
    method: 'POST', headers: contentHeaders, body: JSON.stringify({
      fileName: filename, replaceAll, sendEmail: false, diamondFileFormat: 'Rapnet', fileSize: csv.length,
    })
  }, 'Rapaport multipart preparation'));
  const { multipartSplitSize: split, multipartUploadId, uploadId, putUrls } = instructions;
  if (!uuid(uploadId) || !multipartUploadId || !Number.isSafeInteger(split) || split < 1 || !Array.isArray(putUrls)
      || putUrls.length !== Math.ceil(csv.length / split)) throw new ApiError(502, 'Invalid Rapaport multipart instructions');
  const parts = new Map();
  for (const address of putUrls) {
    const url = new URL(address);
    const part = Number(url.searchParams.get('partNumber'));
    if (url.protocol !== 'https:' || !url.hostname.endsWith('.amazonaws.com') || url.username || url.password || url.port || url.hash
        || !Number.isSafeInteger(part) || part < 1 || part > putUrls.length || parts.has(part)) {
      throw new ApiError(502, 'Untrusted Rapaport upload address');
    }
    parts.set(part, address);
  }
  const eTags = {};
  for (let part = 1; part <= parts.size; part++) {
    const response = await upstream(fetcher, parts.get(part), {
      method: 'PUT', headers: { 'Content-Type': 'text/csv' }, body: csv.subarray((part - 1) * split, part * split)
    }, 'Rapaport upload part');
    const tag = response.headers.get('etag');
    if (!tag) throw new ApiError(502, 'Rapaport did not confirm an upload part');
    eTags[part] = tag.replace(/^"|"$/g, '');
  }
  return json(await upstream(fetcher, endpoint, {
    method: 'PUT', headers: contentHeaders, body: JSON.stringify({ uploadId, multipartUploadId, eTags })
  }, 'Rapaport multipart completion'));
}

function requireFeature(workspace, provider) {
  if (!workspace.features.includes(provider) || (provider === 'rapaport' && !workspace.features.includes('natural'))) {
    throw new ApiError(403, 'This integration is disabled for your company');
  }
}

async function getCredentials(pool, encryptionKey, workspace, provider) {
  requireFeature(workspace, provider);
  const { rows } = await pool.query('SELECT encrypted FROM owner_credentials WHERE workspace_id=$1 AND provider=$2', [workspace.id, provider]);
  if (!rows[0]) throw new ApiError(409, 'Set up this integration before using it');
  return credentialsFor(provider, unseal(encryptionKey, workspace.id, provider, rows[0].encrypted));
}

async function sendEmail(credentials, payload, buyer, transportFactory) {
  if (payload.operation === 'test') {
    const transport = transportFactory(credentials);
    await transport.verify();
    return { connected: true };
  }
  if (!buyer || !validEmail(buyer.settings.email) || typeof payload.key !== 'string' || hash(payload.key) !== buyer.key_hash) {
    throw new ApiError(400, 'Email requires the key of a buyer in your workspace');
  }
  const feedUrl = buyer.settings.feed_url;
  const transport = transportFactory(credentials);
  const guide = typeof payload.guide === 'string' && payload.guide.length <= 2_000_000 ? Buffer.from(payload.guide, 'base64') : null;
  if (guide && !guide.subarray(0, 4).equals(Buffer.from('%PDF'))) throw new ApiError(400, 'Invalid inventory guide');
  await transport.sendMail({
    from: credentials.sender, to: buyer.settings.email,
    subject: 'Your inventory API access',
    ...(guide ? { attachments: [{ filename: 'Inventory API Guide.pdf', content: guide, contentType: 'application/pdf' }] } : {}),
    text: `Inventory URL: ${feedUrl}\nUse the x-api-key header with this key: ${payload.key}\n`,
  });
  return { sent: true };
}

export function integrations({ pool, storage, encryptionKey, fetcher = fetch, transportFactory = credentials => nodemailer.createTransport({
  host: 'smtp.mail.yahoo.com', port: 465, secure: true,
  auth: { user: credentials.sender, pass: credentials.password }, connectionTimeout: 12000, socketTimeout: 22000,
}) }) {
  async function dispatch(workspace, input) {
    const { provider, operation, request_id: requestId } = input;
    if (!uuid(requestId)) throw new ApiError(400, 'A request ID is required');
    const credentials = await getCredentials(pool, encryptionKey, workspace, provider);
    if (provider === 'hrd' && operation === 'lookup') {
      if (typeof input.report !== 'string' || !/^[A-Z0-9-]{3,40}$/.test(input.report) || !/\d/.test(input.report)) {
        throw new ApiError(400, 'Enter a valid HRD report number');
      }
      const response = await upstream(fetcher, 'https://my.hrdantwerp.com/api/public/GetReport/' + input.report, {
        headers: { 'API-Token': credentials.token, Accept: 'application/xml' },
      }, 'HRD lookup');
      const body = await response.text();
      if (body.length > 1_000_000 || !/^\s*(?:<\?xml[^>]*>\s*)?<Report[\s>]/.test(body)) throw new ApiError(502, 'HRD returned an invalid report');
      return { xml: body };
    }
    if (provider === 'rapaport' && operation === 'test') {
      await rapaportHeaders(fetcher, credentials);
      return { connected: true };
    }
    if (provider === 'rapaport' && operation === 'prepare') {
      if (!Number.isSafeInteger(input.size) || input.size < 1 || input.size > MAX_SNAPSHOT) throw new ApiError(400, 'Invalid upload size');
      return { upload_url: await storage.authorize(`workspace-uploads/${workspace.id}/${requestId}.csv`, input.size) };
    }
    if (provider === 'rapaport' && operation === 'status') {
      if (!uuid(input.upload_id)) throw new ApiError(400, 'Invalid upload ID');
      const { rowCount } = await pool.query("SELECT 1 FROM owner_jobs WHERE workspace_id=$1 AND provider='rapaport' AND result->>'uploadId'=$2", [workspace.id, input.upload_id]);
      if (!rowCount) throw new ApiError(404, 'Upload not found');
      const headers = await rapaportHeaders(fetcher, credentials);
      const result = await json(await upstream(fetcher, RAPAPORT_LOTS + '/status/' + input.upload_id, { headers }, 'Rapaport status'));
      return Object.fromEntries(['status', 'endTime', 'numLotReceived', 'numValidLots', 'numInvalidLots'].filter(key =>
        typeof result[key] === 'number' || (typeof result[key] === 'string' && result[key].length < 200)
      ).map(key => [key, result[key]]));
    }

    let perform;
    if (provider === 'rapaport' && ['upload', 'replace'].includes(operation)) {
      if (!Number.isSafeInteger(input.size) || input.size < 1 || input.size > MAX_SNAPSHOT || !/^[a-f0-9]{64}$/.test(input.sha256 || '')) {
        throw new ApiError(400, 'Invalid upload metadata');
      }
      const csv = await storage.bytes(`workspace-uploads/${workspace.id}/${requestId}.csv`, input.size);
      if (csv.length !== input.size || hash(csv) !== input.sha256
          || !csv.subarray(0, 1024).toString('utf8').startsWith('Stock #,Availability,Shape,Weight,')) {
        throw new ApiError(400, 'Natural inventory upload could not be verified');
      }
      const replaceAll = operation === 'replace';
      if (replaceAll) {
        const headerEnd = csv.indexOf(10);
        if (headerEnd === -1 || !csv.subarray(headerEnd + 1).some(byte => byte > 32 && byte !== 34 && byte !== 44)) {
          throw new ApiError(400, 'Cannot replace Rapaport listings with an empty inventory');
        }
      }
      const headers = await rapaportHeaders(fetcher, credentials);
      perform = async () => {
        const response = await rapaportUpload(fetcher, headers, csv, replaceAll);
        if (!uuid(response.uploadId)) throw new ApiError(502, 'Rapaport did not return an upload ID');
        return { uploadId: response.uploadId };
      };
    } else if (provider === 'email' && ['test', 'send'].includes(operation)) {
      const { rows } = await pool.query('SELECT * FROM owner_buyers WHERE workspace_id=$1 AND id=$2', [workspace.id, input.buyer_id || null]);
      perform = () => sendEmail(credentials, input, rows[0], transportFactory);
    } else {
      throw new ApiError(400, 'Unsupported integration operation');
    }

    const { rowCount } = await pool.query(`
      INSERT INTO owner_jobs(workspace_id,request_id,provider,operation,state) VALUES($1,$2,$3,$4,'dispatching')
      ON CONFLICT DO NOTHING`, [workspace.id, requestId, provider, operation]);
    if (!rowCount) {
      const { rows } = await pool.query('SELECT provider,operation,state,result FROM owner_jobs WHERE workspace_id=$1 AND request_id=$2', [workspace.id, requestId]);
      const prior = rows[0];
      if (prior?.state === 'completed' && prior.provider === provider && prior.operation === operation) return prior.result;
      throw new ApiError(409, 'This request was already attempted. Verify the provider outcome before sending again');
    }
    try {
      const result = await perform();
      await pool.query("UPDATE owner_jobs SET state='completed',result=$3 WHERE workspace_id=$1 AND request_id=$2", [workspace.id, requestId, result]);
      return result;
    } catch (error) {
      const state = error.definiteRejection || (error.status === 400) ? 'rejected' : 'needs-review';
      await pool.query('UPDATE owner_jobs SET state=$3 WHERE workspace_id=$1 AND request_id=$2', [workspace.id, requestId, state]);
      if (error instanceof ApiError) throw error;
      throw new ApiError(502, 'Provider did not confirm the operation. Verify its outcome before retrying');
    }
  }
  return dispatch;
}
