# Hosting setup for desktop-managed inventory

This is the backend extension for the existing `chintamani-updates` Vercel project.
It does not replace the updater, release signing key, public Blob store or existing
update routes. There is no web admin dashboard.

## One-time owner setup

1. In Vercel Storage create a **private** Blob store for inventory. Connect it to
   the existing project using a separate environment prefix. Keep the existing
   public installer store and `BLOB_STORE_ID` untouched. Add the private store ID as
   `INVENTORY_BLOB_STORE_ID`. Server functions use the project's Vercel-managed OIDC
   token with this explicit store ID; no storage token goes into the desktop app.
2. In Chintamani Gems open API Access > Hosting & Google setup. Keep the hosting
   origin as `https://chintamani-updates.vercel.app`. Click **Generate / show admin
   hash**. The raw management credential is generated and saved locally in Windows
   Credential Manager, bound to this hosting origin. Copy only the displayed hash
   into the Vercel environment variable `INVENTORY_ADMIN_TOKEN_SHA256`.
3. Deploy the `api/v1` and `lib/inventory-*.js` files alongside the existing updater
   service. Keep its existing package dependencies/lockfile; it already uses the
   Vercel Blob SDK. Add `api/v1/*.js: { maxDuration: 60 }` to the existing vercel.json
   functions map. Set both inventory environment variables in Production and any
   separate staging environment, then redeploy.
4. Back in the desktop setup dialog choose **Save & test hosting**. Leave the admin
   credential field blank when the app generated it on this PC. It keeps the saved
   credential. Never paste DRC keys into this field.
5. Create a Google Cloud project/OAuth application, enable Gmail API and create an
   OAuth client of type **Desktop app**. Configure the consent screen for the sender
   account; complete Google's production consent/verification requirements before
   customer delivery. Download the client JSON file and select it through **Connect
   Google account**. Sign in in the system browser. The app requests only identity,
   verified email and Gmail send permission; refresh tokens stay in Windows vault.

Do not send tokens or passwords through chat or commit them. Do not put inventory
in the public installer store. This repository contains no credential values.

## Desktop workflow

- Existing Excel connections are retained. Initial launch does not publish them.
- Click **Publish inventory** for the initial validation/cache. Each selected type
  needs a valid workbook. Later Excel saves publish updates automatically.
- Add companies in API Access. Choose Natural, Lab Grown, or both, and their rate.
  Company creation saves a read-only key in Windows Credential Manager and does
  not email it. **Publish & email** explicitly validates current workbooks, confirms
  access online and sends the invitation. Gmail acceptance is not inbox delivery.
- Rates in Change Rates are per company. Saving immediately republishes from the
  last validated, unadjusted cache; it does not edit Excel or double-apply rates.
- Keys rotate only when a complete new feed is committed online. Revocation and
  scope changes are confirmed online; offline access changes show as pending.
- Failed/ambiguous emails never retry automatically. Check Sent mail before resend.
- The old DRC history and queued payloads remain readable. They never dispatch.
  Legacy DRC keys are not used or distributed in the new installer.

## Public pull contract

`GET /api/v1/inventory`, header `x-api-key: <company key>`, returns
`{"diamonds":[...]}`. Prices are already adjusted. A response is a full snapshot.
Use `(Diamond Type, StockId)` as identity when both inventory types are enabled.
Headers include publication time, snapshot ID and record count. Do not clear stock
on an HTTP error, incomplete body or invalid JSON. Imported/rejected counts still
require a separate partner reporting contract; this release does not invent one.

## Security / persistence

Company configuration and recipient emails stay on the admin PC. Online records
contain opaque company IDs, key hashes, revisions and private snapshot references.
Current access records are origin-read, never cached. Conditional writes prevent
older jobs from overwriting newer revisions. Uploaded snapshots are immutable,
verified against SHA-256, size, type scope, IDs and record count before activation.
Direct private presigned uploads support JSON snapshots up to 64 MiB. Streaming
pull responses avoid the buffered function response-size limit. A signed upload
URL is short-lived and scoped to one object; it is never emailed or logged.

The full company key lives only in Windows vault and the explicit invitation. An
admin management credential grants control and is never embedded in the installer.
Only the intended admin PC should have it. Back up the local application database;
Windows vault secrets cannot simply be restored onto a different Windows account.

## Offline checks

Run `node --test inventory-service/test/*.test.js` from the app workspace.
Run `python -m pytest` in the Windows app environment. All inventory, storage,
Google and email network operations in tests use mocks. The packaged app supports
`--portal-self-test --diagnostics-output <file.json>`; it uses temporary local data,
no real keys, no browser login and zero network requests.

Production activation is intentionally blocked until private storage and the admin
hash are configured. The existing public update/download service remains separate.
