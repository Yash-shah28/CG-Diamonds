import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { betterAuth } from 'better-auth';
import { bearer, username } from 'better-auth/plugins';
import { getMigrations } from 'better-auth/db/migration';
import { ApiError } from '../lib/inventory-service.js';
import { FEATURES, hash, randomPassword } from './security.js';

export function ownerAuth(pool, env) {
  const secret = env.WORKSPACE_AUTH_SECRET;
  const baseURL = env.WORKSPACE_ORIGIN;
  if (typeof secret !== 'string' || secret.length < 32 || !baseURL) throw new Error('Workspace authentication is not configured');
  return betterAuth({
    database: pool, secret, baseURL,
    logger: { disabled: true },
    emailAndPassword: { enabled: true, autoSignIn: false, minPasswordLength: 12, maxPasswordLength: 128 },
    session: { expiresIn: 7 * 86400, updateAge: 86400, cookieCache: { enabled: false } },
    plugins: [username({ maxUsernameLength: 32, usernameValidator: value => /^[a-z0-9][a-z0-9_.-]{2,31}$/.test(value) }), bearer({ requireSignature: true })],
  });
}

export async function withTransaction(pool, work) {
  const db = await pool.connect();
  try {
    await db.query('BEGIN');
    try {
      const result = await work(db);
      await db.query('COMMIT');
      return result;
    } catch (error) {
      await db.query('ROLLBACK');
      throw error;
    }
  } finally { db.release(); }
}

export async function migrateDatabase(pool, auth) {
  const { rows } = await pool.query('SELECT current_schema() AS name');
  if (rows[0]?.name !== 'chintamani_owner') throw new Error('The owner migration requires the chintamani_owner schema');
  const { runMigrations } = await getMigrations(auth.options);
  await runMigrations();
  await pool.query(await readFile(new URL('./schema.sql', import.meta.url), 'utf8'));
}

export function features(value) {
  if (!Array.isArray(value) || value.some(feature => !FEATURES.includes(feature))) {
    throw new ApiError(400, 'Invalid workspace features');
  }
  return [...new Set(value)].sort();
}

export async function limitLogin(pool, login, remoteIp) {
  // Both limits use database counters so they work across process restarts and replicas.
  for (const [key, limit] of [[hash('login:' + login), 12], [hash('ip:' + remoteIp), 60]]) {
    const { rows } = await pool.query(`
      INSERT INTO owner_login_limits(key,attempts,expires_at) VALUES ($1,1,now()+interval '15 minutes')
      ON CONFLICT(key) DO UPDATE SET
        attempts=CASE WHEN owner_login_limits.expires_at <= now() THEN 1 ELSE owner_login_limits.attempts+1 END,
        expires_at=CASE WHEN owner_login_limits.expires_at <= now() THEN EXCLUDED.expires_at ELSE owner_login_limits.expires_at END
      RETURNING attempts`, [key]);
    if (rows[0].attempts > limit) throw new ApiError(429, 'Too many sign-in attempts. Try again in 15 minutes.');
  }
}

export async function issueSession(auth, usernameValue, password) {
  if (typeof usernameValue !== 'string' || typeof password !== 'string' || password.length > 128) {
    throw new ApiError(401, 'Invalid Login ID or password');
  }
  try {
    const result = await auth.api.signInUsername({ body: { username: usernameValue, password }, returnHeaders: true });
    const token = result.headers.get('set-auth-token');
    if (!token) throw new Error('Missing token');
    return token;
  } catch (error) {
    if (error.statusCode && error.statusCode >= 500) throw error;
    throw new ApiError(401, 'Invalid Login ID or password');
  }
}

export async function workspaceFor(pool, auth, headers, allowTemporary = false) {
  if (!/^Bearer \S+$/.test(headers.get('authorization') || '')) throw new ApiError(401, 'Sign in to your workspace');
  const session = await auth.api.getSession({ headers });
  if (!session?.user?.id) throw new ApiError(401, 'Sign in to your workspace');
  const { rows } = await pool.query('SELECT * FROM owner_workspaces WHERE user_id=$1 AND enabled=true', [session.user.id]);
  const workspace = rows[0];
  if (!workspace) throw new ApiError(403, 'Workspace access is unavailable');
  if (workspace.must_change_password && !allowTemporary) throw new ApiError(403, 'Set your own password first');
  return { workspace, user: session.user };
}

