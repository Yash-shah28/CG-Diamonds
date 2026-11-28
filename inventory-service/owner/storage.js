import { DeleteObjectCommand, GetObjectCommand, PutObjectCommand, HeadObjectCommand, ListObjectsV2Command, S3Client } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { ApiError } from '../lib/inventory-service.js';

export function privateStorage(env = process.env) {
  if (!env.WORKSPACE_S3_BUCKET) throw new Error('WORKSPACE_S3_BUCKET is required');
  const bucket = env.WORKSPACE_S3_BUCKET;
  const client = new S3Client({
    region: env.WORKSPACE_S3_REGION || 'us-east-1',
    endpoint: env.WORKSPACE_S3_ENDPOINT || undefined,
    forcePathStyle: Boolean(env.WORKSPACE_S3_ENDPOINT),
    requestChecksumCalculation: 'WHEN_REQUIRED',
    responseChecksumValidation: 'WHEN_REQUIRED',
    ...(env.WORKSPACE_S3_ACCESS_KEY && env.WORKSPACE_S3_SECRET_KEY ? {
      credentials: { accessKeyId: env.WORKSPACE_S3_ACCESS_KEY, secretAccessKey: env.WORKSPACE_S3_SECRET_KEY }
    } : {}),
  });
  const get = key => client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
  async function read(key, max) {
    const response = await get(key);
    const chunks = [];
    let size = 0;
    for await (const chunk of response.Body) {
      size += chunk.length;
      if (size > max) throw new ApiError(413, 'Stored object is too large');
      chunks.push(chunk);
    }
    return { body: Buffer.concat(chunks), etag: response.ETag };
  }
  return {
    async ready() { await client.send(new ListObjectsV2Command({ Bucket: bucket, MaxKeys: 1 })); return true; },
    async json(key) {
      try {
        const { body, etag } = await read(key, 1024 * 1024);
        return { value: JSON.parse(body.toString('utf8')), etag };
      } catch (error) {
        if (error.$metadata?.httpStatusCode === 404 || error.name === 'NoSuchKey') return null;
        throw error;
      }
    },
    async putJson(key, value, etag) {
      try {
        await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: JSON.stringify(value), ContentType: 'application/json',
          ...(etag ? { IfMatch: etag } : { IfNoneMatch: '*' }) }));
      } catch (error) {
        if ([409, 412].includes(error.$metadata?.httpStatusCode)) throw new ApiError(409, 'Concurrent object update');
        throw error;
      }
    },
    async authorize(key, size) {
      return getSignedUrl(client, new PutObjectCommand({
        Bucket: bucket, Key: key, ContentType: 'application/json', ContentLength: size, IfNoneMatch: '*',
      }), { expiresIn: 300, signableHeaders: new Set(['content-length', 'content-type', 'if-none-match']) });
    },
    async bytes(key, max) { return (await read(key, max)).body; },
    async putBackup(key, body) {
      await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body,
        ContentType: 'application/octet-stream', IfNoneMatch: '*' }));
    },
    async authorizeBackup(key, size) {
      return getSignedUrl(client, new PutObjectCommand({
        Bucket: bucket, Key: key, ContentType: 'application/octet-stream', ContentLength: size,
      }), { expiresIn: 300, signableHeaders: new Set(['content-length', 'content-type']) });
    },
    async removeBackupStage(key) {
      if (!key.startsWith('owner-backup-staging/')) throw new Error('Invalid backup staging key');
      await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
    },
    async stream(key) { return (await get(key)).Body.transformToWebStream(); },
    async size(key) { return (await client.send(new HeadObjectCommand({ Bucket: bucket, Key: key }))).ContentLength; },
    async download(key) {
      return getSignedUrl(client, new GetObjectCommand({ Bucket: bucket, Key: key }), { expiresIn: 120 });
    },
  };
}
