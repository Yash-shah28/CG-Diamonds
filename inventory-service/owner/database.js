import pg from 'pg';

const workspaceSchema = 'chintamani_owner';

export function workspacePool(connectionString, { max = 8, sslCA } = {}) {
  let ssl;
  if (sslCA) {
    const database = new URL(connectionString);
    for (const key of ['sslmode', 'sslcert', 'sslkey', 'sslrootcert']) database.searchParams.delete(key);
    connectionString = database.toString();
    ssl = { ca: sslCA, rejectUnauthorized: true };
  }
  return new pg.Pool({
    connectionString, max, maxLifetimeSeconds: 90, connectionTimeoutMillis: 10000,
    ...(ssl ? { ssl } : {}),
    async onConnect(client) {
      const { rows } = await client.query(`
        SELECT n.nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
          AND NOT EXISTS (
            SELECT FROM aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) WHERE grantee = 0
          )
          AND NOT EXISTS (
            SELECT FROM pg_roles
            WHERE rolname IN ('anon', 'authenticated', 'service_role')
              AND has_schema_privilege(oid, n.oid, 'USAGE, CREATE')
          ) AS isolated
        FROM pg_namespace AS n
        WHERE n.nspname = $1`, [workspaceSchema]);
      if (!rows[0]?.isolated) {
        throw new Error('The owner database role must own the private chintamani_owner schema');
      }
      await client.query("SELECT set_config('search_path', $1, false)", [workspaceSchema]);
    },
  });
}
