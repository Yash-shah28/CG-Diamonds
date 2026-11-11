Validation performed on Windows with Python 3.12.14.

Command: .\.venv312\Scripts\python.exe -m pytest -q --basetemp=.test-tmp/run312a --tb=short
Result: 27 passed in 15.22s.

Coverage includes workbook parsing/validation, mappings, duplicate snapshots, profile separation, queue supersession, mocked authentication and responses, partial outcomes, retries, interrupted-request recovery, unsafe endpoint rejection, stability debounce, filesystem replacement events, Qt window construction and a real worker reacting to workbook edits.

All API tests used httpx.MockTransport. No requests were sent to DRC. Supplied credentials were stored in Windows Credential Manager for Natural and Lab Grown, and retrieved for equality verification without displaying them.

Not validated: DRC request/response schema, import/deletion semantics, type distinction with shared credentials, production end-to-end behavior, signed distribution, installer execution or clean-machine installation.

Packaged executable was built successfully with PyInstaller 6.22.3. An isolated launch initialized its SQLite database and remained running. The check used an empty data directory and offscreen Qt, then stopped only that test process. No remote requests were made. The spec excludes a conflicting non-system ICU DLL discovered during this launch check.

Window activation fix: 28 tests passed in 13.67s, including a second-instance activation test that restores a hidden window and verifies that the lock is released on shutdown. Windows named-pipe tests were run outside the sandbox because the sandbox restricts pipe creation.

DRC request preset 0.2.0: full user-supplied import-header catalogue, corrected /v1/inventory/push endpoint, curl-compatible numeric strings, profile-derived Diamond Type, optional blank omission, migration of original generic mappings and payload preview/auto-map controls. Tests cover every accepted header alias and a complete record containing every listed import field. Request tests use mocked HTTP only.
Latest complete validation: 37 tests passed in 16.20s on Python 3.12.14, including all 67 supplied import properties. The inventory endpoint has not been contacted during tests.
Final 0.2.0 verification: 38 tests passed in 18.35s on Python 3.12.14. The rebuilt executable was observed running in the normal Windows session with its Inventory Sync window open. Includes canonical-profile migration coverage. All HTTP tests used mock transports; no development inventory requests were sent.

Excel import usability fix (0.2.1): blank configuration text and {} are accepted as an empty profile list. Invalid JSON reports its location and offers Reload saved settings. Workbook failures no longer appear as JSON errors. Added RapNet aliases for Price Per Ct, Report #, Cert comment, VideoURL and Rapaport Trade Discount %. New profiles allow blank Color and Clarity. Read-only validation of the user-provided 117004_RapnetInventory.xlsx parsed 274 rows with 42 recognized columns; White Inclusion and Open Inclusion were intentionally left unmapped. The workbook was not modified and its inventory was not uploaded.
Final test result for 0.2.1: 43 passed in 15.26s. The actual user workbook was also exercised through the Add Excel dialog code with dialog selection automated; blank, {}, and [] configurations each successfully created a profile without warning.

Response viewer release 0.3.0: manual two-step preparation/confirmation, exactly one explicit request, bounded streaming response capture, JSON/plain-text viewer, status/timing metadata, per-attempt persisted responses, redacted credential fields and values, old-schema migration and manual-request restart protection. Complete mocked test suite: 71 passed in 18.63s. The viewer was rendered and inspected using an example response clearly marked as not from DRC. No production inventory was submitted during development.
Final 0.3.0 release verification: 71 passed in 18.38s after JSON-format-preserving redaction. All frozen drc_sync modules were compared against the verified source and matched. The packaged executable started against an isolated SQLite database containing one mocked response and no automatic jobs. The normal installation had zero automatic profiles with a confirmed success rule before replacement. No live DRC request was sent during development.

Manual failure diagnosis 0.3.1: added stage-specific, message-free sanitized exception details; offline local setup check and packaged CLI; response retention on later processing/history failures; in-memory viewer fallback when SQLite saving is unavailable; pre-dispatch classification and no manual retries. Test suite: 82 passed in 20.37s. The initial packaged offline check found FileNotFoundError in SSLContext construction because the managed Python runtime's codex-rejected-cas.pem policy asset was absent. Spec now includes the original runtime policy asset without weakening certificate verification. No production inventory request was made.
Packaged 0.3.1 offline diagnostic after the fix: Natural credential lookup Ready; HTTPS client setup Ready; ok=true; requests_sent=0. SHA-256 comparison confirmed the bundled SSL policy file is unchanged from the build runtime. All frozen app modules match the verified source. Existing history and credentials were not rewritten by the diagnostic.

