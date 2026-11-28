# Owner workspaces and private Windows releases

The owner service is a separate Node service. This rollout leaves the public stable installer and existing buyer API serving on their current origins. Deploy it on a dedicated origin and run the first migration beside the legacy publisher.

## First deployment

1. Use a PostgreSQL database and a private S3-compatible bucket. A shared Supabase project is supported because the service uses the private `chintamani_owner` schema. Verify the project reference before running `owner/provision.sql` once with the project database administrator. Set the new role's password privately using `\password chintamani_owner` in `psql`. Use that dedicated role in the owner service's direct or session-mode PostgreSQL connection. The app checks that the schema is owned by the connected role and is unavailable to the public and Supabase Data API roles. Keep the schema out of the Data API's exposed schemas. Existing schemas, roles, and API settings can stay as they are.
2. Configure the following on the server. Keep runtime secrets outside the source, installer, and database dump.

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | Direct or session-mode PostgreSQL connection string for the dedicated `chintamani_owner` role |
| `WORKSPACE_ORIGIN` | Exact public HTTPS origin of the owner service |
| `WORKSPACE_AUTH_SECRET` | Stable random secret of at least 32 characters for owner sessions |
| `WORKSPACE_CREDENTIAL_KEY` | Stable base64-encoded 32-byte encryption key for integration credentials |
| `WORKSPACE_ADMIN_TOKEN_SHA256` | SHA-256 of a separate platform administrator bearer token |
| `WORKSPACE_S3_BUCKET`, `WORKSPACE_S3_REGION`, `WORKSPACE_S3_ENDPOINT`, `WORKSPACE_S3_ACCESS_KEY`, `WORKSPACE_S3_SECRET_KEY` | Independent private object storage. Endpoint is optional for AWS |
| `LEGACY_PUBLIC_ORIGIN` | Exact existing buyer API origin. Defaults to the Chintamani updater origin |
| `LEGACY_BRIDGE_TOKEN` | Existing publisher credential supplied server to server. Required for verified legacy migration and publishing |
| `WORKSPACE_PORTABLE_BUYER_API` | Keep unset until the object transfer and portable buyer API are being rehearsed |
| `PORT`, `WORKSPACE_BIND` | Defaults to port 8080 on localhost. Put TLS at the reverse proxy |
| `WORKSPACE_TRUST_PROXY_IP` | Set to true only when direct access is blocked and the trusted proxy overwrites X-Real-IP |

From `inventory-service`, run `npm ci`, `npm run owner:migrate`, and `npm run owner:start`. Serve the process behind a TLS proxy that accepts up to 64 MiB on `/owner/backup` and has timeouts suitable for workbook transfers and provider operations. Securely back up the backend encryption key separately from the database and buckets. Reuse the same stable origin when redeploying.

The owner service can also be built on a Node host as a container with `docker build -f inventory-service/owner/Dockerfile -t chintamani-owner .`. Run `node owner/server.js --migrate` with the database configuration before starting the service. The service checks the private schema and bucket before listening. Verify `/healthz` through HTTPS before publishing a release.

### Separate Vercel project

Import the `CG-Diamonds` repository into a new project. Set **Root Directory** to `inventory-service`, **Framework Preset** to **Other**, and leave the build and output overrides empty. Select the owner deployment branch. Vercel loads the `vercel.json` file in that folder and runs `api/owner-runtime.js` as a Node.js Function.

Use the dedicated owner-role Supabase connection on the session pooler (port 5432). For verified Supabase TLS set `DATABASE_SSL_CA` to the project's database CA certificate as multiline PEM. Configure the private S3-compatible bucket with the server-side variables listed above. Run `npm run owner:migrate` separately before testing `/healthz`. Vercel enables direct backup transfers and uses short-lived signed storage links. The desktop encrypts archives before upload and the backend checks the archive before committing the new backup revision. The existing `chintamani-updates` project continues to serve buyer traffic.

For storage with a lower per-object limit, set `WORKSPACE_BACKUP_MAX_BYTES` to that limit minus 28 bytes for encryption overhead. For example, set `49999972` for a 50 MB object limit. Uploading a Windows installer also requires a bucket that accepts the installer’s actual size.

## Provision and privately release

Use a protected operator terminal with `WORKSPACE_PLATFORM_TOKEN` and `WORKSPACE_ORIGIN`. Supply a JSON command file to `node owner/admin.js command.json`. Commands include:

| Action | Additional fields |
| --- | --- |
| `create-workspace` | `name`, unique lowercase `slug`, `login`, `features`, `legacy_import`, `backup_enabled` |
| `set-features` | `workspace_id`, `features` |
| `set-backup` | `workspace_id`, `enabled` |
| `transfer-backup` | `workspace_id`, `device_id` from the receiving PC’s backup settings |
| `reset-password` | `workspace_id` |
| `register-release` | `envelope`, generated by the release signing command |
| `assign-release` / `unassign-release` | `workspace_id`, `version` |
| `set-publishing` | `workspace_id`, `enabled` |

Feature IDs are `natural`, `lab_grown`, `buyer_feeds`, `hrd`, `rapaport`, and `email`. Set `legacy_import: true` for the verified original owner only. Deliver the generated temporary password privately. The login flow requires its replacement before other workspace actions or the private installer download.

