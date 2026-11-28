CREATE TABLE IF NOT EXISTS owner_workspaces (
  id uuid PRIMARY KEY,
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text NOT NULL,
  user_id text UNIQUE,
  must_change_password boolean NOT NULL DEFAULT true,
  enabled boolean NOT NULL DEFAULT true,
  features text[] NOT NULL DEFAULT '{}',
  settings jsonb NOT NULL DEFAULT '{}',
  migration_id text,
  legacy_import boolean NOT NULL DEFAULT false,
  publishing_enabled boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS owner_buyers (
  id text PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
  workspace_id uuid NOT NULL REFERENCES owner_workspaces(id),
  key_hash text NOT NULL UNIQUE CHECK (key_hash ~ '^[a-f0-9]{64}$'),
  settings jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS owner_credentials (
  workspace_id uuid NOT NULL REFERENCES owner_workspaces(id),
  provider text NOT NULL CHECK (provider IN ('hrd','rapaport','email')),
  encrypted jsonb NOT NULL,
  PRIMARY KEY (workspace_id, provider)
);
CREATE TABLE IF NOT EXISTS owner_releases (
  version text PRIMARY KEY,
  object_key text NOT NULL,
  envelope jsonb NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS owner_release_assignments (
  workspace_id uuid NOT NULL REFERENCES owner_workspaces(id),
  version text NOT NULL REFERENCES owner_releases(version),
  PRIMARY KEY (workspace_id, version)
);
CREATE TABLE IF NOT EXISTS owner_jobs (
  workspace_id uuid NOT NULL REFERENCES owner_workspaces(id),
  request_id uuid NOT NULL,
  provider text NOT NULL,
  operation text NOT NULL,
  state text NOT NULL,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, request_id)
);
CREATE TABLE IF NOT EXISTS owner_login_limits (
  key text PRIMARY KEY,
  attempts integer NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_admin_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid REFERENCES owner_workspaces(id),
  action text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE owner_workspaces ADD COLUMN IF NOT EXISTS backup_enabled boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS owner_backups (
  workspace_id uuid PRIMARY KEY REFERENCES owner_workspaces(id),
  device_id uuid NOT NULL,
  revision bigint NOT NULL,
  sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size integer NOT NULL CHECK (size > 0),
  object_key text NOT NULL,
  saved_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE owner_backups ADD COLUMN IF NOT EXISTS cipher_format smallint NOT NULL DEFAULT 1;