Dashboard and Excel Mapping release 0.4.0: exactly two tabs, fixed Natural / Lab Grown connection flows, one workbook per type with replacement preserving the other connection and original job filenames, internal profile serialization, per-type secure credential configuration, filtered dashboard history and response viewing. Queued API acceptance remains distinct from completed import success. Added last successful import, failure, queued upload and unsuccessful attempt. Final offline suite: 85 passed in 17.80s. All HTTP tests are mocked.
Packaged 0.4.0 verification: all frozen application modules match the tested source. Natural offline diagnostic passed credential lookup and HTTPS client setup with requests_sent=0. The rebuilt executable initialized an isolated empty database and remained running. Both screens were rendered with a disabled worker and inspected.

Automatic-mapping repair: Edit Mapping now falls back to header aliases for missing or blank saved mappings while preserving custom targets. Regression tests cover both inventory types. Offline suite: 87 passed in 19.08s. Read-only workbook header check: 42 recognized columns with unique targets; White Inclusion and Open Inclusion remain unconfirmed API fields. No workbook changes or uploads.

Save-triggered sync: explicit upload_on_save configuration allows requests while retaining unknown outcomes as review. The watcher establishes a baseline at startup, waits for stable file saves, and bypasses content deduplication for each distinct save. Repeated filesystem events for the same signature do not resend. Save-triggered requests never retry automatically. Development uses mocked HTTP only.
Final save-trigger verification: 89 tests passed in 34.01s. Real watcher regression used mocked HTTP, verified zero startup requests, one request per stable unchanged-value save and no duplicate event requests. Frozen modules match tested source. Isolated packaged startup passed. Existing queued payloads are preserved during activation.

Lab Grown formula support: paired formula/result readers use Excel cached values for mapped formula cells, retaining normal numeric validation and string conversion. Missing cached results and cached Excel errors block parsing and show row/column guidance in monitoring history. Natural formula behavior is unchanged. No spreadsheet or production inventory was modified during tests.
Final formula-support validation: 93 passed in 35.18s. Frozen app modules match tested source; isolated packaged UI startup passed. No production request or workbook modification was made during development.

Startup / acknowledgement UI update: --startup opens in the tray when available and resumes monitoring. Installer sign-in startup option is selected by default. The exact successful queue acknowledgement, with HTTP 2xx and no reported partial errors, is persisted as sent and displayed as Sent to DRC, with a desktop notification for each new accepted automatic upload. Removed manual sync/refresh/send controls and API/setup buttons from the two screens. Historical queued responses receive the same display label without modifying history.
Final startup / acknowledgement checks: 98 offline tests passed in 38.13s; final UI labels passed 4 focused checks. Frozen app modules match tested source. Packaged --startup mode remained running against an empty database. Production inventory was not submitted during development. Windows sign-in registration is verified separately; a full reboot was not performed.

Website rate adjustments: Change Rates tab with DRC increase/discount percentage, live example and persisted website_rates configuration. Applies to both inventory types during snapshot creation and payload preview, from original workbook values each time. Uses Decimal and half-up cents rounding, rejects invalid percentages and non-positive adjusted prices, preserves other fields and workbook content. Saving settings does not enqueue uploads. Default 0% preserves current behavior.
Final Change Rates validation: 120 tests passed in 36.16s. UI rendered and inspected. Frozen application modules match tested source, including rates module. Packaged sign-in startup mode passed using an empty database. No production request or workbook modification was made during development.

Chintamani Gems 0.5.0 rebrand: original faceted gemstone logo (SVG, PNG and seven-size ICO), branded header, emerald/gold styling, dashboard metrics, collection cards, rate panel, window/tray/executable icon and renamed executable. Internal settings, credential service and instance identity remain compatible. Existing suite: 120 passed in 36.60s. All three screens rendered and inspected with the worker disabled; no live inventory request was made.
Final brand checks: focused UI tests passed after spacing adjustments; 1060x760 layout and seven icon sizes verified. Frozen app modules match final source. ChintamaniGems.exe and the compatibility DRCInventorySync.exe both passed isolated startup checks using empty databases. No production inventory was submitted during development.

Light/sidebar redesign: four-page stacked navigation, persistent collapsed sidebar with icons/tooltips, separate History table and response viewing, blue/white theme with Segoe UI, removed slogans and footer lines, blue gem logo, website-driven independent rate rows. Increased rates capped at 100% in the editor and backend; discounts remain below 100%. Invalid legacy settings remain saved and block preparation/dispatch until explicitly corrected. No production requests during development.
Final sidebar release validation: 129 tests passed in 40.14s; four final compact-control tests passed in 4.47s. All four pages rendered at 1240x820 and 1060x760, plus collapsed navigation. Frozen app modules match final source. Final packaged startup passed with an empty database; compatibility launcher also passed. No production inventory was sent during testing.