export async function createWorkspace(pool, auth, input) {
  const { login, slug, name } = input;
  if (!/^[a-z0-9][a-z0-9_.-]{2,31}$/.test(login || '') || !/^[a-z0-9][a-z0-9-]{1,62}$/.test(slug || '')
      || typeof name !== 'string' || !name.trim() || name.length > 120) {
    throw new ApiError(400, 'Enter a valid company name, slug and lowercase Login ID');
  }
  const id = randomUUID(), password = randomPassword();
  const enabledFeatures = features(input.features ?? []);
  // A legacy-claim workspace is explicitly provisioned by the platform admin.
  try {
    await pool.query('INSERT INTO owner_workspaces(id,slug,name,features,legacy_import,backup_enabled) VALUES($1,$2,$3,$4,$5,$6)',
      [id, slug, name.trim(), enabledFeatures, input.legacy_import === true, input.backup_enabled === true]);
  } catch (error) {
    if (error.code === '23505') throw new ApiError(409, 'This company slug is already registered');
    throw error;
  }
  let createdUser;
  try {
    const result = await auth.api.signUpEmail({ body: { name: name.trim(), email: `${id}@workspace.invalid`, username: login, password } });
    createdUser = result.user.id;
    await withTransaction(pool, async db => {
      await db.query('UPDATE owner_workspaces SET user_id=$1 WHERE id=$2', [createdUser, id]);
      await db.query("INSERT INTO owner_admin_history(workspace_id,action) VALUES($1,'create-workspace')", [id]);
    });
  } catch (error) {
    if (createdUser) await pool.query('DELETE FROM "user" WHERE id=$1', [createdUser]);
    await pool.query('DELETE FROM owner_workspaces WHERE id=$1 AND user_id IS NULL', [id]);
    if (error.statusCode && error.statusCode < 500) throw new ApiError(409, 'The Login ID is already in use or invalid');
    throw error;
  }
  return { id, login, temporary_password: password };
}

export async function platformReset(pool, auth, workspaceId) {
  const temporaryPassword = randomPassword();
  const passwordHash = await (await auth.$context).password.hash(temporaryPassword);
  await withTransaction(pool, async db => {
    const { rows } = await db.query('SELECT user_id FROM owner_workspaces WHERE id=$1 FOR UPDATE', [workspaceId]);
    if (!rows[0]?.user_id) throw new ApiError(404, 'Owner not found');
    const userId = rows[0].user_id;
    const changed = await db.query("UPDATE account SET password=$1 WHERE \"userId\"=$2 AND \"providerId\"='credential'", [passwordHash, userId]);
    if (changed.rowCount !== 1) throw new ApiError(409, 'Owner password account is unavailable');
    await db.query('DELETE FROM session WHERE "userId"=$1', [userId]);
    await db.query('UPDATE owner_workspaces SET must_change_password=true WHERE id=$1', [workspaceId]);
    await db.query("INSERT INTO owner_admin_history(workspace_id,action) VALUES($1,'reset-password')", [workspaceId]);
  });
  return { temporary_password: temporaryPassword };
}

export async function changeOwnerPassword(pool, auth, headers, workspace, user, input) {
  if (typeof input.password !== 'string' || input.password.length < 12 || input.password.length > 128
      || typeof input.current_password !== 'string' || input.current_password === input.password) {
    throw new ApiError(400, 'Choose a new password of at least 12 characters');
  }
  const context = await auth.$context;
  const passwordHash = await context.password.hash(input.password);
  await withTransaction(pool, async db => {
    const owner = await db.query('SELECT enabled FROM owner_workspaces WHERE id=$1 FOR UPDATE', [workspace.id]);
    if (!owner.rows[0]?.enabled) throw new ApiError(403, 'Workspace access is unavailable');
    const credentials = await db.query("SELECT password FROM account WHERE \"userId\"=$1 AND \"providerId\"='credential' FOR UPDATE", [user.id]);
    if (!credentials.rows[0]?.password || !await context.password.verify({ hash: credentials.rows[0].password, password: input.current_password })) {
      throw new ApiError(400, 'Could not change the password. Check your current password.');
    }
    await db.query("UPDATE account SET password=$1 WHERE \"userId\"=$2 AND \"providerId\"='credential'", [passwordHash, user.id]);
    await db.query('DELETE FROM session WHERE "userId"=$1', [user.id]);
    await db.query('UPDATE owner_workspaces SET must_change_password=false WHERE id=$1', [workspace.id]);
  });
  return { token: await issueSession(auth, user.username, input.password) };
}
