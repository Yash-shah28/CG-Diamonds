import { createCipheriv, createDecipheriv, createHash, randomBytes, timingSafeEqual, verify } from 'node:crypto';
import { ApiError } from '../lib/inventory-service.js';

export const FEATURES = ['natural', 'lab_grown', 'buyer_feeds', 'hrd', 'rapaport', 'email'];
export const PROVIDERS = {
  hrd: ['token'],
  rapaport: ['client_id', 'client_secret'],
  email: ['sender', 'password'],
};
export const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value);
export const buyerId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
export const keyHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export const hash = value => createHash('sha256').update(value).digest('hex');
export const randomPassword = () => randomBytes(24).toString('base64url');

export function requireAdmin(header, expectedHash) {
  if (!keyHash(expectedHash)) throw new ApiError(503, 'Owner administration is unavailable');
  const token = header?.match(/^Bearer (\S+)$/)?.[1];
  if (!token || !timingSafeEqual(Buffer.from(hash(token), 'hex'), Buffer.from(expectedHash, 'hex'))) {
    throw new ApiError(401, 'Unauthorized');
  }
}

export function credentialKey(encoded) {
  const value = Buffer.from(encoded || '', 'base64');
  if (value.length !== 32) throw new Error('WORKSPACE_CREDENTIAL_KEY must contain a base64-encoded 32-byte key');
  return value;
}

export function seal(key, workspace, provider, value) {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(Buffer.from(`${workspace}:${provider}:1`));
  const data = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
  return { version: 1, iv: iv.toString('base64'), tag: cipher.getAuthTag().toString('base64'), data: data.toString('base64') };
}

export function unseal(key, workspace, provider, value) {
  if (value.version !== 1) throw new Error('Unsupported encryption version');
  const cipher = createDecipheriv('aes-256-gcm', key, Buffer.from(value.iv, 'base64'));
  cipher.setAAD(Buffer.from(`${workspace}:${provider}:1`));
  cipher.setAuthTag(Buffer.from(value.tag, 'base64'));
  return JSON.parse(Buffer.concat([cipher.update(Buffer.from(value.data, 'base64')), cipher.final()]));
}

export function credentialsFor(provider, value) {
  const fields = PROVIDERS[provider];
  if (!fields || !value || typeof value !== 'object' || Array.isArray(value)) throw new ApiError(400, 'Invalid credential provider');
  const credentials = {};
  for (const field of fields) {
    if (typeof value[field] !== 'string' || !value[field].trim() || value[field].length > 2048) {
      throw new ApiError(400, 'Incomplete provider credentials');
    }
    credentials[field] = value[field].trim();
  }
  if (provider === 'email' && !validEmail(credentials.sender)) throw new ApiError(400, 'Invalid sender email');
  return credentials;
}

export const validEmail = value => typeof value === 'string' && value.length <= 254 && /^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$/.test(value);
export const validVersion = value => typeof value === 'string' && /^\d{1,5}\.\d{1,5}\.\d{1,5}$/.test(value);
export function compareVersions(a, b) {
  const left = a.split('.').map(Number), right = b.split('.').map(Number);
  return left[0] - right[0] || left[1] - right[1] || left[2] - right[2];
}

export function verifyPrivateManifest(envelope, publicKey) {
  try {
    const payload = Buffer.from(envelope.payload, 'base64');
    if (payload.length > 16000 || !verify(null, payload, publicKey, Buffer.from(envelope.signature, 'base64'))) throw new Error();
    const value = JSON.parse(payload);
    if (value.schema !== 2 || value.app !== 'chintamani-gems' || value.platform !== 'windows-x64'
        || !validVersion(value.version) || value.object_key !== `private-releases/${value.version}/ChintamaniGemsSetup-${value.version}.exe`
        || !keyHash(value.sha256) || !Number.isSafeInteger(value.size) || value.size < 1 || value.size > 1024 ** 3
        || typeof (value.notes ?? '') !== 'string' || (value.notes ?? '').length > 4000) throw new Error();
    return value;
  } catch {
    throw new ApiError(400, 'Invalid private installer manifest');
  }
}
