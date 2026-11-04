# Version 0.7.3 — Released 2026-11-03

## Published release

Version 0.7.3 was built and published to the stable update channel.

- **Installer:** `ChintamaniGemsSetup-0.7.3.exe`
- **SHA-256:** `9a18498da4cdb28fa2dde8e9fd1716884d57b02c5409e4cf2ce9535577ff4200`
- **Size:** 44,029,799 bytes
- **Update feed:** https://chintamani-updates.vercel.app/api/latest

## Fixes in this release

- **Private Upload Address**: Added `vercel.com` (and `*.vercel-storage.com`) to trusted upload hosts in `portal_client.py`. Resolves `"Hosting returned an untrusted upload address"` during inventory file save/publish.
- **Workbook Updating**: Changing or saving an Excel file immediately triggers validation and caches the inventory.
- **Disconnect Option**: Added a dedicated Disconnect button in Excel Mapping.
- **Header Matching**: Case-insensitive column matching for Excel workbooks.
- **Fancy Diamonds**: Support for Fancy Color inventory without requiring a white Color field.

## Update delivery

Any installed copy of Chintamani Gems (0.7.2 or earlier) will discover this
release automatically — 15 seconds after launch and every 6 hours, or when clicking
**"Check for updates"** in the sidebar.

