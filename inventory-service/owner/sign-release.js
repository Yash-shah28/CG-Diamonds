import { createHash, createPrivateKey, sign } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { basename } from 'node:path';
import { validVersion } from './security.js';

const [installer, version, notesPath] = process.argv.slice(2);
if (!validVersion(version) || !installer || basename(installer) !== `ChintamaniGemsSetup-${version}.exe` || !notesPath) {
  throw new Error('Pass the Windows installer, its version, and a release-notes file');
}
if (!process.env.CHINTAMANI_SIGNING_KEY) throw new Error('Set CHINTAMANI_SIGNING_KEY to the offline private key file');
const notes = (await readFile(notesPath, 'utf8')).trim().slice(0, 4000);
const checksum = createHash('sha256');
for await (const chunk of createReadStream(installer)) checksum.update(chunk);
const body = Buffer.from(JSON.stringify({
  schema: 2, app: 'chintamani-gems', platform: 'windows-x64', version,
  object_key: `private-releases/${version}/${basename(installer)}`,
  size: (await stat(installer)).size, sha256: checksum.digest('hex'), notes,
}));
const privateKey = createPrivateKey(await readFile(process.env.CHINTAMANI_SIGNING_KEY));
console.log(JSON.stringify({ action: 'register-release', envelope: {
  payload: body.toString('base64'), signature: sign(null, body, privateKey).toString('base64'),
}}, null, 2));
