# Update delivery for 0.7.0

The existing signed-update system is preserved. The desktop checks
https://chintamani-updates.vercel.app/api/latest and downloads a newer signed,
SHA-256-verified installer. Installation is explicitly confirmed and waits for the
serialized worker to finish before restarting. Never force-close an active upload.

Version 0.7.0 replaces direct DRC push with pull-only publication. Its installer is
prepared locally but **must not replace the stable update channel until private
inventory hosting and Google OAuth are configured and verified**. The owner has
not created those services yet. The live stable release remains unchanged.

Before releasing:
- Complete `inventory-service/SETUP.md` with the owner.
- Verify authenticated hosting readiness without publishing production inventory.
- Configure a production Google Desktop OAuth application/consent screen. A real
  invitation is sent only by an explicit owner action, never as a development test.
- Run the full offline suite and packaged self-test with isolated data.
- Publish the tested installer with the existing sibling updates repository's
  `npm run publish-release -- <installer> 0.7.0 <notes>` command.

The release signing private key stays on the release PC under
`~/.chintamani-release-signing/ed25519-private.pem`. Never distribute it. The desktop
contains only its public verification key. Release verification does not provide
Windows Authenticode signing; Windows may show an unknown-publisher prompt.

The installed app folder remains `%LOCALAPPDATA%/Programs/ChintamaniGems`. The existing
`%LOCALAPPDATA%/DRCInventorySync/sync.db` and old Credential Manager service identity
remain for compatibility. Portal credentials use a separate service. Existing
shortcuts/startup choices and the Chintamani Gems app identity are retained.

## Current publication status — 2026-10-27

The owner explicitly requested publication after the setup limitations were
reported. Stable now serves 0.7.0. The earlier pre-release status above is historical;
private hosting and Google sender setup remain required for live use. The live
manifest signature and downloaded installer size/SHA-256 were verified.
