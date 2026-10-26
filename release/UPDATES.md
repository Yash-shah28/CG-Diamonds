# Automatic updates (0.6.3)

Clients need to install ChintamaniGemsSetup-0.6.3.exe once to receive the updater.
Older portable builds do not contain an updater and cannot discover this release.
After installation the app checks https://chintamani-updates.vercel.app/api/latest
15 seconds after startup and every six hours. Check for updates is available at
the bottom of the sidebar. New versions download automatically. Choose Install
and restart to install; the app finishes current inventory processing first.
Closing or postponing the prompt keeps monitoring running on the current version.

The installer runs per user without administrator access. App files install in
%LOCALAPPDATA%\Programs\ChintamaniGems. Profiles/history remain in the existing
%LOCALAPPDATA%\DRCInventorySync\sync.db and API keys stay in Windows Credential
Manager. Update installation preserves the startup and shortcut choices recorded
by the installer. Runtime update checks do not send DRC credentials. The customer installer carries
a wrapped shared-credential payload for automatic local provisioning.

Build: powershell -ExecutionPolicy Bypass -File packaging/build.ps1
Requires .venv312 with project dev dependencies and Inno Setup 6.
Bump drc_sync/version.py, pyproject.toml and packaging/installer.iss together.
Test before publishing. Version numbers use three numeric components (e.g. 0.6.3).

Publishing source is in the separate sibling repository ../chintamani-updates.
Use its documented npm run publish-release command. Only the signed installer
metadata is accepted by the publishing APIs; the Blob store token stays on Vercel.
Keep the release signing private key in ~/.chintamani-release-signing backed up
privately. Do not ship it. The public verification key is bundled in the app.
Release signature verification does not provide a Windows Authenticode publisher
certificate; Windows may display Unknown publisher for this installer.

Updates use HTTPS, public Vercel Blob URLs, Ed25519 signatures, installer SHA-256
and size checks. Failed downloads, invalid signatures and older versions are
never executed. No automatic downgrades. An offline website leaves the existing
version usable and checks again later. Downloaded installers are cached in the
user data folder under updates. Private repository or Blob credentials are not
required on client PCs.

Only ChintamaniGems.exe is delivered. The legacy DRC-named data directory and
Credential Manager service remain internal compatibility identifiers so existing
connections, credentials and history continue to work. They are not a second app.

The customer installer now provisions the shared Natural and Lab Grown keys
automatically into Windows Credential Manager. No key entry or readable key file
is needed. See MANAGED_CREDENTIALS.md for provisioning and its security limits.
