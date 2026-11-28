import { readFile } from 'node:fs/promises';

const token = process.env.WORKSPACE_PLATFORM_TOKEN;
const origin = new URL(process.env.WORKSPACE_ORIGIN);
if (!token || origin.protocol !== 'https:' || origin.origin !== process.env.WORKSPACE_ORIGIN) {
  throw new Error('Set a valid HTTPS WORKSPACE_ORIGIN and WORKSPACE_PLATFORM_TOKEN');
}
const settingsFile = process.argv[2];
if (!settingsFile) throw new Error('Pass a JSON command file');
const command = JSON.parse(await readFile(settingsFile, 'utf8'));
const result = await fetch(origin.origin + '/platform/owner', {
  method: 'POST', redirect: 'error', signal: AbortSignal.timeout(120000),
  headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
  body: JSON.stringify(command),
});
const data = await result.json();
if (!result.ok) throw new Error(data.error || 'Administration request failed');
process.stdout.write(JSON.stringify(data, null, 2) + '\n');