### Automatic workspace backup and restore

Enable `backup_enabled` only after the company authorizes uploading its existing app data and connected Excel workbooks. The owner opens the original local inventory immediately after sign-in. The background backup submits the app’s settings, cached inventory, buyer records and rates, history, and connected .xlsx or .xlsm files. The archive limit is 64 MiB. The backend records the verified revision in PostgreSQL and encrypts the archive in the private bucket. Supported provider credentials move through the existing encrypted import.

An empty installation signed into the same workspace automatically restores its latest verified archive. Excel paths point to restored copies. The original PC remains the writer and publisher during restore. An installation with existing local data preserves that data. Restored outbound jobs require review. For a replacement PC, stop the old publisher, verify the new copy, transfer the backup device, and follow the publishing handoff below.

Set `CHINTAMANI_WORKSPACE_ORIGIN` to the owner's HTTPS service origin in the packaging shell and build the Windows installer from the top-level packaging script. The installer includes this public address so the owner only enters a Login ID and password. Upload it under `private-releases/<version>/ChintamaniGemsSetup-<version>.exe` in the private bucket. On the signing machine, set `CHINTAMANI_SIGNING_KEY` and run `node owner/sign-release.js <installer> <version> <notes-file>`. Save the resulting command JSON, register it, then assign that version to the selected workspace. Give the selected owner the `/get-app` page address. The desktop app checks assigned versions with its signed-in session. The public stable channel is managed separately.

Older installations check a common public release feed before owner sign-in exists. Send a selected pilot owner to the private installer page for their first upgrade. Subsequent **Check for updates** requests use the signed-in company’s release assignments.

## Preserve buyer access during migration

- Back up the Windows SQLite database and run the import from the same Windows account. Sign in when the app opens, then open **Owner workspace** and choose **Import settings from this PC** to move the settings to the backend. Existing buyer IDs, key hashes, rates, scope, and Excel mappings are imported. Credentials available to this Windows account are read for enabled Rapaport, HRD, and Yahoo Mail and encrypted in the backend. Reconnect missing credentials through the owner screen.
- Keep the public legacy endpoint serving throughout. Compare representative buyer responses using the same URLs and keys. Check inventory types, prices, counts, feedback, and the current revision. If a live buyer is absent from the source PC, use the original owner's database or recover its setup before cutover.
- After parity checks, stop publishing from any older installation, wait for pending legacy inventory operations to drain, enable workspace publishing for the owner, and select **Use workspace publishing** on the owner’s PC. The bridge publishes via the current backend until the infrastructure move. Confirm a new snapshot arrives on the same buyer URL with the same key. A failed cutover can resume the legacy publisher against its last good snapshot. Keep a copy of the local setup during the pilot.

## Rehearse the move off Supabase

1. Export the owner schema with `pg_dump --format=custom --schema=chintamani_owner --no-owner --no-acl`. On the target create the dedicated `chintamani_owner` login role and set its password privately. Temporarily grant it `CREATE` on the target database, then use an administrator connection to run `pg_restore --exit-on-error --no-owner --no-acl --role=chintamani_owner`. The restore creates the schema and makes the dedicated role its owner. Revoke the database-level `CREATE` grant after restoring. Use secure connection settings and a locally protected backup destination. Preserve the separate auth secret, credential encryption key, installer verification key, and object storage credentials.
2. Configure `SOURCE_S3_*` for the owner service's source bucket and `WORKSPACE_S3_*` for the destination. Run `npm run owner:copy-storage -- --copy` to copy and verify the inventory, signed public release metadata, and private installer objects. Then unset `SOURCE_S3_BUCKET` and set `SOURCE_VERCEL_BLOB_STORE_ID` and `SOURCE_VERCEL_BLOB_TOKEN` for the existing private Vercel inventory store. Run the same command to copy the live buyer access records and snapshots under `inventory/`. The copier only reads the source. Run each command without `--copy` for read-only checksum comparison. Direct the new backend to the target once both sources have been verified.
3. Rehearse with the target service on a private origin, `WORKSPACE_PORTABLE_BUYER_API=true`, and the restored data. Verify the same owner password, two workspaces with different permissions, a real credential lookup using a controlled test request, private releases, and existing buyer keys and records. Keep the active production service online during the rehearsal.
4. For final cutover, pause publishing and workspace writes while continuing buyer reads from the original snapshot. Re-export the database and catch up the destination files. Verify them again before activating the portable backend. In the existing Vercel project use `lib/legacy-forward.js` on the existing `/api/v1/inventory`, `/api/v1/feedback`, `/api/latest`, and `/api/download` handlers, and block the old admin publisher when forwarding is enabled. Set `OWNER_BUYER_FORWARD_ORIGIN` to the portable Node backend only after verification. The existing Vercel buyer endpoint forwards server to server, preserving the client URL and authorization headers.
5. Check current buyer requests end to end, then resume publishing. If any check fails, restore the prior Vercel deployment and re-enable the legacy publisher. Keep the source available until the migrated owner, integration jobs, private updates and buyers are verified.
