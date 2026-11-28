import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { pathToFileURL } from 'node:url';
import { GetObjectCommand, ListObjectsV2Command, PutObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { get, list } from '@vercel/blob';

function s3Client(env, prefix) {
  const endpoint = env[prefix + '_ENDPOINT'];
  const accessKeyId = env[prefix + '_ACCESS_KEY'];
  const secretAccessKey = env[prefix + '_SECRET_KEY'];
  return new S3Client({
    region: env[prefix + '_REGION'] || 'us-east-1', endpoint, forcePathStyle: Boolean(endpoint),
    requestChecksumCalculation: 'WHEN_REQUIRED', responseChecksumValidation: 'WHEN_REQUIRED',
    ...(accessKeyId && secretAccessKey ? { credentials: { accessKeyId, secretAccessKey } } : {}),
  });
}

function s3Source(client, bucket) {
  return {
    prefixes: ['inventory/', 'channels/', 'private-releases/', 'owner-backups/'],
    async page(prefix, cursor) {
      const page = await client.send(new ListObjectsV2Command({ Bucket: bucket, Prefix: prefix, ContinuationToken: cursor }));
      return { keys: (page.Contents || []).map(object => object.Key), next: page.NextContinuationToken };
    },
    async read(key) {
      const object = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
      return { body: object.Body, contentType: object.ContentType };
    },
  };
}

export function vercelInventorySource(storeId, token, sdk = { get, list }) {
  if (!storeId || !token) throw new Error('The source Vercel Blob store ID and token are required');
  const options = { storeId, token };
  return {
    prefixes: ['inventory/'],
    async page(prefix, cursor) {
      const page = await sdk.list({ ...options, prefix, cursor, limit: 1000 });
      const keys = page.blobs.map(blob => blob.pathname);
      if (keys.some(key => !key.startsWith(prefix))) throw new Error('The source returned an object outside the inventory prefix');
      if (page.hasMore && !page.cursor) throw new Error('The source returned an incomplete inventory listing');
      return { keys, next: page.hasMore ? page.cursor : undefined };
    },
    async read(key) {
      const object = await sdk.get(key, { ...options, access: 'private', useCache: false });
      if (!object?.stream) throw new Error('A private source inventory object is unavailable');
      return { body: object.stream, contentType: object.blob.contentType };
    },
  };
}

function s3Target(client, bucket) {
  return {
    async read(key) {
      try {
        return (await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }))).Body;
      } catch (error) {
        if (error.$metadata?.httpStatusCode === 404) return null;
        throw error;
      }
    },
    async write(key, body, size, contentType) {
      await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body,
        ContentLength: size, ContentType: contentType }));
    },
  };
}

async function digest(body) {
  if (!body) return null;
  const checksum = createHash('sha256');
  for await (const chunk of body) checksum.update(chunk);
  return checksum.digest('hex');
}

export async function verifyObjectCopy(source, target, copy = false) {
  const scratch = await mkdtemp(join(tmpdir(), 'inventory-object-transfer-'));
  const file = join(scratch, 'object');
  let count = 0;
  try {
    for (const prefix of source.prefixes) {
      let cursor;
      do {
        const page = await source.page(prefix, cursor);
        for (const key of page.keys) {
          const original = await source.read(key);
          await pipeline(original.body, createWriteStream(file, { mode: 0o600 }));
          const expected = await digest(createReadStream(file));
          let actual = await digest(await target.read(key));
          if (actual !== expected && copy) {
            await target.write(key, createReadStream(file), (await stat(file)).size, original.contentType);
            actual = await digest(await target.read(key));
          }
          if (actual !== expected) throw new Error('A target object is missing or has a different checksum');
          count++;
        }
        cursor = page.next;
      } while (cursor);
    }
    if (!count) throw new Error('The source did not contain any objects in the expected prefixes');
    return count;
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
}

async function run(env, copy) {
  const sourceBucket = env.SOURCE_S3_BUCKET;
  const targetBucket = env.WORKSPACE_S3_BUCKET;
  const vercel = Boolean(env.SOURCE_VERCEL_BLOB_STORE_ID || env.SOURCE_VERCEL_BLOB_TOKEN);
  if (!targetBucket || vercel === Boolean(sourceBucket)) throw new Error('Configure one source and the target S3 bucket');
  if (sourceBucket === targetBucket && env.SOURCE_S3_ENDPOINT === env.WORKSPACE_S3_ENDPOINT) {
    throw new Error('The source and target must be different');
  }
  const sourceClient = vercel ? null : s3Client(env, 'SOURCE_S3');
  const targetClient = s3Client(env, 'WORKSPACE_S3');
  try {
    const source = vercel ? vercelInventorySource(env.SOURCE_VERCEL_BLOB_STORE_ID, env.SOURCE_VERCEL_BLOB_TOKEN)
      : s3Source(sourceClient, sourceBucket);
    const count = await verifyObjectCopy(source, s3Target(targetClient, targetBucket), copy);
    console.log(`Verified ${count} private objects.`);
  } finally {
    sourceClient?.destroy();
    targetClient.destroy();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await run(process.env, process.argv.includes('--copy'));
}
