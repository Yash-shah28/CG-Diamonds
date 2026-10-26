# Managed API credentials — version 0.6.3

The customer installer provisions the same Natural and Lab Grown DRC keys on each
installation. Clients do not enter or view credentials in the app. Credentials
are stored in the current Windows user's Credential Manager under the existing
service identity. Excel connections, pricing and sync history remain separate
and are preserved on upgrades.

The shared payload is authenticated and wrapped at build time and compiled into
the application; no api-keys.json, plaintext credential file or key-bearing command
line is distributed. Application UI, diagnostics and installer messages never
print the keys. The installer invokes an offline provisioning operation before
opening the app. Credential storage errors block uploads; there is no plaintext
fallback. Managed keys are authoritative and cannot be overridden in the app.

This is protection against casual viewing, not an extraction-proof secret store:
the installer must carry the material needed to recover the keys on a new PC.
A determined local user/admin can recover distributed desktop credentials. Keeping
keys completely off client PCs requires a backend that authenticates clients and
makes DRC requests server-side.

Build using packaging/build.ps1. Its build-only generator reads both current keys
from the release computer's Windows Credential Manager and writes an ignored,
wrapped module before freezing. Never commit that generated module to GitHub.
Changing a shared key requires a tested new app release, not editing client files.
Keep build outputs and installers restricted to the intended customer where possible.

Offline packaged verification:
ChintamaniGems.exe --credential-self-test --diagnostics-output <report.json>
This uses an in-memory credential store, never writes real credentials and sends
no requests. Reports contain only inventory-type names, pass/fail and counts.