Typography / rate-input refinement: larger consistent Segoe UI sizing, clearer labels and spacing, compact five-column rate table, exclusive minus/plus adjustment buttons, no price example. Replaced the internally collapsed spinbox editor with a normal validated numeric text field (minimum width 140px, 44px height); numeric value visibility checked through rendering. Existing percentage caps, saved modes and upload behavior retained.
Final typography/input release: 131 tests passed in 42.90s, including real keyboard entry, plus/minus mode selection and invalid-text preservation. Percentage text rendered visibly at 1060x760. Frozen app modules match final source; packaged startup passed with an empty database. No production inventory sent during testing.

Popup/grid/icon refinement: custom worksheet/profile pickers with 440px minimum width, readable selection controls and secondary Cancel; mapping dialog with explicit Save mapping, 56px rows and fully contained 42px editors. Corrected table-item padding that cropped cell widgets. Unified grey grid headers, row borders and status pills. Replaced navigation with high-resolution outline icons and a simplified circular gem logo. Full offline suite: 133 passed in 54.43s; final popup containment checks: 2 passed in 5.04s. Rendered and inspected picker, mapping dialog, history and collapsed navigation. No production uploads during testing.
Final popup/grid/icon release: all 133 offline tests passed; focused popup containment tests passed. Frozen application modules match final source. Final mapping and history layouts inspected after clipping fixes. Packaged startup with the newly bundled SVG renderer passed using an empty database with a bounded initialization wait. No production inventory sent during testing.

## Version 0.6.1 updater validation (2026-10-24)

- 157 Python tests passed, including 24 updater tests; HTTP inventory tests mocked.
- 10 update website tests passed: real Ed25519 verification plus mocked storage API
  behavior, release validation, authorization expiry and version ordering.
- Website dependency audit: 0 vulnerabilities after updating the Zod override.
- Full frozen module comparison matched every drc_sync module to tested source.
- Staged Windows app and isolated installer-installed app launched successfully
  using empty isolated databases; executable hashes matched.
- Inno Setup SmokeTest compilation installed the same packaged payload while
  excluding real shortcuts, startup changes and uninstall registration.
- Production installer built separately with normal per-user startup/shortcut
  choices and cooperative update handoff; no forced process termination.
- Existing production app, profiles, history and Credential Manager were not
  modified during tests. No DRC production inventory requests made.

Delivery build is 0.6.1. Final real Windows handoff testing checks the parent exit,
.NET SHA-256 verification and installer arguments without PowerShell module dependencies.

Final delivery verification: version 0.6.1 was published to the connected Vercel
Blob store through an OIDC-compatible presigned upload. The live signed feed
selected 0.6.1 for an older client and no update for the current version. A full
public Blob download passed SHA-256/size verification against the local installer.
The website Download for Windows link points to the signed release's installer URL.

## Version 0.6.2 branding cleanup (2026-10-25)

160 tests passed. Packaged modules matched source. The delivery folder contains
only ChintamaniGems.exe and ChintamaniGems.ico at its root, with dependencies in
_internal; no duplicate DRCInventorySync.exe is bundled. An isolated installer
test supplied the old DRCInventorySync directory and confirmed redirection to
ChintamaniGems, removal of an obsolete launcher fixture, and successful app launch.
Legacy data and credential identities remain compatible. No production inventory
requests were made. Shortcut/startup cleanup is scoped to known obsolete names.

## Version 0.6.3 managed customer credentials (2026-10-25)

Shared Natural/Lab Grown credentials are provisioned into Windows Credential
Manager without a client key-entry screen. The installer's provisioning operation
is offline; runtime reads enforce the packaged shared credential set. The wrapped
payload is compiled into the application; no readable credential config is shipped.
This does not make credentials extraction-proof against a determined local user.

The full suite passed (167 tests), followed by the additional corrupt-bundle test
and all eight credential tests passing. Frozen modules matched source. Packaged
fresh-store self-test passed using an in-memory store with zero network requests.
The packaged real Windows provisioning command returned success and preserved the
existing matching approved credentials. Shared values matched the build machine's
approved set; scanning the packaged output found no plaintext credential values.
No production inventory requests were sent during development or verification.
The isolated installer-installed executable also passed its fresh-machine
credential self-test with both inventory types, idempotent provisioning, zero
requests, no plaintext credential file and no real test credential-store writes.
Smoke installer mutex was isolated from the running production app; the production
installer retains its normal application mutex and offline setup callback.

## Version 0.7.0 pull inventory feature (2026-10-27)

- Full Python regression suite: 185 passed, isolated SQLite and mocked network.
- Inventory backend: 13 tests passed. Updates repository: 23 tests passed,
  including the existing signed updater contract.
