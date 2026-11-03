# Chintamani Gems 0.7.0

Windows desktop management for Natural and Lab Grown Excel inventory, per-company
pricing, protected pull API access and explicit Google invitation emails.

## Important delivery state

Version 0.7.0 implements **pull-only** publishing. It never dispatches old DRC push
jobs. Existing profiles, rates, credentials and history are retained; legacy history
is read-only. The currently installed production app is not replaced by development
or testing. Do not publish this installer to automatic updates until hosting and
Google setup have been completed and verified for the intended owner.

The inventory API backend extension is under `inventory-service/` and deployed
alongside the existing Vercel updater service. No web admin dashboard is required.
It needs a **new private inventory Blob store** and a separate admin-token hash.
The public installer store and updater endpoints remain unchanged.

## Desktop workflow

1. Keep or connect one Natural and one Lab Grown workbook under Excel Mapping.
2. Open API Access > Hosting & Google setup. Follow [the setup guide](inventory-service/SETUP.md)
   to configure private hosting, generate the local admin credential/hash and connect
   the sender using a Google Cloud Desktop OAuth client with Gmail API enabled.
3. Use Publish inventory to validate the initial source data. Excel saves subsequently
   validate and prepare updated feeds. Invalid/empty data retains the last good feed.
4. Add a company with its email, allowed inventory types and rate. Its unique read-only
   key goes into Windows Credential Manager. Saving the form does not send an email.
5. Choose Publish & email to publish a complete validated feed and explicitly email
   its key, URL and secret-free PDF integration guide. Rates can subsequently be
   changed in Change Rates; saving immediately republishes from original cached prices.
6. Rotate keys, revoke access and change inventory scope through API Access. Changes
   remain visibly pending until hosting confirms them. History distinguishes API
   publication, failures and Gmail acceptance; it never claims a website imported data.

## Detailed workbook issues

Notifications include the inventory type, file, worksheet and exact validation
problem. Row and source column are shown for numeric/formula/type errors; missing
headers, duplicate Stock IDs, missing worksheets, locked files and invalid XLSX files
have specific actionable messages. Different issues notify immediately; identical
issues are suppressed for 60 seconds. Click a notification or a History row to view
full details. Arbitrary library exception messages and secrets are not displayed.

## API contract and security

The public endpoint is `GET /api/v1/inventory`, with `x-api-key` authentication and
`{"diamonds":[...]}` responses. Fields preserve the existing supplier mappings.
`Diamond Type` identifies NATURAL / LAB_GROWN. Stock IDs stay unchanged; identify
records by (Diamond Type, StockId) when both types are enabled. Prices already include
company adjustments; increases are capped at 100%, discounts at 99.99%, and rounded
half-up to two decimal places. Excel files and source prices are not modified.

Prepared company snapshots and authorization records use private storage. Only key
hashes are stored online; client keys, hosting admin credentials and Google tokens
remain in the Windows user vault. No management credential, storage token or legacy
DRC key bundle is included in the installer. Companies cannot use their read-only
keys to publish inventory or manage other companies. Invalid requests and failed
fetches must never be treated by a consumer as empty inventory or a deletion command.

## Development and verification

Use Python 3.12+ and install this project's dev dependencies. Run
`.venv312/Scripts/python.exe -m pytest -q`; inventory, Google and email tests use mocks.
Windows Qt activation tests need permission to create a temporary local named pipe.
Run `node --test inventory-service/test/*.test.js` for the hosted API tests.

Build with `packaging/build.ps1`. The result is `release/ChintamaniGemsSetup-0.7.0.exe`.
The packaged offline check is:

```
ChintamaniGems.exe --portal-self-test --diagnostics-output report.json
```

This creates temporary data, uses no real credentials and sends zero requests.
See [UPDATES.md](UPDATES.md) for signed release publishing and [VALIDATION.md](VALIDATION.md)
for checks performed. Source code and current app state remain separate from the
installer; only application files are distributed.
