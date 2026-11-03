# Version 0.7.0 - owner setup before activation

The desktop feature and API extension are implemented. The private inventory store
and Google OAuth client have **not** been configured yet. The current production
app and stable installer download have not been replaced.

1. Create/connect a private inventory Blob store on Vercel, separate from releases.
   Set `INVENTORY_BLOB_STORE_ID` without changing the public updater store variables.
2. In API Access > Hosting & Google setup, generate the local admin credential and
   copy its displayed hash to `INVENTORY_ADMIN_TOKEN_SHA256` on Vercel, then redeploy
   and choose Save & test hosting. Do not copy the actual credential into chat.
3. Enable Gmail API in Google Cloud, create a Desktop OAuth client, complete consent
   setup, and choose its downloaded JSON in Connect Google account.
4. Validate/publish the initial inventory. Add a company, configure prices/access,
   and use Publish & email only for the intended recipient.

See `inventory-service/SETUP.md` for the full procedure. Installing this version
stops legacy direct DRC uploads. Until hosting is ready, API publication remains
pending and the app reports the missing setup. No old job will be sent to DRC.

## Published release — 2026-10-27

At the owner's explicit request, version 0.7.0 was published to the stable channel
using the existing signed release publisher. This supersedes the earlier local-only
release status above. Release notes disclose the setup requirement and the switch
to pull-only publishing. At publication, inventory hosting returned HTTP 503 and
Google sender credentials were not configured. No production inventory or email
was sent, and the installed application was not replaced by the publishing step.