- Checked company scope isolation, original-price adjustments, rate boundaries,
  immutable snapshot integrity, stale revisions, rotation, revocation, interrupted
  publication, legacy queued-job preservation and zero DRC dispatch.
- Checked Google loopback/PKCE authorization, send acceptance, redaction, ambiguous
  email outcomes without automatic retries, and local history-storage failures.
- Checked all five pages at 1240x890 and 980x680, expanded/collapsed sidebar,
  detailed workbook error notifications and History access. Rendered and visually
  checked all five integration-guide PDF pages; no real credentials are included.
- Backend scaffold pushed as updates repository commit 81e5b55. Anonymous inventory
  endpoint returns 503 until private hosting is configured. Public updater remains
  HTTP 200 and its signed payload remains version 0.6.3.
- No production inventory or invitation email was sent. Existing installed app,
  production database, Windows credentials, shortcuts and startup were untouched.
- Private Blob store, owner management hash and Google Desktop OAuth application
  are still required before live activation/customer delivery. Version 0.7.0 is
  not published to the automatic update channel.
- Final frozen modules matched current source; the bundled DRC credential module
  was excluded. Frozen offline diagnostic and smoke-installer-installed diagnostic
  both passed: pull-only, no legacy dispatch, send-only Google scope, PDF ready,
  zero network requests. Normal frozen startup passed with isolated empty data and
  updates disabled. The test process was closed without replacing the installed app.
Local production installer compiled successfully (52,792,758 bytes); not published.

## 0.7.0 stable publication — 2026-10-27

Published at the owner's explicit request despite incomplete hosting/Google setup.
Verified the live stable manifest's Ed25519 signature and version 0.7.0, then
retrieved the public download and verified its exact 52,792,758-byte length and
SHA-256 against the signed manifest. No production inventory/email was sent.

## Version 0.7.4 update button repair (2026-11-03)

The project was located in the renamed Desktop/Diamond directory. Verified that
live signed release discovery returns HTTP 200 and stable version 0.7.3. Corrected
source-mode updater initialization and manual clicks during automatic checks.
Source mode checks for releases and reports installer availability without trying
to replace the Python environment. Explicit test-mode disabling is now visible.
All 189 regression tests passed, including 30 update tests. No inventory requests,
real emails, production data changes or stable-channel publication were performed.
Frozen source matched the tested code. The offline executable diagnostic passed
with zero requests. The 0.7.4 installer compiled successfully; SHA-256 is recorded
in release/SHA256SUMS-0.7.4.txt. Existing installation and stable feed unchanged.

## Version 0.8.2 fetch status and full-page rates (2026-11-11)

Fixed API activity writes finishing after response return and the desktop retaining
references to deleted activity workers. Polling restarts correctly, uses the default
hosting origin when needed, and distinguishes unknown/unavailable activity from
confirmed absence of fetches. Dashboard follows the same status distinction.

Removed the rates table height ceiling and competing page spacer. Checked 60
sample companies, last-row visibility, rate edits, and table resizing at 980x680,
1240x890 and 1240x1050 using isolated SQLite data and no real vault writes.

196 Python tests and 30 backend tests passed; all 9 focused UI/activity tests passed
after the final Dashboard refinement. Backend commit 7494d30 was pushed and its
Vercel deployment status verified successful. Anonymous inventory access remained
HTTP 401. No valid company feed was fetched for testing, no production inventory
was sent, and no real test companies/emails were created. Installer is local only.

### Authentication audit addition and final 0.8.2 delivery

Added private immutable request audit objects and admin-only paged retrieval for
accepted, missing, unknown, revoked and suspended company-key authentication.
Retained hashed key bindings across rotation/revocation; never inferred company
from IP, agent or a supplied name. Tests verify no raw key in records, log-safe
metadata, generic 401 responses and private pagination on both storage adapters.

History persists deduplicated audit records in a new table, supports authentication
result filters and older pages, and displays company, HTTP code, fingerprint and
result with full request details. Missing/unknown keys show Unknown company.
Existing profiles, credentials, rates and history were preserved.

198 desktop and 37 backend tests passed. All 11 focused UI/audit tests passed after
layout refinements. Visually inspected synthetic History rows and 60 sample company
rate rows. Backend commit c5612ac deployed with Vercel success confirmed.
Final frozen modules matched source; offline diagnostic passed with zero requests.
The 0.8.2 installer compiled successfully; SHA-256 is in release/SHA256SUMS-0.8.2.txt.
Installer remains local; stable automatic updates and the installed app were not
replaced. Audit capture starts at deployment; older requests cannot be backfilled.
