# Version 0.7.4 — Released 2026-11-04

## Published release

Version 0.7.4 was built and published to the stable update channel.

- **Installer:** `ChintamaniGemsSetup-0.7.4.exe`
- **SHA-256:** `faee0b533cf480a8ac2aa9a3d04ce53050589353660589120011e9ecbce14dfb`
- **Size:** 44,026,057 bytes
- **Update feed:** https://chintamani-updates.vercel.app/api/latest

## Highlights in this release

- **Dual-Array Inventory Split**: Separated inventory API snapshot into two distinct arrays: `"natural"` and `"lab_grown"`.
  - Always includes both keys (`[]` if empty or unconfigured).
  - Maintained canonical `"Diamond Type"` and per-company price adjustments on each record.
- **Update Feedback in Development Mode**: Clear informative dialogs when checking for updates outside of frozen installer environments.
- **Store Prefix Compatibility**: Added direct support for both `INVENTORY_STORE_ID` and `INVENTORY_BLOB_STORE_ID` in Vercel Blob integrations.
